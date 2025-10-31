"""Graphical interface for the YouTube downloader workflow.

This module recreates the logic from the legacy ``yt_downloader.bat`` script
but provides a Tkinter-based user interface so that end users can paste a
YouTube URL and kick off the process with a single button.

The application keeps the smart dynamic bitrate calculation from the batch
file, assumes a constant 320 kbps AAC audio track and the ``slow`` x264 preset,
and streams progress information into a log window.  The heavy lifting is still
done by the command line utilities ``yt-dlp``, ``ffprobe`` and ``ffmpeg`` –
this script simply orchestrates them in Python instead of a batch file.
"""

from __future__ import annotations

import io
import os
import sys
import datetime as _dt
import json
import queue
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk

try:  # Optional dependency – thumbnails require Pillow for JPEG support.
    from PIL import Image, ImageTk  # type: ignore

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow is optional at runtime.
    PIL_AVAILABLE = False


# ----- Worker implementation -------------------------------------------------

class DownloadWorker(threading.Thread):
    """Thread that runs the download/transcode workflow."""

    def __init__(
        self,
        *,
        task_id: str,
        url: str,
        root: Path,
        title: Optional[str],
        separate_folder: bool,
        event_queue: "queue.Queue[dict[str, object]]",
    ) -> None:
        super().__init__(daemon=True)
        self.task_id = task_id
        self.url = url.strip()
        self.root = root
        self.title = title
        self.separate_folder = separate_folder
        self.event_queue = event_queue
        self.error: Optional[str] = None

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - mirrors batch workflow for clarity
        workdir: Optional[Path] = None
        tempdir: Optional[Path] = None
        final_destination: Optional[Path] = None
        try:
            if not self.url:
                raise ValueError("Порожній URL.")

            self._log("[CONFIG] ROOT=%s" % self.root)
            self.root.mkdir(parents=True, exist_ok=True)

            timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            workdir = self.root / f"DL_{timestamp}_{self.task_id.replace('-', '_')}"
            tempdir = workdir / "temp"
            tempdir.mkdir(parents=True, exist_ok=True)
            self._log(f"[INFO] Робоча папка: {workdir}")
            self._status("завантажується")

            meta = self._ensure_metadata()
            title = meta.get("title") or "video"
            sanitized_title = _sanitize_filename(title)
            self._log(f"[INFO] Назва: {title}")
            self._emit("title", title=title)

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
                final_destination = final_path
            else:
                vbit = self._compute_vbit(src)
                self._log(f"[3/4] Цільовий відео-бітрейт: {vbit}")

                self._status("конвертується")
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
                final_destination = final_path

            if final_destination is None:
                raise RuntimeError("Не вдалося створити фінальний файл")

            if not self.separate_folder:
                all_videos = self.root / "All_Videos"
                all_videos.mkdir(parents=True, exist_ok=True)
                target_path = _unique_path(all_videos / final_destination.name)
                shutil.move(str(final_destination), target_path)
                final_destination = target_path

            self._status("готово")
            self._emit("done", path=str(final_destination))
            self._log(f"[DONE] {final_destination}")
        except Exception as exc:  # pylint: disable=broad-except
            self.error = str(exc)
            self._status("помилка")
            self._emit("error", message=self.error)
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
            if workdir is not None and not self.separate_folder:
                shutil.rmtree(workdir, ignore_errors=True)

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
        self._emit("log", message=message)

    def _status(self, status: str) -> None:
        self._emit("status", status=status)

    def _emit(self, event_type: str, **payload: object) -> None:
        data: dict[str, object] = {"task_id": self.task_id, "type": event_type}
        data.update(payload)
        self.event_queue.put(data)


# ----- Helpers ----------------------------------------------------------------


def _sanitize_filename(title: str) -> str:
    invalid = set('<>:"/\\|?*')
    cleaned = ["_" if ch in invalid or ord(ch) < 32 else ch for ch in title]
    sanitized = "".join(cleaned).strip().rstrip(". ")
    if not sanitized:
        sanitized = "video"
    return sanitized


def _unique_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    counter = 1
    while True:
        new_candidate = parent / f"{stem}_{counter}{suffix}"
        if not new_candidate.exists():
            return new_candidate
        counter += 1


# ----- Tkinter user interface ------------------------------------------------


class TaskRow(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        task_id: str,
        title: str,
        open_callback: Callable[[Path], None],
    ) -> None:
        super().__init__(master)
        self.task_id = task_id
        self.display_title = title
        self.status_var = tk.StringVar(value="Статус: очікує")
        self.title_label = ttk.Label(self, text=title, style="TaskTitle.TLabel")
        self.status_label = ttk.Label(self, textvariable=self.status_var)
        self.open_button = ttk.Button(
            self,
            text="Відкрити папку",
            command=self._open_folder,
            state="disabled",
        )
        self._open_callback = open_callback
        self.final_path: Optional[Path] = None

        self.title_label.grid(row=0, column=0, sticky="w")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.open_button.grid(row=0, column=1, rowspan=2, padx=(12, 0))
        self.grid_columnconfigure(0, weight=1)

    def update_status(self, status: str) -> None:
        self.status_var.set(f"Статус: {status}")
        if status == "готово" and self.final_path is not None:
            self.open_button.state(["!disabled"])
        else:
            self.open_button.state(["disabled"])

    def set_final_path(self, path: Path) -> None:
        self.final_path = path
        if self.status_var.get().endswith("готово"):
            self.open_button.state(["!disabled"])

    def set_title(self, title: str) -> None:
        self.display_title = title
        self.title_label.configure(text=title)

    def _open_folder(self) -> None:
        if self.final_path is None:
            return
        self._open_callback(self.final_path)

class DownloaderUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("780x720")
        self.minsize(760, 680)

        self.style = ttk.Style(self)
        try:
            theme = "vista" if sys.platform.startswith("win") else "clam"
            self.style.theme_use(theme)
        except tk.TclError:
            pass
        self.style.configure("TaskTitle.TLabel", font=("Segoe UI", 10, "bold"))
        self.option_add("*Font", ("Segoe UI", 10))

        self.event_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
        self.workers: dict[str, DownloadWorker] = {}
        self.tasks: dict[str, TaskRow] = {}
        self.task_counter = 1
        self.preview_job: Optional[str] = None
        self.preview_fetch_in_progress = False
        self.preview_info: dict[str, str] = {}
        self.thumbnail_image: Optional["ImageTk.PhotoImage"] = None

        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(1, weight=1)

        ttk.Label(container, text="YouTube URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(container, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        self.url_var.trace_add("write", self._on_url_change)

        ttk.Label(container, text="Root folder").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.root_var = tk.StringVar(value="D:/YouTube")
        self.root_entry = ttk.Entry(container, textvariable=self.root_var)
        self.root_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(12, 0))
        ttk.Button(container, text="Вибрати", command=self._browse_root).grid(
            row=1, column=2, sticky="e", pady=(12, 0)
        )

        self.separate_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            container, text="В окрему папку", variable=self.separate_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.start_button = ttk.Button(container, text="Старт", command=self._start_worker)
        self.start_button.grid(row=2, column=2, sticky="e", pady=(12, 0))

        preview_frame = ttk.LabelFrame(container, text="Попередній перегляд")
        preview_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(18, 12))
        preview_frame.columnconfigure(0, weight=1)

        self.preview_title_var = tk.StringVar(value="Назва: —")
        ttk.Label(
            preview_frame,
            textvariable=self.preview_title_var,
            justify="left",
            wraplength=560,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))

        if PIL_AVAILABLE:
            self.preview_image_label = tk.Label(preview_frame, background="#202020")
        else:
            self.preview_image_label = tk.Label(
                preview_frame,
                text="Щоб бачити обкладинку, встановіть пакет Pillow",
            )
        self.preview_image_label.grid(row=1, column=0, padx=12, pady=6, sticky="nsew")

        self.preview_status_var = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self.preview_status_var).grid(
            row=2, column=0, sticky="w", padx=12, pady=(0, 12)
        )

        tasks_frame = ttk.LabelFrame(container, text="Черга завантажень")
        tasks_frame.grid(row=4, column=0, columnspan=3, sticky="nsew")
        tasks_frame.columnconfigure(0, weight=1)
        tasks_frame.rowconfigure(0, weight=1)
        container.grid_rowconfigure(4, weight=1)

        self.tasks_canvas = tk.Canvas(tasks_frame, highlightthickness=0, height=200)
        self.tasks_canvas.grid(row=0, column=0, sticky="nsew")
        self.tasks_scroll = ttk.Scrollbar(
            tasks_frame, orient="vertical", command=self.tasks_canvas.yview
        )
        self.tasks_scroll.grid(row=0, column=1, sticky="ns")
        self.tasks_canvas.configure(yscrollcommand=self.tasks_scroll.set)
        self.tasks_inner = ttk.Frame(self.tasks_canvas)
        self.tasks_inner.columnconfigure(0, weight=1)
        self.tasks_canvas.create_window((0, 0), window=self.tasks_inner, anchor="nw")
        self.tasks_inner.bind(
            "<Configure>",
            lambda _: self.tasks_canvas.configure(
                scrollregion=self.tasks_canvas.bbox("all")
            ),
        )

        log_frame = ttk.LabelFrame(container, text="Журнал")
        log_frame.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        container.grid_rowconfigure(5, weight=1)

        self.log_widget = scrolledtext.ScrolledText(
            log_frame,
            state="disabled",
            font=("Consolas", 10),
        )
        self.log_widget.pack(fill="both", expand=True, padx=6, pady=8)

        self._register_clipboard_shortcuts()

        self.after(200, self._poll_queue)

    def _browse_root(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.root_var.get() or str(Path.home()))
        if directory:
            self.root_var.set(directory)

    def _start_worker(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL", "Вставте посилання на відео.")
            return

        try:
            root_path = Path(self.root_var.get()).expanduser().resolve()
        except Exception:  # pylint: disable=broad-except
            messagebox.showerror("Папка", "Неможливо використати цю теку.")
            return

        task_id = f"task-{self.task_counter}"
        self.task_counter += 1

        display_title = self.preview_info.get("title") or url
        task_row = TaskRow(
            self.tasks_inner,
            task_id=task_id,
            title=display_title,
            open_callback=self._open_result_folder,
        )
        row_index = len(self.tasks)
        task_row.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
        self.tasks[task_id] = task_row

        worker = DownloadWorker(
            task_id=task_id,
            url=url,
            root=root_path,
            title=self.preview_info.get("title"),
            separate_folder=self.separate_var.get(),
            event_queue=self.event_queue,
        )
        self.workers[task_id] = worker
        worker.start()

        task_row.update_status("завантажується")
        self._append_log(f"[{task_row.display_title}] Запущено процес")

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
                event = self.event_queue.get_nowait()
                task_id = str(event.get("task_id", ""))
                task_row = self.tasks.get(task_id)
                if not task_row:
                    continue
                event_type = event.get("type")
                if event_type == "log":
                    message = str(event.get("message", ""))
                    self._append_log(f"[{task_row.display_title}] {message}")
                elif event_type == "status":
                    status = str(event.get("status", ""))
                    if status:
                        task_row.update_status(status)
                elif event_type == "title":
                    title = str(event.get("title", task_row.display_title))
                    task_row.set_title(title)
                elif event_type == "done":
                    path_value = event.get("path")
                    if path_value:
                        final_path = Path(str(path_value))
                        task_row.set_final_path(final_path)
                        task_row.update_status("готово")
                elif event_type == "error":
                    message = str(event.get("message", ""))
                    if message:
                        messagebox.showerror("Помилка", message)
        except queue.Empty:
            pass

        finished = [tid for tid, worker in self.workers.items() if not worker.is_alive()]
        for tid in finished:
            self.workers.pop(tid, None)

        self.after(200, self._poll_queue)

    def _append_log(self, message: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _register_clipboard_shortcuts(self) -> None:
        self.bind_all("<Control-KeyPress>", self._on_ctrl_keypress, add="+")

    def _on_ctrl_keypress(self, event: tk.Event) -> str | None:  # type: ignore[override]
        widget = event.widget
        if not isinstance(widget, (tk.Entry, tk.Text, scrolledtext.ScrolledText)):
            return None
        keysym = getattr(event, "keysym", "")
        if isinstance(keysym, str) and keysym.lower() in {"v", "c", "x", "a"}:
            return None

        mapping = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}
        sequence = mapping.get(event.keycode)
        if sequence:
            widget.event_generate(sequence)
            return "break"
        return None

    def _open_result_folder(self, path: Path) -> None:
        folder = path.parent
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(folder)], close_fds=True)
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Папка", f"Не вдалося відкрити теку: {exc}")


def main() -> None:
    app = DownloaderUI()
    app.mainloop()


if __name__ == "__main__":
    main()
