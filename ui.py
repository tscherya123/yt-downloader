"""Graphical interface for the YouTube downloader workflow.

This module recreates the logic from ``yt_downloader.bat`` but provides a
Tkinter-based user interface so that end users can paste a YouTube URL and
kick off the process with a single button.

The UI exposes the configurable parameters that existed in the batch file
(``ROOT``, ``MODE``, ``FIXED_VBIT``, ``AUDIO_BIT`` and ``X264_PRESET``) and
streams progress information into a log window.  The heavy lifting is still
done by the command line utilities ``yt-dlp``, ``ffprobe`` and ``ffmpeg`` –
this script simply orchestrates them in Python instead of a batch file.
"""

from __future__ import annotations

import datetime as _dt
import queue
import subprocess
import threading
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext


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
        mode: str,
        fixed_vbit: str,
        audio_bit: str,
        x264_preset: str,
        log_queue: "queue.Queue[str]",
    ) -> None:
        super().__init__(daemon=True)
        self.url = url.strip()
        self.root = root
        self.mode = mode.upper()
        self.fixed_vbit = fixed_vbit
        self.audio_bit = audio_bit
        self.x264_preset = x264_preset
        self.log_queue = log_queue
        self.error: Optional[str] = None

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - mirrors batch workflow for clarity
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

            if video_codec.lower() == "h264" and audio_codec.lower() == "aac":
                self._log(
                    "[INFO] Уже H.264+AAC. Перейменовую у video.mp4 без перекодування."
                )
                dest = src.with_name("video.mp4")
                src.rename(dest)
                self._log(f"[DONE] {dest}")
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
                    self.x264_preset,
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
                    self.audio_bit,
                    "-movflags",
                    "+faststart",
                    "video.mp4",
                ],
                cwd=workdir,
            )

            self._log(f"[DONE] {workdir / 'video.mp4'}")
        except Exception as exc:  # pylint: disable=broad-except
            self.error = str(exc)
            self._log(f"[ERROR] {self.error}")

    def _compute_vbit(self, src: Path) -> str:
        if self.mode != "DYNAMIC":
            return self.fixed_vbit

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


# ----- Tkinter user interface ------------------------------------------------


class DownloaderUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("640x480")

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[DownloadWorker] = None

        # URL input
        tk.Label(self, text="YouTube URL").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.url_var = tk.StringVar()
        tk.Entry(self, textvariable=self.url_var, width=60).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=5, pady=5
        )

        # Root directory selection
        tk.Label(self, text="Root folder").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.root_var = tk.StringVar(value=str(Path.home()))
        tk.Entry(self, textvariable=self.root_var, width=40).grid(
            row=1, column=1, sticky="we", padx=5, pady=5
        )
        tk.Button(self, text="Browse", command=self._browse_root).grid(
            row=1, column=2, sticky="w", padx=5, pady=5
        )

        # Mode radio buttons
        tk.Label(self, text="Mode").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.mode_var = tk.StringVar(value="DYNAMIC")
        tk.Radiobutton(self, text="Dynamic", variable=self.mode_var, value="DYNAMIC").grid(
            row=2, column=1, sticky="w", padx=5
        )
        tk.Radiobutton(self, text="Fixed", variable=self.mode_var, value="FIXED").grid(
            row=2, column=2, sticky="w", padx=5
        )

        # Bitrate and preset configuration
        tk.Label(self, text="Fixed video bitrate (e.g. 50M)").grid(
            row=3, column=0, sticky="w", padx=5, pady=5
        )
        self.fixed_vbit_var = tk.StringVar(value="50M")
        tk.Entry(self, textvariable=self.fixed_vbit_var).grid(
            row=3, column=1, sticky="we", padx=5, pady=5
        )

        tk.Label(self, text="Audio bitrate").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.audio_bit_var = tk.StringVar(value="320k")
        tk.Entry(self, textvariable=self.audio_bit_var).grid(
            row=4, column=1, sticky="we", padx=5, pady=5
        )

        tk.Label(self, text="x264 preset").grid(row=5, column=0, sticky="w", padx=5, pady=5)
        self.x264_preset_var = tk.StringVar(value="slow")
        tk.Entry(self, textvariable=self.x264_preset_var).grid(
            row=5, column=1, sticky="we", padx=5, pady=5
        )

        # Start button
        tk.Button(self, text="Start", command=self._start_worker).grid(
            row=6, column=0, columnspan=3, pady=10
        )

        # Log output
        self.log_widget = scrolledtext.ScrolledText(self, state="disabled")
        self.log_widget.grid(row=7, column=0, columnspan=4, sticky="nsew", padx=5, pady=5)

        # Configure resizing behaviour
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(7, weight=1)

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
            mode=self.mode_var.get(),
            fixed_vbit=self.fixed_vbit_var.get(),
            audio_bit=self.audio_bit_var.get(),
            x264_preset=self.x264_preset_var.get(),
            log_queue=self.log_queue,
        )
        self.worker.start()

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
