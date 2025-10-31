"""Настільний застосунок для завантаження відео з YouTube.

Головні компоненти – інтерфейс Tkinter та робітники, що керують ``yt-dlp``,
``ffprobe`` і ``ffmpeg``. Програма пропонує попередній перегляд метаданих,
декілька паралельних завантажень і автоматичне перекодування в H.264 із
пресетом ``slow``.
"""

from __future__ import annotations

import io
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

try:  # Необов'язкова залежність – для обкладинок у форматі JPEG потрібен Pillow.
    from PIL import Image, ImageTk  # type: ignore

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow необов'язковий під час виконання.
    PIL_AVAILABLE = False


# ----- Реалізація робітника -------------------------------------------------

class DownloadWorker(threading.Thread):
    """Потік, що виконує повний цикл завантаження й перекодування."""

    def __init__(
        self,
        *,
        task_id: str,
        url: str,
        root: Path,
        title: Optional[str],
        separate_folder: bool,
        start_seconds: float,
        end_seconds: Optional[float],
        event_queue: "queue.Queue[dict[str, object]]",
    ) -> None:
        super().__init__(daemon=True)
        self.task_id = task_id
        self.url = url.strip()
        self.root = root
        self.title = title
        self.separate_folder = separate_folder
        self.start_seconds = max(start_seconds, 0.0)
        self.end_seconds = end_seconds
        self.event_queue = event_queue
        self.error: Optional[str] = None

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - відтворюємо логіку батника для прозорості
        workdir: Optional[Path] = None
        tempdir: Optional[Path] = None
        final_destination: Optional[Path] = None
        try:
            if not self.url:
                raise ValueError("Порожній URL.")

            if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
                raise ValueError("Кінцева позначка має бути більшою за початкову.")

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

            if self.start_seconds > 0 or self.end_seconds is not None:
                human_start = _format_timestamp(self.start_seconds)
                if self.end_seconds is not None:
                    human_end = _format_timestamp(self.end_seconds)
                else:
                    human_end = "кінець"
                self._log(f"[INFO] Відрізок: {human_start} – {human_end}")

            # Крок 1: завантаження через yt-dlp
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

            template_placeholder = workdir / "source.%(ext)s"
            template_placeholder.unlink(missing_ok=True)

            src = next(workdir.glob("source.*"), None)
            if not src:
                raise RuntimeError("Не знайдено source.*")

            video_codec, audio_codec = self._probe_codecs(src)
            self._log(f"[2/4] Відео: {video_codec}   Аудіо: {audio_codec}")

            final_path = workdir / f"{sanitized_title}.mp4"
            clip_requested = self.start_seconds > 0 or self.end_seconds is not None
            needs_transcode = not (
                video_codec.lower() == "h264" and audio_codec.lower() == "aac"
            )

            if not needs_transcode and not clip_requested:
                self._log(
                    "[INFO] Уже H.264+AAC. Перейменовую без перекодування."
                )
                src.rename(final_path)
                final_destination = final_path
            else:
                self._status("конвертується")
                ffmpeg_args = ["ffmpeg", "-hide_banner", "-stats", "-y"]
                if clip_requested:
                    ffmpeg_args.extend(["-ss", _format_timestamp(self.start_seconds)])
                ffmpeg_args.extend(["-i", str(src)])
                if clip_requested and self.end_seconds is not None:
                    segment = max(self.end_seconds - self.start_seconds, 0.0)
                    ffmpeg_args.extend(["-t", _format_timestamp(segment)])

                if needs_transcode:
                    vbit = self._compute_vbit(src)
                    self._log(f"[3/4] Цільовий відео-бітрейт: {vbit}")
                    self._log("[4/4] Перекодування у MP4...")
                    ffmpeg_args.extend(
                        [
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
                        ]
                    )
                else:
                    self._log("[INFO] Копіюю доріжки без повторного кодування.")
                    ffmpeg_args.extend(
                        [
                            "-c:v",
                            "copy",
                            "-c:a",
                            "copy",
                            "-movflags",
                            "+faststart",
                            final_path.name,
                        ]
                    )

                self._run(ffmpeg_args, cwd=workdir)
                final_destination = final_path

            if final_destination is None:
                raise RuntimeError("Не вдалося створити фінальний файл")

            if not self.separate_folder:
                library = self.root / "YT_DOWNLOADER_FILES"
                library.mkdir(parents=True, exist_ok=True)
                target_path = _unique_path(library / final_destination.name)
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
        # Обчислюємо тривалість (у секундах) і розмір файлу (у байтах), щоб оцінити бітрейт
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

        # Повторюємо арифметику зі старого батника
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
        process = subprocess.run(  # noqa: S603 - свідоме виконання зовнішньої команди
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


# ----- Допоміжні функції -----------------------------------------------------


def _sanitize_filename(title: str) -> str:
    invalid = set('<>:"/\\|?*')
    cleaned = ["_" if ch in invalid or ord(ch) < 32 else ch for ch in title]
    sanitized = "".join(cleaned).strip().rstrip(". ")
    if not sanitized:
        sanitized = "video"
    return sanitized


def _format_timestamp(value: float) -> str:
    total_ms = int(round(max(value, 0.0) * 1000))
    seconds, milliseconds = divmod(total_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}".rstrip("0.")
    if milliseconds:
        return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}".rstrip("0.")
    return f"{minutes:02d}:{seconds:02d}"


def _shorten_title(title: str, limit: int = 40) -> str:
    if len(title) <= limit:
        return title
    cutoff = max(limit - 3, 1)
    return title[:cutoff] + "..."


def _parse_time_input(text: str) -> Optional[float]:
    cleaned = text.strip()
    if not cleaned:
        return None
    parts = cleaned.split(":")
    if len(parts) > 3:
        raise ValueError("Неправильний формат часу")
    total = 0.0
    multiplier = 1.0
    for component in reversed(parts):
        if not component:
            raise ValueError("Неправильний формат часу")
        try:
            value = float(component)
        except ValueError as exc:
            raise ValueError("Неправильний формат часу") from exc
        total += value * multiplier
        multiplier *= 60
    return total


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


# ----- Графічний інтерфейс Tkinter -----------------------------------------


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
        self.full_title = title
        self.display_title = _shorten_title(title)
        self.status_var = tk.StringVar(value="Статус: очікує")
        self.title_label = ttk.Label(
            self,
            text=self.display_title,
            style="TaskTitle.TLabel",
            width=40,
        )
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
        self.full_title = title
        self.display_title = _shorten_title(title)
        self.title_label.configure(text=self.display_title)

    def _open_folder(self) -> None:
        if self.final_path is None:
            return
        self._open_callback(self.final_path)

class DownloaderUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("1200x760")
        self.minsize(1040, 700)

        self.style = ttk.Style(self)
        try:
            theme = "vista" if sys.platform.startswith("win") else "clam"
            self.style.theme_use(theme)
        except tk.TclError:
            pass
        self.style.configure("TaskTitle.TLabel", font=("Segoe UI", 10, "bold"))
        self.option_add("*Font", ("Segoe UI", 10))

        self.settings_path = Path(__file__).with_name("settings.json")
        self.settings: dict[str, object] = self._load_settings()
        default_root = str((Path.home() / "Downloads").resolve())
        saved_root = self.settings.get("root_folder")
        self.initial_root = saved_root if isinstance(saved_root, str) else default_root

        self.event_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
        self.workers: dict[str, DownloadWorker] = {}
        self.tasks: dict[str, TaskRow] = {}
        self.task_counter = 1
        self.preview_fetch_in_progress = False
        self.preview_token = 0
        self.preview_info: dict[str, str] = {}
        self.duration_seconds: Optional[float] = None
        self.thumbnail_image: Optional["ImageTk.PhotoImage"] = None

        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=3)
        container.grid_columnconfigure(1, weight=2)
        container.grid_rowconfigure(0, weight=1)

        left_frame = ttk.Frame(container)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_frame.grid_columnconfigure(1, weight=1)
        left_frame.grid_rowconfigure(4, weight=1)

        ttk.Label(left_frame, text="YouTube URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(left_frame, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.url_var.trace_add("write", self._on_url_change)
        self.search_button = ttk.Button(
            left_frame, text="Знайти відео", command=self._fetch_preview
        )
        self.search_button.grid(row=0, column=2, sticky="e")

        ttk.Label(left_frame, text="Коренева тека").grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        self.root_var = tk.StringVar(value=self.initial_root)
        self.root_entry = ttk.Entry(left_frame, textvariable=self.root_var)
        self.root_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(12, 0))
        ttk.Button(left_frame, text="Вибрати", command=self._browse_root).grid(
            row=1, column=2, sticky="e", pady=(12, 0)
        )

        self.separate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            left_frame, text="В окрему папку", variable=self.separate_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.download_button = ttk.Button(
            left_frame,
            text="Скачати",
            command=self._start_worker,
            state="disabled",
        )
        self.download_button.grid(row=2, column=2, sticky="e", pady=(12, 0))

        preview_frame = ttk.LabelFrame(left_frame, text="Попередній перегляд")
        preview_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(18, 12))
        preview_frame.columnconfigure(0, weight=1)

        self.preview_title_var = tk.StringVar(value="Назва: —")
        ttk.Label(
            preview_frame,
            textvariable=self.preview_title_var,
            justify="left",
            wraplength=560,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))

        self.preview_duration_var = tk.StringVar(value="Тривалість: —")
        ttk.Label(
            preview_frame,
            textvariable=self.preview_duration_var,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=12)

        if PIL_AVAILABLE:
            self.preview_image_label = tk.Label(preview_frame, background="#202020")
        else:
            self.preview_image_label = tk.Label(
                preview_frame,
                text="Щоб бачити обкладинку, встановіть пакет Pillow",
            )
        self.preview_image_label.grid(row=2, column=0, padx=12, pady=6, sticky="nsew")

        clip_frame = ttk.Frame(preview_frame)
        clip_frame.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 6))
        ttk.Label(clip_frame, text="Початок").grid(row=0, column=0, sticky="w")
        self.start_time_var = tk.StringVar(value="00:00")
        self.start_entry = ttk.Entry(
            clip_frame, textvariable=self.start_time_var, width=10, state="disabled"
        )
        self.start_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(clip_frame, text="Кінець").grid(row=0, column=2, sticky="w")
        self.end_time_var = tk.StringVar(value="00:00")
        self.end_entry = ttk.Entry(
            clip_frame, textvariable=self.end_time_var, width=10, state="disabled"
        )
        self.end_entry.grid(row=0, column=3, sticky="w", padx=(6, 0))

        self.preview_status_var = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self.preview_status_var).grid(
            row=4, column=0, sticky="w", padx=12, pady=(0, 12)
        )

        queue_frame = ttk.LabelFrame(container, text="Черга завантажень")
        queue_frame.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(1, weight=1)

        header_frame = ttk.Frame(queue_frame)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 4))
        header_frame.columnconfigure(0, weight=1)
        self.clear_history_button = ttk.Button(
            header_frame,
            text="Очистити історію",
            command=self._confirm_clear_history,
            state="disabled",
        )
        self.clear_history_button.grid(row=0, column=1, sticky="e")

        self.tasks_canvas = tk.Canvas(queue_frame, highlightthickness=0)
        self.tasks_canvas.grid(row=1, column=0, sticky="nsew")
        self.tasks_scroll = ttk.Scrollbar(
            queue_frame, orient="vertical", command=self.tasks_canvas.yview
        )
        self.tasks_scroll.grid(row=1, column=1, sticky="ns")
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

        log_frame = ttk.LabelFrame(left_frame, text="Журнал")
        log_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(6, 0))

        self.log_widget = scrolledtext.ScrolledText(
            log_frame,
            state="disabled",
            font=("Consolas", 10),
        )
        self.log_widget.pack(fill="both", expand=True, padx=6, pady=8)

        self._register_clipboard_shortcuts()

        self.after(200, self._poll_queue)
        self.after(300, self._ensure_root_folder)
        self._update_clear_history_state()

    def _browse_root(self) -> None:
        directory = filedialog.askdirectory(
            initialdir=self.root_var.get() or self.initial_root,
            title="Оберіть теку для завантажень",
        )
        if directory:
            self.root_var.set(directory)
            self._store_root(directory)

    def _start_worker(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL", "Вставте посилання на відео.")
            return

        if self.duration_seconds is None:
            messagebox.showwarning(
                "Метадані",
                "Спочатку натисніть «Знайти відео» та дочекайтесь попереднього перегляду.",
            )
            return

        try:
            start_seconds = _parse_time_input(self.start_time_var.get()) or 0.0
            end_seconds_value = _parse_time_input(self.end_time_var.get())
        except ValueError:
            messagebox.showerror(
                "Час",
                "Некоректний формат часу. Використовуйте формат гг:хх:сс.",
            )
            return

        duration = self.duration_seconds
        end_seconds = end_seconds_value if end_seconds_value is not None else duration

        if start_seconds < 0 or start_seconds >= duration:
            messagebox.showerror(
                "Час",
                "Початковий час має бути в межах тривалості відео.",
            )
            return

        if end_seconds <= start_seconds or end_seconds > duration + 1e-3:
            messagebox.showerror(
                "Час",
                "Кінцевий час має бути більшим за початковий і не перевищувати тривалість відео.",
            )
            return

        try:
            root_path = Path(self.root_var.get()).expanduser().resolve()
        except Exception:  # pylint: disable=broad-except
            messagebox.showerror("Папка", "Неможливо використати цю теку.")
            return

        self._store_root(str(root_path))

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
        self._update_clear_history_state()

        clip_end_for_worker: Optional[float]
        if abs(end_seconds - duration) <= 1e-3:
            clip_end_for_worker = None
        else:
            clip_end_for_worker = end_seconds

        worker = DownloadWorker(
            task_id=task_id,
            url=url,
            root=root_path,
            title=self.preview_info.get("title"),
            separate_folder=self.separate_var.get(),
            start_seconds=start_seconds,
            end_seconds=clip_end_for_worker,
            event_queue=self.event_queue,
        )
        self.workers[task_id] = worker
        worker.start()

        task_row.update_status("завантажується")
        self._append_log(f"[{task_row.full_title}] Запущено процес")

    def _on_url_change(self, *_: object) -> None:
        self.preview_token += 1
        url = self.url_var.get().strip()
        if not url:
            self._clear_preview()
            return

        self.preview_info = {}
        self.duration_seconds = None
        self.preview_title_var.set("Назва: —")
        self.preview_duration_var.set("Тривалість: —")
        self.preview_status_var.set("")
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)

    def _fetch_preview(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL", "Вставте посилання на відео.")
            return
        if self.preview_fetch_in_progress:
            return

        self.preview_fetch_in_progress = True
        self.preview_token += 1
        token = self.preview_token
        self.preview_status_var.set("Завантаження даних…")
        self.search_button.state(["disabled"])
        self.download_button.state(["disabled"])
        self.preview_title_var.set("Назва: —")
        self.preview_duration_var.set("Тривалість: —")
        self._set_clip_controls_enabled(False)
        if PIL_AVAILABLE:
            self.preview_image_label.configure(image="", text="")
        else:
            self.preview_image_label.configure(
                text="Щоб бачити обкладинку, встановіть пакет Pillow",
                image="",
            )
        self.thumbnail_image = None

        def worker() -> None:
            try:
                output = subprocess.run(  # noqa: S603 - виклик зовнішньої утиліти
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
                duration_value: Optional[float] = None
                raw_duration = data.get("duration")
                if isinstance(raw_duration, (int, float)):
                    duration_value = float(raw_duration)
                else:
                    duration_text = data.get("duration_string")
                    if isinstance(duration_text, str):
                        try:
                            parsed = _parse_time_input(duration_text)
                        except ValueError:
                            parsed = None
                        if parsed is not None:
                            duration_value = parsed

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
                self.after(
                    0,
                    lambda: self._apply_preview(
                        token, title, thumbnail_url, image, duration_value
                    ),
                )
            except Exception as exc:  # pylint: disable=broad-except
                self.after(0, lambda: self._preview_error(token, str(exc)))
            finally:
                self.after(0, lambda: self._preview_fetch_done(token))

        threading.Thread(target=worker, daemon=True).start()

    def _preview_fetch_done(self, _: int) -> None:
        self.preview_fetch_in_progress = False
        self.search_button.state(["!disabled"])
        try:
            self.search_button.configure(state=tk.NORMAL)
        except tk.TclError:
            pass

    def _apply_preview(
        self,
        token: int,
        title: str,
        thumbnail_url: Optional[str],
        image: Optional["ImageTk.PhotoImage"],
        duration: Optional[float],
    ) -> None:
        if token != self.preview_token:
            return
        self.preview_info = {"title": title, "thumbnail": thumbnail_url or ""}
        self.preview_title_var.set(f"Назва: {title}")

        if duration is not None:
            self.duration_seconds = duration
            formatted_duration = _format_timestamp(duration)
            self.preview_duration_var.set(f"Тривалість: {formatted_duration}")
            self.start_time_var.set("00:00")
            self.end_time_var.set(formatted_duration)
            self._set_clip_controls_enabled(True)
            self.download_button.state(["!disabled"])
            self.preview_status_var.set("Готово")
        else:
            self.duration_seconds = None
            self.preview_duration_var.set("Тривалість: —")
            self.start_time_var.set("00:00")
            self.end_time_var.set("")
            self._set_clip_controls_enabled(False)
            self.download_button.state(["disabled"])
            self.preview_status_var.set("Не вдалося визначити тривалість")

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

    def _preview_error(self, token: int, message: str) -> None:
        if token != self.preview_token:
            return
        self.preview_info = {}
        self.duration_seconds = None
        self.preview_title_var.set("Назва: —")
        self.preview_duration_var.set("Тривалість: —")
        self.preview_image_label.configure(text="Не вдалося завантажити прев’ю", image="")
        self.thumbnail_image = None
        self.preview_status_var.set(message)
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)
        self.search_button.state(["!disabled"])
        try:
            self.search_button.configure(state=tk.NORMAL)
        except tk.TclError:
            pass
        messagebox.showwarning(
            "URL",
            "Не вдалося отримати дані за цим посиланням. Перевірте, що це сторінка відео.",
        )

    def _clear_preview(self) -> None:
        self.preview_info = {}
        self.duration_seconds = None
        self.preview_title_var.set("Назва: —")
        self.preview_duration_var.set("Тривалість: —")
        if PIL_AVAILABLE:
            self.preview_image_label.configure(text="", image="")
        else:
            self.preview_image_label.configure(
                text="Щоб бачити обкладинку, встановіть пакет Pillow",
                image="",
            )
        self.thumbnail_image = None
        self.preview_status_var.set("")
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)

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
                    self._append_log(f"[{task_row.full_title}] {message}")
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

    def _confirm_clear_history(self) -> None:
        if any(worker.is_alive() for worker in self.workers.values()):
            messagebox.showwarning(
                "Історія",
                "Спочатку дочекайтеся завершення всіх завантажень.",
            )
            return

        if not self.tasks:
            messagebox.showinfo("Історія", "Історія вже порожня.")
            return

        proceed = messagebox.askyesno(
            "Очистити історію",
            "Видалити всі записи зі списку? Файли залишаться на диску.",
            icon="warning",
        )
        if not proceed:
            return

        for row in list(self.tasks.values()):
            row.destroy()
        self.tasks.clear()
        bbox = self.tasks_canvas.bbox("all")
        if bbox:
            self.tasks_canvas.configure(scrollregion=bbox)
        else:
            self.tasks_canvas.configure(scrollregion=(0, 0, 0, 0))
        self.tasks_canvas.yview_moveto(0)
        self._update_clear_history_state()

    def _update_clear_history_state(self) -> None:
        if not hasattr(self, "clear_history_button"):
            return
        if self.tasks:
            self.clear_history_button.state(["!disabled"])
        else:
            self.clear_history_button.state(["disabled"])

    def _set_clip_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)

    def _load_settings(self) -> dict[str, object]:
        if not self.settings_path.exists():
            return {}
        try:
            with self.settings_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:  # pylint: disable=broad-except
            return {}
        return {}

    def _save_settings(self) -> None:
        try:
            with self.settings_path.open("w", encoding="utf-8") as handle:
                json.dump(self.settings, handle, ensure_ascii=False, indent=2)
        except Exception:  # pylint: disable=broad-except
            pass

    def _store_root(self, path: str) -> None:
        self.settings["root_folder"] = path
        self._save_settings()

    def _ensure_root_folder(self) -> None:
        current = self.root_var.get().strip()
        if current and Path(current).exists():
            self._store_root(current)
            return

        fallback = Path(self.initial_root).expanduser()
        if not fallback.exists():
            fallback.parent.mkdir(parents=True, exist_ok=True)

        selected = filedialog.askdirectory(
            title="Оберіть теку для завантажень",
            initialdir=str(fallback),
        )
        if selected:
            self.root_var.set(selected)
            self._store_root(selected)
        else:
            fallback.mkdir(parents=True, exist_ok=True)
            self.root_var.set(str(fallback))
            self._store_root(str(fallback))

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
        if not path.exists():
            messagebox.showerror(
                "Папка",
                "Файл не знайдено. Переконайтеся, що його не було переміщено або видалено.",
            )
            return
        folder = path.parent
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(folder)], close_fds=True)
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("Папка", f"Не вдалося відкрити теку: {exc}")


def main() -> None:
    app = DownloaderUI()
    app.mainloop()


if __name__ == "__main__":
    main()
