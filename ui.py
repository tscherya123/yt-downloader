"""Graphical interface for the YouTube downloader workflow.

This module recreates the logic from ``yt_downloader.bat`` but provides a
Tkinter-based user interface so that end users can paste a YouTube URL and
kick off the process with a single button.

The application keeps the smart dynamic bitrate calculation from the batch
file, assumes a constant 320 kbps AAC audio track and the ``slow`` x264 preset,
and streams progress information into a log window.  The heavy lifting is still
done by the command line utilities ``yt-dlp``, ``ffprobe`` and ``ffmpeg`` –
this script simply orchestrates them in Python instead of a batch file.
"""

from __future__ import annotations

import io
import datetime as _dt
import json
import queue
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

try:  # Optional dependency – thumbnails require Pillow for JPEG support.
    from PIL import Image, ImageTk  # type: ignore

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow is optional at runtime.
    PIL_AVAILABLE = False


# ----- Worker implementation -------------------------------------------------

class DownloadWorker(threading.Thread):
    """Thread that runs the download/transcode workflow.

    Parameters are passed in through a simple dataclass-like container so that
    the Tkinter UI stays responsive while the subprocess calls are running.
    Progress messages are sent to a ``queue.Queue`` which the GUI polls.
    """

    def __init__(
        self,
        *,
        url: str,
        root: Path,
        title: Optional[str],
        log_queue: "queue.Queue[str]",
    ) -> None:
        super().__init__(daemon=True)
        self.url = url.strip()
        self.root = root
        self.title = title
        self.log_queue = log_queue
        self.error: Optional[str] = None

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - mirrors batch workflow for clarity
        workdir: Optional[Path] = None
        tempdir: Optional[Path] = None
        try:
            if not self.url:
                raise ValueError("Порожній URL.")

            self._log("[CONFIG] ROOT=%s" % self.root)
            self.root.mkdir(parents=True, exist_ok=True)

            timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            workdir = self.root / f"DL_{timestamp}"
            tempdir = workdir / "temp"
            tempdir.mkdir(parents=True, exist_ok=True)
            self._log(f"[INFO] Робоча папка: {workdir}")

            meta = self._ensure_metadata()
            title = meta.get("title") or "video"
            sanitized_title = _sanitize_filename(title)
            self._log(f"[INFO] Назва: {title}")

            # Step 1: yt-dlp download
            self._log("[1/4] Завантаження...")
            self._run(
                [
                    "yt-dlp",
                    "-f",
                    "bv*+ba/b",
                    "-S",
                    "res,fps,br",
                    "--hls-prefer-ffmpeg",
                    "-N",
                    "8",
                    "-P",
                    str(workdir),
                    "--paths",
                    f"temp:{tempdir}",
                    "-o",
                    "source.%(ext)s",
                    self.url,
                ],
                cwd=workdir,
            )

            # The batch file cleans up potential template leftovers; emulate that
            template_placeholder = workdir / "source.%(ext)s"
            template_placeholder.unlink(missing_ok=True)

            src = next(workdir.glob("source.*"), None)
            if not src:
                raise RuntimeError("Не знайдено source.*")

            video_codec, audio_codec = self._probe_codecs(src)
            self._log(f"[2/4] Відео: {video_codec}   Аудіо: {audio_codec}")

            final_path = workdir / f"{sanitized_title}.mp4"
            if video_codec.lower() == "h264" and audio_codec.lower() == "aac":
                self._log(
                    "[INFO] Уже H.264+AAC. Перейменовую без перекодування."
                )
                src.rename(final_path)
                self._log(f"[DONE] {final_path}")
                return

            vbit = self._compute_vbit(src)
            self._log(f"[3/4] Цільовий відео-бітрейт: {vbit}")

            self._log("[4/4] Перекодування у MP4...")
            self._run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-stats",
                    "-y",
                    "-i",
                    str(src),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "slow",
                    "-pix_fmt",
                    "yuv420p",
                    "-b:v",
                    vbit,
                    "-minrate",
                    vbit,
                    "-maxrate",
                    vbit,
                    "-bufsize",
                    "100M",
                    "-profile:v",
                    "high",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "320k",
                    "-movflags",
                    "+faststart",
                    final_path.name,
                ],
                cwd=workdir,
            )

            self._log(f"[DONE] {final_path}")
        except Exception as exc:  # pylint: disable=broad-except
            self.error = str(exc)
            self._log(f"[ERROR] {self.error}")
        finally:
            if workdir is not None:
                try:
                    leftover = next(workdir.glob("source.*"), None)
                    if leftover and leftover.exists():
                        leftover.unlink()
                except Exception:
                    pass
            if tempdir is not None:
                shutil.rmtree(tempdir, ignore_errors=True)

    def _compute_vbit(self, src: Path) -> str:
        # Derive duration (seconds) and file size (bytes) to estimate bitrate
        duration = self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
        ).strip()

        try:
            dur_value = max(float(duration), 1.0)
        except ValueError:
            dur_value = 1.0

        file_size = src.stat().st_size
        total = int((file_size * 8) // dur_value)

        # replicate batch arithmetic
        audio = 320_000
        video = max(800_000, total - audio)
        headroom = int(video * 1.15)
        mbit = max((headroom + 999_999) // 1_000_000, 4)
        return f"{mbit}M"

    def _ensure_metadata(self) -> dict[str, str]:
        if self.title:
            return {"title": self.title}

        output = self._run(
            [
                "yt-dlp",
                "--dump-single-json",
                "--skip-download",
                self.url,
            ],
            capture_output=True,
        )
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"title": "video"}

    def _probe_codecs(self, src: Path) -> tuple[str, str]:
        output = self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
        )
        video_codec = output.strip()

        output = self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
        )
        audio_codec = output.strip()

        return video_codec or "unknown", audio_codec or "unknown"

    def _run(
        self,
        args: list[str],
        *,
        cwd: Optional[Path] = None,
        capture_output: bool = False,
    ) -> str:
        process = subprocess.run(  # noqa: S603 - deliberate command execution
            args,
            cwd=cwd,
            check=True,
            capture_output=capture_output,
            text=True,
        )
        if capture_output:
            return process.stdout
        return ""

    def _log(self, message: str) -> None:
        self.log_queue.put(message)


# ----- Helpers ----------------------------------------------------------------


def _sanitize_filename(title: str) -> str:
    invalid = set('<>:"/\\|?*')
    cleaned = ["_" if ch in invalid or ord(ch) < 32 else ch for ch in title]
    sanitized = "".join(cleaned).strip().rstrip(". ")
    if not sanitized:
        sanitized = "video"
    return sanitized


# ----- Tkinter user interface ------------------------------------------------


class DownloaderUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("700x520")

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[DownloadWorker] = None
        self.preview_job: Optional[str] = None
        self.preview_fetch_in_progress = False
        self.preview_info: dict[str, str] = {}
        self.thumbnail_image: Optional["ImageTk.PhotoImage"] = None

        # URL input
        tk.Label(self, text="YouTube URL").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.url_var = tk.StringVar()
        tk.Entry(self, textvariable=self.url_var, width=60).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=5, pady=5
        )
        self.url_var.trace_add("write", self._on_url_change)

        # Root directory selection
        tk.Label(self, text="Root folder").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.root_var = tk.StringVar(value="D:/YouTube")
        tk.Entry(self, textvariable=self.root_var, width=40).grid(
            row=1, column=1, sticky="we", padx=5, pady=5
        )
        tk.Button(self, text="Browse", command=self._browse_root).grid(
            row=1, column=2, sticky="w", padx=5, pady=5
        )

        # Preview frame
        preview_frame = tk.LabelFrame(self, text="Попередній перегляд")
        preview_frame.grid(row=2, column=0, columnspan=4, sticky="we", padx=5, pady=10)

        self.preview_title_var = tk.StringVar(value="Назва: —")
        tk.Label(
            preview_frame,
            textvariable=self.preview_title_var,
            anchor="w",
            justify="left",
            wraplength=500,
        ).pack(fill="x", padx=10, pady=(8, 4))

        if PIL_AVAILABLE:
            self.preview_image_label = tk.Label(preview_frame)
        else:
            self.preview_image_label = tk.Label(
                preview_frame,
                text="Щоб бачити обкладинку, встановіть пакет Pillow",
            )
        self.preview_image_label.pack(padx=10, pady=5)

        self.preview_status_var = tk.StringVar(value="")
        tk.Label(preview_frame, textvariable=self.preview_status_var, fg="#666").pack(
            fill="x", padx=10, pady=(0, 8)
        )

        # Start button
        tk.Button(self, text="Start", command=self._start_worker).grid(
            row=3, column=0, columnspan=3, pady=10
        )

        # Log output
        self.log_widget = scrolledtext.ScrolledText(self, state="disabled")
        self.log_widget.grid(row=4, column=0, columnspan=4, sticky="nsew", padx=5, pady=5)

        # Configure resizing behaviour
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self.after(200, self._poll_queue)

    def _browse_root(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.root_var.get() or str(Path.home()))
        if directory:
            self.root_var.set(directory)

    def _start_worker(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(
                "Already running", "Зачекайте, поки завершиться поточний процес."
            )
            return

        try:
            root_path = Path(self.root_var.get()).expanduser().resolve()
        except Exception:  # pylint: disable=broad-except
            messagebox.showerror("Invalid path", "Неможливо використати цю папку.")
            return

        self._append_log("=== Початок ===")
        self.worker = DownloadWorker(
            url=self.url_var.get(),
            root=root_path,
            title=self.preview_info.get("title"),
            log_queue=self.log_queue,
        )
        self.worker.start()

    def _on_url_change(self, *_: object) -> None:
        url = self.url_var.get().strip()
        if self.preview_job:
            self.after_cancel(self.preview_job)
            self.preview_job = None

        if not url:
            self._clear_preview()
            return

        self.preview_status_var.set("Завантаження даних…")
        self.preview_job = self.after(600, lambda: self._start_preview_fetch(url))

    def _start_preview_fetch(self, url: str) -> None:
        if self.preview_fetch_in_progress:
            return

        self.preview_fetch_in_progress = True

        def worker() -> None:
            try:
                output = subprocess.run(  # noqa: S603
                    [
                        "yt-dlp",
                        "--dump-single-json",
                        "--skip-download",
                        url,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                data = json.loads(output)
                title = data.get("title") or "—"
                thumbnail_url = data.get("thumbnail")
                image = None
                if thumbnail_url and PIL_AVAILABLE:
                    try:
                        with urllib.request.urlopen(thumbnail_url, timeout=10) as response:
                            payload = response.read()
                        pil_image = Image.open(io.BytesIO(payload))
                        pil_image.thumbnail((480, 270))
                        image = ImageTk.PhotoImage(pil_image)
                    except Exception:
                        image = None
                self.after(0, lambda: self._apply_preview(title, thumbnail_url, image))
            except Exception as exc:  # pylint: disable=broad-except
                self.after(0, lambda: self._preview_error(str(exc)))
            finally:
                self.after(0, self._preview_fetch_done)

        threading.Thread(target=worker, daemon=True).start()

    def _preview_fetch_done(self) -> None:
        self.preview_fetch_in_progress = False

    def _apply_preview(
        self,
        title: str,
        thumbnail_url: Optional[str],
        image: Optional["ImageTk.PhotoImage"],
    ) -> None:
        self.preview_info = {"title": title, "thumbnail": thumbnail_url or ""}
        self.preview_title_var.set(f"Назва: {title}")
        if PIL_AVAILABLE and image is not None:
            self.thumbnail_image = image
            self.preview_image_label.configure(image=image, text="")
        elif not PIL_AVAILABLE and thumbnail_url:
            self.preview_image_label.configure(
                text="Обкладинка недоступна без пакета Pillow",
                image="",
            )
            self.thumbnail_image = None
        else:
            self.preview_image_label.configure(text="", image="")
            self.thumbnail_image = None
        self.preview_status_var.set("Готово")

    def _preview_error(self, message: str) -> None:
        self.preview_info = {}
        self.preview_title_var.set("Назва: —")
        self.preview_image_label.configure(text="Не вдалося завантажити прев’ю", image="")
        self.thumbnail_image = None
        self.preview_status_var.set(message)

    def _clear_preview(self) -> None:
        self.preview_info = {}
        self.preview_title_var.set("Назва: —")
        if PIL_AVAILABLE:
            self.preview_image_label.configure(text="", image="")
        else:
            self.preview_image_label.configure(
                text="Щоб бачити обкладинку, встановіть пакет Pillow",
                image="",
            )
        self.thumbnail_image = None
        self.preview_status_var.set("")

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                self._append_log(message)
        except queue.Empty:
            pass

        if self.worker and not self.worker.is_alive():
            if self.worker.error:
                messagebox.showerror("Помилка", self.worker.error)
            else:
                messagebox.showinfo("Готово", "Завершено без помилок.")
            self.worker = None

        self.after(200, self._poll_queue)

    def _append_log(self, message: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")


def main() -> None:
    app = DownloaderUI()
    app.mainloop()


if __name__ == "__main__":
    main()
