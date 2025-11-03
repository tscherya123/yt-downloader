"""Tkinter application entry point for the YouTube downloader."""

from __future__ import annotations

import base64
import io
import sys
import datetime as _dt
import json
import queue
import subprocess
import threading
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk

try:  # Необов'язкова залежність – для обкладинок у форматі JPEG потрібен Pillow.
    from PIL import Image, ImageTk  # type: ignore

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow необов'язковий під час виконання.
    PIL_AVAILABLE = False

from .localization import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    translate,
)
from .themes import DEFAULT_THEME, THEMES
from .utils import (
    format_timestamp as _format_timestamp,
    is_youtube_video_url as _is_youtube_video_url,
    parse_time_input as _parse_time_input,
    subprocess_no_window_kwargs as _subprocess_no_window_kwargs,
    yt_dlp_command as _yt_dlp_command,
)
from .widgets import TaskRow
from .worker import DownloadWorker


class DownloaderUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.config_dir = self._prepare_config_directory()
        self.settings_path = self.config_dir / "settings.json"
        self.queue_state_path = self.config_dir / "download_queue.json"
        self._migrate_legacy_state_files()
        self.settings: dict[str, object] = self._load_settings()
        self.language = self._coerce_language(self.settings.get("language"))
        self.theme = self._coerce_theme(self.settings.get("theme"))

        self.title(self._("app_title"))
        self.geometry("1200x760")
        self.minsize(1040, 700)

        self.style = ttk.Style(self)
        available_themes = set(self.style.theme_names())
        current_theme = self.style.theme_use()
        preferred_light = "vista" if sys.platform.startswith("win") else "clam"
        if preferred_light in available_themes:
            self.light_base_theme = preferred_light
        elif current_theme in available_themes:
            self.light_base_theme = current_theme
        else:
            self.light_base_theme = "default"
        self.dark_base_theme = "clam" if "clam" in available_themes else self.light_base_theme
        try:
            self.style.theme_use(self.light_base_theme)
            self.current_base_theme = self.light_base_theme
        except tk.TclError:
            # Якщо потрібний базовий стиль недоступний, запам'ятовуємо доступний варіант.
            self.current_base_theme = current_theme
            self.light_base_theme = current_theme
            self.dark_base_theme = current_theme
        self.style.configure("TaskTitle.TLabel", font=("Segoe UI", 10, "bold"))
        self.option_add("*Font", ("Segoe UI", 10))

        default_root = str((Path.home() / "Downloads").resolve())
        saved_root = self.settings.get("root_folder")
        self.initial_root = saved_root if isinstance(saved_root, str) else default_root

        self.queue_state = self._load_queue_state()
        self.queue_records: dict[str, dict[str, Any]] = {
            item["task_id"]: item for item in self.queue_state.get("items", [])
        }
        self._save_queue_state()

        self.event_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
        self.workers: dict[str, DownloadWorker] = {}
        self.tasks: dict[str, TaskRow] = {}
        self.task_order: list[str] = []
        self.task_counter = self._compute_next_task_counter()
        self.preview_fetch_in_progress = False
        self.preview_token = 0
        self.preview_info: dict[str, str] = {}
        self.duration_seconds: Optional[float] = None
        self.thumbnail_image: Optional[tk.PhotoImage] = None

        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=3)
        container.grid_columnconfigure(1, weight=2)
        container.grid_rowconfigure(0, weight=0)
        container.grid_rowconfigure(1, weight=1)

        options_frame = ttk.Frame(container)
        options_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        options_frame.grid_columnconfigure(1, weight=1)
        options_frame.grid_columnconfigure(3, weight=1)

        self.language_label = ttk.Label(options_frame, anchor="e")
        self.language_label.grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.language_display_var = tk.StringVar()
        self.language_combo = ttk.Combobox(
            options_frame,
            state="readonly",
            textvariable=self.language_display_var,
            width=18,
        )
        self.language_combo.grid(row=0, column=1, sticky="w")
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_selected)

        self.theme_label = ttk.Label(options_frame, anchor="e")
        self.theme_label.grid(row=0, column=2, sticky="e", padx=(18, 6))
        self.theme_display_var = tk.StringVar()
        self.theme_combo = ttk.Combobox(
            options_frame,
            state="readonly",
            textvariable=self.theme_display_var,
            width=18,
        )
        self.theme_combo.grid(row=0, column=3, sticky="w")
        self.theme_combo.bind("<<ComboboxSelected>>", self._on_theme_selected)

        left_frame = ttk.Frame(container)
        left_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        left_frame.grid_columnconfigure(1, weight=1)
        left_frame.grid_rowconfigure(4, weight=1)

        self.url_label = ttk.Label(left_frame, text=self._("url_label"))
        self.url_label.grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(left_frame, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.url_var.trace_add("write", self._on_url_change)
        self.search_button = ttk.Button(
            left_frame, text=self._("search_button"), command=self._fetch_preview
        )
        self.search_button.grid(row=0, column=2, sticky="e")
        self._update_search_button_state()

        self.root_label = ttk.Label(left_frame, text=self._("root_label"))
        self.root_label.grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.root_var = tk.StringVar(value=self.initial_root)
        self.root_entry = ttk.Entry(left_frame, textvariable=self.root_var)
        self.root_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(12, 0))
        self.browse_button = ttk.Button(
            left_frame, text=self._("choose_button"), command=self._browse_root
        )
        self.browse_button.grid(row=1, column=2, sticky="e", pady=(12, 0))

        self.separate_var = tk.BooleanVar(value=False)
        self.separate_check = ttk.Checkbutton(
            left_frame, text=self._("separate_folder"), variable=self.separate_var
        )
        self.separate_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.download_button = ttk.Button(
            left_frame,
            text=self._("download_button"),
            command=self._start_worker,
            state="disabled",
        )
        self.download_button.grid(row=2, column=2, sticky="e", pady=(12, 0))

        self.preview_frame = ttk.LabelFrame(left_frame, text=self._("preview_group"))
        preview_frame = self.preview_frame
        preview_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(18, 12))
        preview_frame.columnconfigure(0, weight=1)

        self.preview_title_var = tk.StringVar()
        self.preview_title_label = ttk.Label(
            preview_frame,
            textvariable=self.preview_title_var,
            justify="left",
            wraplength=560,
        )
        self.preview_title_label.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))

        self.preview_duration_var = tk.StringVar()
        self.preview_duration_label = ttk.Label(
            preview_frame,
            textvariable=self.preview_duration_var,
            justify="left",
        )
        self.preview_duration_label.grid(row=1, column=0, sticky="w", padx=12)

        self.preview_image_label = tk.Label(preview_frame)
        self.preview_image_label.grid(row=2, column=0, padx=12, pady=6, sticky="nsew")
        self.preview_image_text_key = None

        clip_frame = ttk.Frame(preview_frame)
        clip_frame.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 6))
        self.clip_start_label = ttk.Label(clip_frame, text=self._("clip_start_label"))
        self.clip_start_label.grid(row=0, column=0, sticky="w")
        self.start_time_var = tk.StringVar(value="00:00")
        self.start_entry = ttk.Entry(
            clip_frame, textvariable=self.start_time_var, width=10, state="disabled"
        )
        self.start_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        self.clip_end_label = ttk.Label(clip_frame, text=self._("clip_end_label"))
        self.clip_end_label.grid(row=0, column=2, sticky="w")
        self.end_time_var = tk.StringVar(value="00:00")
        self.end_entry = ttk.Entry(
            clip_frame, textvariable=self.end_time_var, width=10, state="disabled"
        )
        self.end_entry.grid(row=0, column=3, sticky="w", padx=(6, 0))

        self.preview_status_var = tk.StringVar(value="")
        self.preview_status_label = ttk.Label(preview_frame, textvariable=self.preview_status_var)
        self.preview_status_label.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 12))

        self.queue_frame = ttk.LabelFrame(container, text=self._("queue_group"))
        queue_frame = self.queue_frame
        queue_frame.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(2, weight=1)

        header_frame = ttk.Frame(queue_frame)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 4))
        header_frame.columnconfigure(0, weight=1)
        self.clear_history_button = ttk.Button(
            header_frame,
            text=self._("clear_history"),
            command=self._confirm_clear_history,
            state="disabled",
        )
        self.clear_history_button.grid(row=0, column=1, sticky="e")

        self.queue_columns_frame = ttk.Frame(queue_frame, style="TaskHeader.TFrame")
        columns_frame = self.queue_columns_frame
        columns_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 4))
        columns_frame.columnconfigure(0, weight=3)
        columns_frame.columnconfigure(1, weight=2)
        columns_frame.columnconfigure(2, weight=0)
        self.queue_title_header = ttk.Label(
            columns_frame,
            text=self._("queue_column_title"),
            style="TaskHeader.TLabel",
            anchor="w",
        )
        self.queue_status_header = ttk.Label(
            columns_frame,
            text=self._("queue_column_status"),
            style="TaskHeader.TLabel",
            anchor="w",
        )
        self.queue_actions_header = ttk.Label(
            columns_frame,
            text=self._("queue_column_actions"),
            style="TaskHeader.TLabel",
            anchor="e",
        )
        self.queue_title_header.grid(row=0, column=0, sticky="w")
        self.queue_status_header.grid(row=0, column=1, sticky="w")
        self.queue_actions_header.grid(row=0, column=2, sticky="e")

        self.tasks_canvas = tk.Canvas(queue_frame, highlightthickness=0)
        self.tasks_canvas.grid(row=2, column=0, sticky="nsew")
        self.tasks_scroll = ttk.Scrollbar(
            queue_frame, orient="vertical", command=self.tasks_canvas.yview
        )
        self.tasks_scroll.grid(row=2, column=1, sticky="ns")
        self.tasks_canvas.configure(yscrollcommand=self.tasks_scroll.set)
        self.tasks_inner = ttk.Frame(self.tasks_canvas, style="TaskContainer.TFrame")
        self.tasks_inner.columnconfigure(0, weight=1)
        self.tasks_window = self.tasks_canvas.create_window(
            (0, 0), window=self.tasks_inner, anchor="nw"
        )
        self.tasks_inner.bind(
            "<Configure>",
            lambda _: self.tasks_canvas.configure(
                scrollregion=self.tasks_canvas.bbox("all")
            ),
        )
        self.tasks_canvas.bind(
            "<Configure>",
            lambda event: self.tasks_canvas.itemconfigure(
                self.tasks_window, width=event.width
            ),
        )

        self._restore_queue_from_history()

        self.log_frame = ttk.LabelFrame(left_frame, text=self._("log_group"))
        log_frame = self.log_frame
        log_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(6, 0))

        self.log_widget = scrolledtext.ScrolledText(
            log_frame,
            state="disabled",
            font=("Consolas", 10),
        )
        self.log_widget.pack(fill="both", expand=True, padx=6, pady=8)

        self.preview_title_value: Optional[str] = None
        self.preview_duration_value: Optional[str] = None
        self.preview_status_key = "idle"

        self._register_clipboard_shortcuts()

        self.after(200, self._poll_queue)
        self.after(300, self._ensure_root_folder)
        self._update_clear_history_state()
        self._apply_language()
        self._apply_theme()
    def _set_preview_title(self, title: Optional[str]) -> None:
        self.preview_title_value = title
        display = title if title else "—"
        self.preview_title_var.set(self._("preview_title", title=display))

    def _set_preview_duration(self, duration: Optional[str]) -> None:
        self.preview_duration_value = duration
        display = duration if duration else "—"
        self.preview_duration_var.set(self._("preview_duration", duration=display))

    def _set_preview_status(self, key: str) -> None:
        self.preview_status_key = key
        if key == "idle":
            self.preview_status_var.set("")
            return
        self.preview_status_var.set(self._(f"preview_status_{key}"))

    def _set_preview_image_text(self, key: Optional[str]) -> None:
        self.preview_image_text_key = key
        if key is None:
            self.preview_image_label.configure(text="")
        else:
            self.preview_image_label.configure(text=self._(key), image="")

    def _browse_root(self) -> None:
        directory = filedialog.askdirectory(
            initialdir=self.root_var.get() or self.initial_root,
            title=self._("dialog_choose_folder"),
        )
        if directory:
            self.root_var.set(directory)
            self._store_root(directory)

    def _start_worker(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(
                self._("warning_url_title"), self._("warning_url_body")
            )
            return

        if self.duration_seconds is None:
            messagebox.showwarning(
                self._("warning_metadata_title"),
                self._("warning_metadata_body"),
            )
            return

        try:
            start_seconds = _parse_time_input(self.start_time_var.get()) or 0.0
            end_seconds_value = _parse_time_input(self.end_time_var.get())
        except ValueError:
            messagebox.showerror(
                self._("error_time_title"),
                self._("error_time_format"),
            )
            return

        duration = self.duration_seconds
        end_seconds = end_seconds_value if end_seconds_value is not None else duration

        if start_seconds < 0 or start_seconds >= duration:
            messagebox.showerror(
                self._("error_time_title"),
                self._("error_time_start_range"),
            )
            return

        if end_seconds <= start_seconds or end_seconds > duration + 1e-3:
            messagebox.showerror(
                self._("error_time_title"),
                self._("error_time_end_range"),
            )
            return

        try:
            root_path = Path(self.root_var.get()).expanduser().resolve()
        except Exception:  # pylint: disable=broad-except
            messagebox.showerror(
                self._("error_root_title"), self._("error_root_body")
            )
            return

        self._store_root(str(root_path))

        task_id = f"task-{self.task_counter}"
        self.task_counter += 1
        self.queue_state["next_id"] = max(self.task_counter, 1)

        display_title = self.preview_info.get("title") or url
        task_row = TaskRow(
            self.tasks_inner,
            task_id=task_id,
            title=display_title,
            open_callback=self._open_result_folder,
            translator=self._,
            cancel_callback=self._cancel_task,
            remove_callback=self._remove_history_entry,
            open_url_callback=self._open_source_url,
            retry_callback=self._retry_task,
            source_url=url,
            status="downloading",
        )
        task_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.tasks = {task_id: task_row, **self.tasks}
        self.task_order.insert(0, task_id)

        record: dict[str, Any] = {
            "task_id": task_id,
            "title": display_title,
            "status": "downloading",
            "path": None,
            "created_at": _dt.datetime.now().isoformat(),
            "url": url,
        }
        self.queue_state.setdefault("items", []).insert(0, record)
        self.queue_records[task_id] = record
        self._save_queue_state()
        self._reflow_task_rows()
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
            language=self.language,
        )
        self.workers[task_id] = worker
        worker.start()

        self._append_log(f"[{task_row.full_title}] {self._('log_process_started')}")

    def _on_url_change(self, *_: object) -> None:
        self.preview_token += 1
        url = self.url_var.get().strip()
        if not url:
            self._clear_preview()
            return

        self.preview_info = {}
        self.duration_seconds = None
        self._set_preview_title(None)
        self._set_preview_duration(None)
        self._set_preview_status("idle")
        self.preview_image_label.configure(image="", text="")
        self.preview_image_text_key = None
        self.thumbnail_image = None
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)
        self._update_search_button_state()

    def _update_search_button_state(self) -> None:
        if not hasattr(self, "search_button"):
            return
        should_enable = (
            not self.preview_fetch_in_progress
            and _is_youtube_video_url(self.url_var.get().strip())
        )
        if should_enable:
            self.search_button.state(["!disabled"])
            try:
                self.search_button.configure(state=tk.NORMAL)
            except tk.TclError:
                pass
        else:
            self.search_button.state(["disabled"])
            try:
                self.search_button.configure(state=tk.DISABLED)
            except tk.TclError:
                pass

    def _fetch_preview(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(
                self._("warning_url_title"), self._("warning_url_body")
            )
            return
        if self.preview_fetch_in_progress:
            return
        if not _is_youtube_video_url(url):
            self._update_search_button_state()
            return

        self.preview_fetch_in_progress = True
        self._update_search_button_state()
        self.preview_token += 1
        token = self.preview_token
        self._set_preview_status("loading")
        self.download_button.state(["disabled"])
        self._set_preview_title(None)
        self._set_preview_duration(None)
        self._set_clip_controls_enabled(False)
        self.preview_image_label.configure(image="", text="")
        self.preview_image_text_key = None
        self.thumbnail_image = None

        def worker() -> None:
            try:
                cmd = _yt_dlp_command(
                    "--dump-single-json",
                    "--skip-download",
                    url,
                    prefer_gui=False,
                )
                output = subprocess.run(  # noqa: S603 - виклик зовнішньої утиліти
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    **_subprocess_no_window_kwargs(),
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
                if thumbnail_url:
                    try:
                        with urllib.request.urlopen(thumbnail_url, timeout=10) as response:
                            payload = response.read()
                        image = self._prepare_thumbnail_image(payload)
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
        self._update_search_button_state()

    def _apply_preview(
        self,
        token: int,
        title: str,
        thumbnail_url: Optional[str],
        image: Optional[tk.PhotoImage],
        duration: Optional[float],
    ) -> None:
        if token != self.preview_token:
            return
        self.preview_info = {"title": title, "thumbnail": thumbnail_url or ""}
        self._set_preview_title(title)

        if duration is not None:
            self.duration_seconds = duration
            formatted_duration = _format_timestamp(duration)
            self._set_preview_duration(formatted_duration)
            self.start_time_var.set("00:00")
            self.end_time_var.set(formatted_duration)
            self._set_clip_controls_enabled(True)
            self.download_button.state(["!disabled"])
            self._set_preview_status("ready")
        else:
            self.duration_seconds = None
            self._set_preview_duration(None)
            self.start_time_var.set("00:00")
            self.end_time_var.set("")
            self._set_clip_controls_enabled(False)
            self.download_button.state(["disabled"])
            self._set_preview_status("no_duration")

        if image is not None:
            self.thumbnail_image = image
            self.preview_image_label.configure(image=image, text="")
            self.preview_image_text_key = None
        else:
            if thumbnail_url:
                self._set_preview_image_text("preview_thumbnail_unavailable")
            else:
                self.preview_image_label.configure(text="", image="")
                self.preview_image_text_key = None
            self.thumbnail_image = None

    def _prepare_thumbnail_image(self, payload: bytes) -> Optional[tk.PhotoImage]:
        """Create a Tk-compatible thumbnail image from raw bytes."""

        if PIL_AVAILABLE:
            try:
                pil_image = Image.open(io.BytesIO(payload))
                pil_image.thumbnail((480, 270))
                return ImageTk.PhotoImage(pil_image)
            except Exception:
                pass
        return self._photoimage_from_bytes(payload)

    def _photoimage_from_bytes(self, payload: bytes) -> Optional[tk.PhotoImage]:
        detected_format = self._detect_image_format(payload)
        encoded = base64.b64encode(payload).decode("ascii")
        if detected_format:
            try:
                return tk.PhotoImage(data=encoded, format=detected_format)
            except (tk.TclError, RuntimeError, ValueError):
                pass
        try:
            return tk.PhotoImage(data=encoded)
        except (tk.TclError, RuntimeError, ValueError):
            pass

        png_payload = self._transcode_image_to_png(payload)
        if not png_payload:
            return None

        try:
            encoded_png = base64.b64encode(png_payload).decode("ascii")
            return tk.PhotoImage(data=encoded_png, format="png")
        except (tk.TclError, RuntimeError, ValueError):
            try:
                return tk.PhotoImage(data=encoded_png)
            except (tk.TclError, RuntimeError, ValueError):
                return None

    def _detect_image_format(self, payload: bytes) -> Optional[str]:
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if payload.startswith(b"\xff\xd8\xff"):
            return "jpeg"
        if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
            return "gif"
        return None

    def _transcode_image_to_png(self, payload: bytes) -> Optional[bytes]:
        try:
            result = subprocess.run(  # noqa: S603 - свідоме виконання зовнішньої команди
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-i",
                    "pipe:0",
                    "-vf",
                    "scale=if(gt(iw,480),480,iw):-1",
                    "-f",
                    "png",
                    "pipe:1",
                ],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                **_subprocess_no_window_kwargs(),
            )
        except (FileNotFoundError, subprocess.CalledProcessError, OSError):
            return None
        return result.stdout or None

    def _preview_error(self, token: int, message: str) -> None:
        if token != self.preview_token:
            return
        self.preview_info = {}
        self.duration_seconds = None
        self._set_preview_title(None)
        self._set_preview_duration(None)
        self._set_preview_image_text("preview_thumbnail_failed")
        self.thumbnail_image = None
        self._set_preview_status("error")
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)
        self._update_search_button_state()
        messagebox.showwarning(
            self._("warning_url_title"), self._("preview_status_error")
        )

    def _clear_preview(self) -> None:
        self.preview_info = {}
        self.duration_seconds = None
        self._set_preview_title(None)
        self._set_preview_duration(None)
        self.preview_image_label.configure(text="", image="")
        self.preview_image_text_key = None
        self.thumbnail_image = None
        self._set_preview_status("idle")
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)
        self._update_search_button_state()

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
                        update_payload: dict[str, Any] = {"status": status}
                        if status == "cancelled":
                            update_payload["path"] = None
                            update_payload["error"] = None
                        self._update_queue_record(task_id, **update_payload)
                elif event_type == "title":
                    title = str(event.get("title", task_row.display_title))
                    task_row.set_title(title)
                    self._update_queue_record(task_id, title=title)
                elif event_type == "done":
                    path_value = event.get("path")
                    if path_value:
                        final_path = Path(str(path_value))
                        task_row.set_final_path(final_path)
                        task_row.update_status("done")
                        self._update_queue_record(
                            task_id,
                            status="done",
                            path=str(final_path),
                            title=task_row.full_title,
                            error=None,
                        )
                elif event_type == "error":
                    message = str(event.get("message", ""))
                    if message:
                        messagebox.showerror(self._("error_generic_title"), message)
                    self._update_queue_record(
                        task_id,
                        status="error",
                        error=message or None,
                        path=None,
                    )
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
                self._("history_title"), self._("warning_history_running")
            )
            return

        if not self.tasks:
            messagebox.showinfo(self._("history_title"), self._("info_history_empty"))
            return

        proceed = messagebox.askyesno(
            self._("confirm_clear_history_title"),
            self._("confirm_clear_history_prompt"),
            icon="warning",
        )
        if not proceed:
            return

        for row in list(self.tasks.values()):
            row.destroy()
        self.tasks.clear()
        self.task_order.clear()
        self.queue_records.clear()
        self.queue_state["items"] = []
        self._save_queue_state()
        self._reflow_task_rows()
        self.tasks_canvas.yview_moveto(0)
        self._update_clear_history_state()

    def _update_clear_history_state(self) -> None:
        if not hasattr(self, "clear_history_button"):
            return
        if self.tasks:
            self.clear_history_button.state(["!disabled"])
        else:
            self.clear_history_button.state(["disabled"])

    def _cancel_task(self, task_id: str) -> None:
        worker = self.workers.get(task_id)
        if not worker:
            return
        worker.cancel()
        row = self.tasks.get(task_id)
        if row:
            row.mark_cancelling()
        self._update_queue_record(task_id, status="cancelled", path=None, error=None)

    def _retry_task(self, _: str, url: str) -> None:
        cleaned = url.strip()
        if not cleaned:
            return
        self.url_var.set(cleaned)
        self._update_search_button_state()
        try:
            self.url_entry.focus_set()
        except tk.TclError:
            pass

        def trigger_fetch() -> None:
            if self.preview_fetch_in_progress:
                self.after(150, trigger_fetch)
                return
            if _is_youtube_video_url(self.url_var.get().strip()):
                self._fetch_preview()

        self.after_idle(trigger_fetch)

    def _remove_history_entry(self, task_id: str) -> None:
        row = self.tasks.get(task_id)
        if row is None:
            return
        if row.status_code in {"downloading", "converting"}:
            return
        worker = self.workers.get(task_id)
        if worker and worker.is_alive():
            return
        row.destroy()
        self.tasks.pop(task_id, None)
        try:
            self.task_order.remove(task_id)
        except ValueError:
            pass
        self._remove_queue_record(task_id)
        self._reflow_task_rows()
        self._update_clear_history_state()

    def _reflow_task_rows(self) -> None:
        cleaned_order: list[str] = []
        for index, task_id in enumerate(self.task_order):
            row = self.tasks.get(task_id)
            if not row:
                continue
            row.grid_configure(row=index)
            cleaned_order.append(task_id)
        if len(cleaned_order) != len(self.task_order):
            self.task_order = cleaned_order
        self.tasks_inner.update_idletasks()
        bbox = self.tasks_canvas.bbox("all")
        if bbox:
            self.tasks_canvas.configure(scrollregion=bbox)
        else:
            self.tasks_canvas.configure(scrollregion=(0, 0, 0, 0))

    def _restore_queue_from_history(self) -> None:
        for record in self.queue_state.get("items", []):
            task_id = str(record.get("task_id", ""))
            if not task_id:
                continue
            title = str(record.get("title") or task_id)
            status = str(record.get("status") or "done")
            path_value = record.get("path")
            final_path = None
            if isinstance(path_value, str) and path_value:
                final_path = Path(path_value)
            url_value = record.get("url")
            source_url = None
            if isinstance(url_value, str):
                stripped = url_value.strip()
                if stripped:
                    source_url = stripped
            task_row = TaskRow(
                self.tasks_inner,
                task_id=task_id,
                title=title,
                open_callback=self._open_result_folder,
                translator=self._,
                cancel_callback=self._cancel_task,
                remove_callback=self._remove_history_entry,
                open_url_callback=self._open_source_url,
                retry_callback=self._retry_task,
                source_url=source_url,
                status=status,
                final_path=final_path,
            )
            task_row.grid(row=len(self.tasks), column=0, sticky="ew", pady=(0, 8))
            self.tasks[task_id] = task_row
            self.task_order.append(task_id)
        self._reflow_task_rows()
        self._update_clear_history_state()

    def _update_queue_record(self, task_id: str, **changes: Any) -> None:
        record = self.queue_records.get(task_id)
        if not record:
            return
        for key, value in changes.items():
            if key == "path":
                record[key] = str(value) if value else None
            elif key == "error":
                if value:
                    record[key] = str(value)
                else:
                    record.pop(key, None)
            elif key == "url":
                record[key] = str(value) if value else None
            elif key == "title":
                record[key] = str(value)
            elif key == "status":
                record[key] = str(value)
            else:
                record[key] = value
        row = self.tasks.get(task_id)
        if row and "url" in changes:
            row.set_source_url(changes["url"])
        self._save_queue_state()

    def _remove_queue_record(self, task_id: str) -> None:
        self.queue_records.pop(task_id, None)
        self.queue_state["items"] = [
            item for item in self.queue_state.get("items", []) if item.get("task_id") != task_id
        ]
        self._save_queue_state()

    def _save_queue_state(self) -> None:
        stored_next = self.queue_state.get("next_id")
        next_id = stored_next if isinstance(stored_next, int) and stored_next > 0 else 1
        state = {"next_id": next_id, "items": self.queue_state.get("items", [])}
        try:
            with self.queue_state_path.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_queue_state(self) -> dict[str, Any]:
        default: dict[str, Any] = {"next_id": 1, "items": []}
        if not self.queue_state_path.exists():
            return default
        try:
            with self.queue_state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return default
        if not isinstance(data, dict):
            return default
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        allowed_statuses = {"waiting", "downloading", "converting", "done", "error", "cancelled"}
        for raw in data.get("items", []):
            if not isinstance(raw, dict):
                continue
            task_id = str(raw.get("task_id", "")).strip()
            if not task_id or task_id in seen_ids:
                continue
            seen_ids.add(task_id)
            title = str(raw.get("title") or task_id)
            status = str(raw.get("status") or "done")
            if status not in allowed_statuses:
                status = "cancelled" if status in {"downloading", "converting"} else "done"
            if status in {"downloading", "converting"}:
                status = "cancelled"
            path_value = raw.get("path")
            path = str(path_value).strip() if isinstance(path_value, str) else ""
            url_value = raw.get("url")
            url = str(url_value).strip() if isinstance(url_value, str) else ""
            entry: dict[str, Any] = {
                "task_id": task_id,
                "title": title,
                "status": status,
                "path": path or None,
                "url": url or None,
                "created_at": str(raw.get("created_at"))
                if isinstance(raw.get("created_at"), str)
                and raw.get("created_at")
                else _dt.datetime.now().isoformat(),
            }
            error_value = raw.get("error")
            if isinstance(error_value, str) and error_value:
                entry["error"] = error_value
            items.append(entry)
        next_id = data.get("next_id")
        if not isinstance(next_id, int) or next_id < 1:
            next_id = 1
        return {"next_id": next_id, "items": items}

    def _compute_next_task_counter(self) -> int:
        max_id = 0
        for task_id in self.queue_records:
            try:
                suffix = int(str(task_id).split("-")[-1])
            except ValueError:
                continue
            max_id = max(max_id, suffix)
        stored_next = self.queue_state.get("next_id")
        if isinstance(stored_next, int) and stored_next > 0:
            max_id = max(max_id, stored_next - 1)
        return max(max_id + 1, 1)

    def _set_clip_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)

    def _on_language_selected(self, _: tk.Event) -> None:  # pragma: no cover - UI callback
        selection = self.language_display_var.get()
        code = getattr(self, "_language_name_map", {}).get(selection)
        if not code:
            current_name = getattr(self, "_language_display_by_code", {}).get(
                self.language
            )
            if current_name:
                self.language_display_var.set(current_name)
            return
        if code == self.language:
            return
        self.language = code
        self._store_language(code)
        self._apply_language()

    def _on_theme_selected(self, _: tk.Event) -> None:  # pragma: no cover - UI callback
        selection = self.theme_display_var.get()
        code = getattr(self, "_theme_name_map", {}).get(selection)
        if not code:
            current_name = getattr(self, "_theme_display_by_code", {}).get(self.theme)
            if current_name:
                self.theme_display_var.set(current_name)
            return
        if code == self.theme:
            return
        self.theme = code
        self._store_theme(code)
        self._apply_theme()

    def _apply_language(self) -> None:
        language_names = {code: translate(self.language, f"language_{code}") for code in SUPPORTED_LANGUAGES}
        self._language_display_by_code = language_names
        self._language_name_map = {name: code for code, name in language_names.items()}
        self.language_combo.configure(values=list(language_names.values()))
        current_language_name = language_names.get(self.language, language_names[DEFAULT_LANGUAGE])
        self.language_display_var.set(current_language_name)

        theme_names = {key: self._(f"theme_{key}") for key in THEMES}
        self._theme_display_by_code = theme_names
        self._theme_name_map = {name: key for key, name in theme_names.items()}
        self.theme_combo.configure(values=list(theme_names.values()))
        current_theme_name = theme_names.get(self.theme, theme_names[DEFAULT_THEME])
        self.theme_display_var.set(current_theme_name)

        self.title(self._("app_title"))
        self.language_label.configure(text=self._("language_label"))
        self.theme_label.configure(text=self._("theme_label"))
        self.url_label.configure(text=self._("url_label"))
        self.search_button.configure(text=self._("search_button"))
        self.root_label.configure(text=self._("root_label"))
        self.browse_button.configure(text=self._("choose_button"))
        self.separate_check.configure(text=self._("separate_folder"))
        self.download_button.configure(text=self._("download_button"))
        self.preview_frame.configure(text=self._("preview_group"))
        self.queue_frame.configure(text=self._("queue_group"))
        self.log_frame.configure(text=self._("log_group"))
        self.clip_start_label.configure(text=self._("clip_start_label"))
        self.clip_end_label.configure(text=self._("clip_end_label"))
        self.clear_history_button.configure(text=self._("clear_history"))
        if hasattr(self, "queue_title_header"):
            self.queue_title_header.configure(text=self._("queue_column_title"))
            self.queue_status_header.configure(text=self._("queue_column_status"))
            self.queue_actions_header.configure(text=self._("queue_column_actions"))

        self._set_preview_title(self.preview_title_value)
        self._set_preview_duration(self.preview_duration_value)
        self._set_preview_status(self.preview_status_key)
        if self.preview_image_text_key is not None:
            self._set_preview_image_text(self.preview_image_text_key)

        for row in self.tasks.values():
            row.retranslate(self._)

    def _apply_theme(self) -> None:
        colors = THEMES.get(self.theme, THEMES[DEFAULT_THEME])
        self.configure(bg=colors["background"])

        desired_base_theme = (
            self.light_base_theme if self.theme == "light" else self.dark_base_theme
        )
        if desired_base_theme != self.current_base_theme:
            try:
                self.style.theme_use(desired_base_theme)
                self.current_base_theme = desired_base_theme
            except tk.TclError:
                # Якщо теми немає, залишаємось на попередній.
                pass

        self.style.configure("TFrame", background=colors["frame"])
        self.style.configure("TLabelframe", background=colors["frame"], foreground=colors["text"])
        self.style.configure("TLabelframe.Label", background=colors["frame"], foreground=colors["text"])
        self.style.configure("TLabel", background=colors["frame"], foreground=colors["text"])
        self.style.configure(
            "TaskTitle.TLabel",
            background=colors["frame"],
            foreground=colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "TaskRow.TFrame",
            background=colors["frame"],
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "TaskActions.TFrame",
            background=colors["frame"],
            borderwidth=0,
            relief="flat",
        )
        self.style.configure(
            "TaskHeader.TFrame",
            background=colors["frame"],
        )
        self.style.configure(
            "TaskHeader.TLabel",
            background=colors["frame"],
            foreground=colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "TaskStatus.TLabel",
            background=colors["frame"],
            foreground=colors["muted"],
        )
        self.style.configure(
            "TaskContainer.TFrame",
            background=colors["canvas_bg"],
        )
        self.style.configure(
            "TButton",
            background=colors["button_bg"],
            foreground=colors["button_fg"],
        )
        self.style.map(
            "TButton",
            background=[("active", colors["button_active_bg"]), ("disabled", colors["button_bg"])],
            foreground=[("disabled", colors["disabled_fg"])]
        )
        self.style.configure(
            "TCheckbutton",
            background=colors["frame"],
            foreground=colors["text"],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=colors["entry_bg"],
            background=colors["frame"],
            foreground=colors["text"],
            arrowcolor=colors["text"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["entry_bg"])],
            foreground=[
                ("readonly", colors["text"]),
                ("disabled", colors["disabled_fg"]),
            ],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=colors["entry_bg"],
            foreground=colors["text"],
            insertcolor=colors["text"],
        )
        self.style.map(
            "TEntry",
            fieldbackground=[
                ("readonly", colors["entry_bg"]),
                ("disabled", colors["frame"]),
            ],
            foreground=[("disabled", colors["disabled_fg"])],
        )
        dropdown_bg = colors["dropdown_bg"]
        dropdown_fg = colors["dropdown_fg"]
        dropdown_select_bg = colors["dropdown_select_bg"]
        dropdown_select_fg = colors["dropdown_select_fg"]
        listbox_colors = {
            "background": dropdown_bg,
            "foreground": dropdown_fg,
            "selectBackground": dropdown_select_bg,
            "selectForeground": dropdown_select_fg,
            "highlightColor": dropdown_bg,
            "highlightBackground": dropdown_bg,
            "borderColor": dropdown_bg,
            "activeBackground": dropdown_select_bg,
            "activeForeground": dropdown_select_fg,
        }
        for option, value in listbox_colors.items():
            self.option_add(f"*TCombobox*Listbox.{option}", value)
            self.option_add(f"*TCombobox*Listbox*{option}", value)
        self.option_add("*TCombobox*Foreground", dropdown_fg)

        scrollbar_colors = {
            "background": colors["button_bg"],
            "troughcolor": colors["frame"],
            "arrowcolor": colors["button_fg"],
            "bordercolor": colors["frame"],
            "lightcolor": colors["frame"],
            "darkcolor": colors["frame"],
        }
        for orientation in ("Vertical", "Horizontal"):
            style_name = f"{orientation}.TScrollbar"
            self.style.configure(style_name, **scrollbar_colors)
            self.style.map(
                style_name,
                background=[("active", colors["button_active_bg"])],
                arrowcolor=[("active", colors["button_fg"])],
            )

        dropdown_kwargs = {
            "background": dropdown_bg,
            "foreground": dropdown_fg,
            "select_background": dropdown_select_bg,
            "select_foreground": dropdown_select_fg,
        }
        if hasattr(self, "language_combo"):
            self._style_combobox_dropdown(self.language_combo, **dropdown_kwargs)
        if hasattr(self, "theme_combo"):
            self._style_combobox_dropdown(self.theme_combo, **dropdown_kwargs)

        self.tasks_canvas.configure(background=colors["canvas_bg"], highlightthickness=0)
        if hasattr(self, "tasks_inner"):
            self.tasks_inner.configure(style="TaskContainer.TFrame")
        if hasattr(self, "queue_columns_frame"):
            self.queue_columns_frame.configure(style="TaskHeader.TFrame")
        self.preview_image_label.configure(bg=colors["frame"], fg=colors["text"])
        self.log_widget.configure(
            bg=colors["log_bg"], fg=colors["log_fg"], insertbackground=colors["log_fg"]
        )

    def _style_combobox_dropdown(
        self,
        combobox: ttk.Combobox,
        *,
        background: str,
        foreground: str,
        select_background: str,
        select_foreground: str,
    ) -> None:
        try:
            popdown = self.tk.call("ttk::combobox::PopdownWindow", str(combobox))
        except tk.TclError:
            return

        try:
            popdown_widget = self.nametowidget(popdown)
            popdown_widget.configure(bg=background)
        except (tk.TclError, KeyError):
            popdown_widget = None

        frame_path = f"{popdown}.f"
        try:
            frame_widget = self.nametowidget(frame_path)
        except (tk.TclError, KeyError):
            frame_widget = None

        listbox_path = f"{frame_path}.l"
        try:
            listbox_widget = self.nametowidget(listbox_path)
        except (tk.TclError, KeyError):
            listbox_widget = None

        if frame_widget is not None:
            frame_widget.configure(bg=background)
        if popdown_widget is not None:
            popdown_widget.configure(bg=background)
        if listbox_widget is None:
            return

        listbox_widget.configure(
            background=background,
            foreground=foreground,
            selectbackground=select_background,
            selectforeground=select_foreground,
            highlightcolor=background,
            highlightbackground=background,
            activestyle="none",
        )

    def _store_language(self, code: str) -> None:
        self.settings["language"] = code
        self._save_settings()

    def _store_theme(self, code: str) -> None:
        self.settings["theme"] = code
        self._save_settings()

    def _(self, key: str, **kwargs: object) -> str:
        return translate(self.language, key, **kwargs)

    def _coerce_language(self, value: object) -> str:
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in SUPPORTED_LANGUAGES:
                return lowered
        return DEFAULT_LANGUAGE

    def _coerce_theme(self, value: object) -> str:
        if isinstance(value, str) and value in THEMES:
            return value
        return DEFAULT_THEME

    def _prepare_config_directory(self) -> Path:
        documents_dir = Path.home() / "Documents"
        try:
            documents_dir.mkdir(parents=True, exist_ok=True)
            base_dir = documents_dir
        except Exception:  # pylint: disable=broad-except
            base_dir = Path.home()

        config_dir = base_dir / "YT Downloader Settings"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # pylint: disable=broad-except
            fallback_dir = Path.home() / "YT Downloader Settings"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            return fallback_dir
        return config_dir

    def _legacy_state_candidates(self, filename: str) -> list[Path]:
        script_dir = Path(__file__).resolve().parent
        candidates = [script_dir / filename]

        cwd_candidate = Path.cwd() / filename
        if cwd_candidate != candidates[0]:
            candidates.append(cwd_candidate)

        return candidates

    def _migrate_legacy_state_files(self) -> None:
        for filename in ("settings.json", "download_queue.json"):
            destination = self.config_dir / filename
            if destination.exists():
                continue

            for candidate in self._legacy_state_candidates(filename):
                if not candidate.exists():
                    continue
                try:
                    destination.write_bytes(candidate.read_bytes())
                    break
                except Exception:  # pylint: disable=broad-except
                    continue

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
            title=self._("dialog_choose_folder"),
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

    def _open_source_url(self, url: str) -> None:
        try:
            opened = webbrowser.open(url, new=2)
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror(
                self._("error_generic_title"),
                self._("error_open_url_failed", error=exc),
            )
            return
        if not opened:
            messagebox.showerror(
                self._("error_generic_title"),
                self._(
                    "error_open_url_failed",
                    error=self._("error_open_url_failed_unknown"),
                ),
            )

    def _open_result_folder(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror(
                self._("error_root_title"), self._("error_folder_missing_file")
            )
            return
        folder = path.parent
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(
                    ["explorer", "/select,", str(path)],
                    **_subprocess_no_window_kwargs(),
                )
            elif sys.platform == "darwin":
                subprocess.Popen(
                    ["open", "-R", str(path)],
                    close_fds=True,
                    **_subprocess_no_window_kwargs(),
                )
            else:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    close_fds=True,
                    **_subprocess_no_window_kwargs(),
                )
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror(
                self._("error_root_title"),
                self._("error_open_folder_failed", error=exc),
            )



def main() -> None:
    app = DownloaderUI()
    app.mainloop()
