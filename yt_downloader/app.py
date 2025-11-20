"""Tkinter application entry point for the video downloader."""

from __future__ import annotations

import io
import os
import sys
import datetime as _dt
import json
import queue
import subprocess
import threading
import traceback
import urllib.request
import webbrowser
import time
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:  # Необов'язкова залежність – для обкладинок у форматі JPEG потрібен Pillow.
    from PIL import Image, ImageTk  # type: ignore

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow необов'язковий під час виконання.
    PIL_AVAILABLE = False

from .backend import BackendError, fetch_video_metadata
from .localization import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    translate,
)
from .themes import DEFAULT_THEME, THEMES, apply_theme, resolve_theme
from .updates import (
    InstallResult,
    UpdateError,
    UpdateInfo,
    check_for_update,
    download_update_asset,
    install_downloaded_asset,
)
from .updater import build_updater_command, maybe_run_updater
from .utils import (
    format_timestamp as _format_timestamp,
    is_supported_video_url as _is_supported_video_url,
    parse_time_input as _parse_time_input,
    resolve_asset_path,
)
from .widgets import TaskRow
from .worker import DownloadWorker
from .version import __version__


class DownloaderApp(ctk.CTk):
    """Base window configured for CustomTkinter."""

    def __init__(self) -> None:
        super().__init__()
        self._icon_images: list[tk.PhotoImage] = []
        self._configure_window_icon()
        self.withdraw()

    def _configure_window_icon(self) -> None:
        """Attempt to set a branded application icon for the main window."""

        icon_bitmap = resolve_asset_path("yt-dw-logo.ico")
        if icon_bitmap is not None:
            try:
                self.iconbitmap(default=str(icon_bitmap))
            except Exception:
                pass

        icon_image_path = resolve_asset_path("yt-dw-logo.png")
        if icon_image_path is None:
            return

        try:
            image = tk.PhotoImage(file=str(icon_image_path))
        except Exception:
            return

        self.iconphoto(False, image)
        self._icon_images.append(image)


class DownloaderUI(DownloaderApp):
    def __init__(self) -> None:
        super().__init__()

        self.app_version = __version__
        self.update_log_path = self._determine_update_log_path()
        self._update_log_lock = threading.Lock()
        self.update_dialog: Optional[ctk.CTkToplevel] = None
        self.update_dialog_message_var = ctk.StringVar()
        self.update_dialog_progress: Optional[ctk.CTkProgressBar] = None
        self.update_button_frame: Optional[ctk.CTkFrame] = None
        self.update_primary_button: Optional[ctk.CTkButton] = None
        self.update_secondary_button: Optional[ctk.CTkButton] = None
        self._update_message_key: Optional[str] = None
        self._update_message_kwargs: dict[str, object] = {}
        self._update_dialog_title_key: Optional[str] = None
        self._update_primary_button_key: Optional[str] = None
        self._update_secondary_button_key: Optional[str] = None
        self._update_dialog_can_close = False
        self._update_auto_close_after: Optional[str] = None
        self._update_dialog_shown_at: Optional[float] = None
        self._update_check_start_job: Optional[str] = None
        self._post_update_check_job: Optional[str] = None
        self._update_check_started_at: Optional[float] = None
        self._update_check_finished_at: Optional[float] = None
        self.pending_update_info: Optional[UpdateInfo] = None
        self.pending_install_result: Optional[InstallResult] = None
        self._update_download_total: Optional[int] = None
        self._pending_updater_command: Optional[list[str]] = None

        self.config_dir = self._prepare_config_directory()
        self.settings_path = self.config_dir / "settings.json"
        self.queue_state_path = self.config_dir / "download_queue.json"
        self._migrate_legacy_state_files()
        self.settings: dict[str, object] = self._load_settings()
        self.language = self._coerce_language(self.settings.get("language"))
        self.theme = self._coerce_theme(self.settings.get("theme"))
        self.current_palette: dict[str, str] = {}

        convert_setting = self.settings.get("convert_to_mp4")
        if isinstance(convert_setting, bool):
            convert_enabled = convert_setting
        else:
            convert_enabled = True
            self.settings["convert_to_mp4"] = convert_enabled
            self._save_settings()
        self.convert_to_mp4_var = ctk.BooleanVar(value=convert_enabled)
        self.convert_to_mp4_var.trace_add("write", self._on_convert_to_mp4_toggle)

        sequential_setting = self.settings.get("sequential_downloads")
        if isinstance(sequential_setting, bool):
            sequential_enabled = sequential_setting
        else:
            sequential_enabled = True
            self.settings["sequential_downloads"] = sequential_enabled
            self._save_settings()
        self.sequential_downloads_var = ctk.BooleanVar(value=sequential_enabled)
        self.sequential_downloads_var.trace_add(
            "write", self._on_sequential_downloads_toggle
        )

        self.update_cache_dir = self.config_dir / "updates"

        self._set_window_title()
        self.geometry("1200x760")
        self.minsize(1040, 700)

        self.base_font = ctk.CTkFont(family="Segoe UI", size=12)
        self.small_font = ctk.CTkFont(family="Segoe UI", size=11)
        self.bold_font = ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        self.queue_count_var = ctk.StringVar(value="0")

        default_root = str((Path.home() / "Downloads").resolve())
        saved_root = self.settings.get("root_folder")
        self.initial_root = saved_root if isinstance(saved_root, str) else default_root

        self._log_update_event("Application started", extra={"version": self.app_version})

        self.queue_state = self._load_queue_state()
        self.queue_records: dict[str, dict[str, Any]] = {
            item["task_id"]: item for item in self.queue_state.get("items", [])
        }
        self._save_queue_state()

        self.event_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
        self.workers: dict[str, DownloadWorker] = {}
        self.pending_workers: dict[str, DownloadWorker] = {}
        self.tasks: dict[str, TaskRow] = {}
        self.task_order: list[str] = []
        self.task_counter = self._compute_next_task_counter()
        self.preview_fetch_in_progress = False
        self.preview_token = 0
        self.preview_info: dict[str, str] = {}
        self.duration_seconds: Optional[float] = None
        self.thumbnail_image: Optional["ImageTk.PhotoImage"] = None

        self.preview_title_value: Optional[str] = None
        self.preview_duration_value: Optional[str] = None
        self.preview_status_key = "idle"

        self._main_interface_initialized = False

        self.update_view: Optional[ctk.CTkFrame] = None
        self.update_title_var = ctk.StringVar()
        self.update_title_label: Optional[ctk.CTkLabel] = None

        self.protocol("WM_DELETE_WINDOW", self._on_root_close_attempt)

        self.after(200, self._poll_queue)
        self.after(300, self._ensure_root_folder)
        self.after(400, self._initiate_update_check)

    def _build_main_interface(self) -> None:
        if self._main_interface_initialized:
            return

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        container = ctk.CTkFrame(self, corner_radius=20)
        container.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)
        self.root_container = container

        header_frame = ctk.CTkFrame(container, corner_radius=16, border_width=1)
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header_frame.grid_columnconfigure(1, weight=1)
        self.header_frame = header_frame

        brand_frame = ctk.CTkFrame(header_frame, width=56, height=56, corner_radius=14)
        brand_frame.grid(row=0, column=0, padx=14, pady=12)
        brand_frame.grid_propagate(False)
        self.brand_frame = brand_frame
        self.brand_icon = ctk.CTkLabel(brand_frame, text="⬇", font=self.bold_font)
        self.brand_icon.place(relx=0.5, rely=0.5, anchor="center")

        title_stack = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_stack.grid(row=0, column=1, sticky="w", pady=12)
        title_stack.grid_columnconfigure(0, weight=1)
        self.header_title = ctk.CTkLabel(
            title_stack, anchor="w", font=self.bold_font, text=self._("app_title")
        )
        self.header_title.grid(row=0, column=0, sticky="w")
        self.version_badge = ctk.CTkLabel(
            title_stack,
            anchor="w",
            font=self.small_font,
            text=f"v{self.app_version}",
            corner_radius=10,
            padx=10,
            pady=4,
        )
        self.version_badge.grid(row=0, column=1, sticky="w", padx=(10, 0))

        control_row = ctk.CTkFrame(header_frame, fg_color="transparent")
        control_row.grid(row=0, column=2, sticky="e", padx=14)
        control_row.grid_columnconfigure(1, weight=1)
        control_row.grid_columnconfigure(3, weight=1)
        self.language_label = ctk.CTkLabel(control_row, anchor="e", font=self.base_font)
        self.language_label.grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.language_display_var = ctk.StringVar()
        self.language_combo = ctk.CTkComboBox(
            control_row,
            state="readonly",
            values=[],
            variable=self.language_display_var,
            command=self._on_language_selected,
            width=160,
        )
        self.language_combo.grid(row=0, column=1, sticky="e", padx=(0, 12))

        self.theme_label = ctk.CTkLabel(control_row, anchor="e", font=self.base_font)
        self.theme_label.grid(row=0, column=2, sticky="e", padx=(12, 6))
        self.theme_display_var = ctk.StringVar()
        self.theme_combo = ctk.CTkComboBox(
            control_row,
            state="readonly",
            values=[],
            variable=self.theme_display_var,
            command=self._on_theme_selected,
            width=160,
        )
        self.theme_combo.grid(row=0, column=3, sticky="e")

        content_frame = ctk.CTkFrame(container, fg_color="transparent")
        content_frame.grid(row=1, column=0, sticky="nsew")
        content_frame.grid_columnconfigure(0, weight=4)
        content_frame.grid_columnconfigure(1, weight=6)
        content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame = content_frame

        left_frame = ctk.CTkFrame(content_frame, corner_radius=18, border_width=1)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left_frame.grid_columnconfigure(0, weight=1)
        left_frame.grid_rowconfigure(1, weight=1)
        self.left_frame = left_frame

        right_column = ctk.CTkFrame(content_frame, corner_radius=18, border_width=1)
        right_column.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        right_column.grid_columnconfigure(0, weight=1)
        right_column.grid_rowconfigure(0, weight=3)
        right_column.grid_rowconfigure(1, weight=1)
        self.right_column = right_column

        inputs_card = ctk.CTkFrame(left_frame, corner_radius=14, border_width=1)
        inputs_card.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 10))
        inputs_card.grid_columnconfigure(0, weight=1)
        inputs_card.grid_columnconfigure(1, weight=0)
        self.inputs_card = inputs_card

        self.url_label = ctk.CTkLabel(
            inputs_card, text=self._("url_label"), font=self.base_font, anchor="w"
        )
        self.url_label.grid(row=0, column=0, sticky="w", pady=(2, 4))
        self.url_var = ctk.StringVar()
        self.url_entry = ctk.CTkEntry(inputs_card, textvariable=self.url_var, font=self.base_font)
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(0, 2))
        self.url_var.trace_add("write", self._on_url_change)
        self.search_button = ctk.CTkButton(
            inputs_card, text=self._("search_button"), command=self._fetch_preview, width=150
        )
        self.search_button.grid(row=1, column=1, sticky="e")
        self._update_search_button_state()

        self.root_label = ctk.CTkLabel(
            inputs_card, text=self._("root_label"), font=self.base_font, anchor="w"
        )
        self.root_label.grid(row=2, column=0, sticky="w", pady=(12, 4))
        self.root_var = ctk.StringVar(value=self.initial_root)
        self.root_entry = ctk.CTkEntry(inputs_card, textvariable=self.root_var, font=self.base_font)
        self.root_entry.grid(row=3, column=0, sticky="ew", padx=(0, 10), pady=(0, 2))
        self.browse_button = ctk.CTkButton(
            inputs_card, text=self._("choose_button"), command=self._browse_root, width=150
        )
        self.browse_button.grid(row=3, column=1, sticky="e")

        toggles_frame = ctk.CTkFrame(inputs_card, corner_radius=12, border_width=1)
        toggles_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 10))
        toggles_frame.grid_columnconfigure(0, weight=1)
        toggles_frame.grid_columnconfigure(1, weight=1)
        self.toggles_frame = toggles_frame

        self.separate_var = ctk.BooleanVar(value=False)
        self.separate_check = ctk.CTkCheckBox(
            toggles_frame,
            text=self._("separate_folder"),
            variable=self.separate_var,
            onvalue=True,
            offvalue=False,
            font=self.base_font,
        )
        self.separate_check.grid(row=0, column=0, sticky="w", padx=12, pady=8)

        download_options_frame = ctk.CTkFrame(toggles_frame, fg_color="transparent")
        download_options_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        download_options_frame.grid_columnconfigure(0, weight=1)
        download_options_frame.grid_columnconfigure(1, weight=1)
        self.download_options_frame = download_options_frame
        self.convert_check = ctk.CTkCheckBox(
            download_options_frame,
            text=self._("convert_to_mp4"),
            variable=self.convert_to_mp4_var,
            onvalue=True,
            offvalue=False,
            font=self.base_font,
        )
        self.convert_check.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.sequential_check = ctk.CTkCheckBox(
            download_options_frame,
            text=self._("sequential_downloads"),
            variable=self.sequential_downloads_var,
            onvalue=True,
            offvalue=False,
            font=self.base_font,
        )
        self.sequential_check.grid(row=0, column=1, sticky="w")

        self.download_button = ctk.CTkButton(
            inputs_card,
            text=self._("download_button"),
            command=self._start_worker,
            state="disabled",
            height=44,
        )
        self.download_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 4))

        self.preview_frame = ctk.CTkFrame(left_frame, corner_radius=14, border_width=1)
        preview_frame = self.preview_frame
        preview_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 10))
        preview_frame.columnconfigure(0, weight=1)

        self.preview_frame_label = ctk.CTkLabel(
            preview_frame,
            text=self._("preview_group"),
            font=self.bold_font,
            anchor="w",
        )
        self.preview_frame_label.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))

        self.preview_title_var = ctk.StringVar()
        self.preview_title_label = ctk.CTkLabel(
            preview_frame,
            textvariable=self.preview_title_var,
            justify="left",
            anchor="w",
            wraplength=520,
            font=self.base_font,
        )
        self.preview_title_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))

        self.preview_duration_var = ctk.StringVar()
        self.preview_duration_label = ctk.CTkLabel(
            preview_frame,
            textvariable=self.preview_duration_var,
            justify="left",
            anchor="w",
            font=self.base_font,
        )
        self.preview_duration_label.grid(row=2, column=0, sticky="w", padx=12)

        thumbnail_card = ctk.CTkFrame(preview_frame, corner_radius=12, border_width=1)
        thumbnail_card.grid(row=3, column=0, padx=12, pady=10, sticky="nsew")
        thumbnail_card.grid_columnconfigure(0, weight=1)
        thumbnail_card.grid_rowconfigure(0, weight=1)
        self.thumbnail_card = thumbnail_card
        self.preview_image_label = ctk.CTkLabel(thumbnail_card, text="")
        self.preview_image_label.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.preview_image_text_key = None
        if not PIL_AVAILABLE:
            self.preview_image_text_key = "preview_thumbnail_pillow_required"
            self.preview_image_label.configure(text=self._("preview_thumbnail_pillow_required"))

        clip_frame = ctk.CTkFrame(preview_frame, fg_color="transparent")
        clip_frame.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 6))
        self.clip_frame = clip_frame
        self.clip_start_label = ctk.CTkLabel(
            clip_frame, text=self._("clip_start_label"), font=self.base_font
        )
        self.clip_start_label.grid(row=0, column=0, sticky="w")
        self.start_time_var = ctk.StringVar(value="00:00")
        self.start_entry = ctk.CTkEntry(
            clip_frame,
            textvariable=self.start_time_var,
            width=90,
            state="disabled",
            font=self.base_font,
        )
        self.start_entry.grid(row=0, column=1, sticky="w", padx=(6, 18))
        self.clip_end_label = ctk.CTkLabel(
            clip_frame, text=self._("clip_end_label"), font=self.base_font
        )
        self.clip_end_label.grid(row=0, column=2, sticky="w")
        self.end_time_var = ctk.StringVar(value="00:00")
        self.end_entry = ctk.CTkEntry(
            clip_frame,
            textvariable=self.end_time_var,
            width=90,
            state="disabled",
            font=self.base_font,
        )
        self.end_entry.grid(row=0, column=3, sticky="w", padx=(6, 0))

        self.preview_status_var = ctk.StringVar(value="")
        self.preview_status_label = ctk.CTkLabel(
            preview_frame, textvariable=self.preview_status_var, anchor="w", font=self.base_font
        )
        self.preview_status_label.grid(row=5, column=0, sticky="w", padx=12, pady=(0, 12))

        self.queue_shell = ctk.CTkFrame(right_column, corner_radius=16, border_width=1)
        self.queue_shell.grid(row=0, column=0, sticky="nsew")
        self.queue_shell.grid_columnconfigure(0, weight=1)
        self.queue_shell.grid_rowconfigure(0, weight=1)

        self.queue_frame = ctk.CTkFrame(self.queue_shell, corner_radius=12, border_width=1)
        queue_frame = self.queue_frame
        queue_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(3, weight=1)

        self.queue_frame_label = ctk.CTkLabel(
            queue_frame, text=self._("queue_group"), font=self.bold_font, anchor="w"
        )
        self.queue_frame_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 6))

        header_frame = ctk.CTkFrame(queue_frame, fg_color="transparent")
        header_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
        header_frame.columnconfigure(0, weight=1)
        self.queue_header_frame = header_frame

        queue_meta = ctk.CTkFrame(header_frame, fg_color="transparent")
        queue_meta.grid(row=0, column=0, sticky="w")
        self.queue_count_badge = ctk.CTkLabel(
            queue_meta,
            textvariable=self.queue_count_var,
            font=self.small_font,
            corner_radius=10,
            padx=10,
            pady=4,
        )
        self.queue_count_badge.grid(row=0, column=0, sticky="w")

        self.clear_history_button = ctk.CTkButton(
            header_frame,
            text=self._("clear_history"),
            command=self._confirm_clear_history,
            state="disabled",
            width=140,
        )
        self.clear_history_button.grid(row=0, column=1, sticky="e")

        self.queue_columns_frame = ctk.CTkFrame(queue_frame, fg_color="transparent")
        columns_frame = self.queue_columns_frame
        columns_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 2))
        columns_frame.columnconfigure(0, weight=3)
        columns_frame.columnconfigure(1, weight=2)
        columns_frame.columnconfigure(2, weight=0)
        self.queue_title_header = ctk.CTkLabel(
            columns_frame,
            text=self._("queue_column_title"),
            anchor="w",
            font=self.small_font,
        )
        self.queue_status_header = ctk.CTkLabel(
            columns_frame,
            text=self._("queue_column_status"),
            anchor="w",
            font=self.small_font,
        )
        self.queue_actions_header = ctk.CTkLabel(
            columns_frame,
            text=self._("queue_column_actions"),
            anchor="e",
            font=self.small_font,
        )
        self.queue_title_header.grid(row=0, column=0, sticky="w")
        self.queue_status_header.grid(row=0, column=1, sticky="w")
        self.queue_actions_header.grid(row=0, column=2, sticky="e")

        tasks_container = ctk.CTkFrame(queue_frame, fg_color="transparent")
        tasks_container.grid(
            row=3, column=0, columnspan=2, sticky="nsew", padx=12, pady=(0, 12)
        )
        tasks_container.grid_columnconfigure(0, weight=1)
        tasks_container.grid_rowconfigure(0, weight=1)
        self.tasks_container = tasks_container

        self.tasks_canvas = tk.Canvas(tasks_container, highlightthickness=0)
        self.tasks_canvas.grid(row=0, column=0, sticky="nsew")
        self.tasks_scroll = ctk.CTkScrollbar(tasks_container, orientation="vertical")
        self.tasks_scroll.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.tasks_canvas.configure(yscrollcommand=self.tasks_scroll.set)
        self.tasks_scroll.configure(command=self.tasks_canvas.yview)
        self.tasks_inner = ctk.CTkFrame(self.tasks_canvas, fg_color="transparent")
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

        self.log_frame = ctk.CTkFrame(right_column, corner_radius=14, border_width=1)
        log_frame = self.log_frame
        log_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 12))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        self.log_frame_label = ctk.CTkLabel(
            log_frame, text=self._("log_group"), font=self.bold_font, anchor="w"
        )
        self.log_frame_label.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))

        self.log_widget = ctk.CTkTextbox(
            log_frame,
            state="disabled",
            font=("Consolas", 10),
            wrap="word",
        )
        self.log_widget.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self._register_clipboard_shortcuts()

        self._main_interface_initialized = True
        self._apply_language()
        self._apply_theme()
        self._update_clear_history_state()
    def _determine_update_log_path(self) -> Path:
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path(__file__).resolve().parent
        return base_dir / "update.log"

    def _log_update_event(
        self,
        message: str,
        *,
        extra: Optional[dict[str, object]] = None,
        include_exception: bool = False,
    ) -> None:
        try:
            timestamp = _dt.datetime.now().isoformat(sep=" ", timespec="seconds")
            payload = {"message": message}
            if extra:
                payload.update(extra)
            if include_exception:
                payload["exception"] = traceback.format_exc()
            line = f"[{timestamp}] " + json.dumps(payload, ensure_ascii=False)
            with self._update_log_lock:
                with self.update_log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(line + "\n")
        except Exception:
            pass

    def _set_preview_title(self, title: Optional[str]) -> None:
        self.preview_title_value = title
        if not hasattr(self, "preview_title_var"):
            return
        display = title if title else "—"
        self.preview_title_var.set(self._("preview_title", title=display))

    def _set_preview_duration(self, duration: Optional[str]) -> None:
        self.preview_duration_value = duration
        if not hasattr(self, "preview_duration_var"):
            return
        display = duration if duration else "—"
        self.preview_duration_var.set(self._("preview_duration", duration=display))

    def _set_preview_status(self, key: str) -> None:
        self.preview_status_key = key
        if not hasattr(self, "preview_status_var"):
            return
        if key == "idle":
            self.preview_status_var.set("")
            return
        self.preview_status_var.set(self._(f"preview_status_{key}"))

    def _set_preview_image_text(self, key: Optional[str]) -> None:
        self.preview_image_text_key = key
        if not hasattr(self, "preview_image_label"):
            return
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
        initial_status = "downloading"
        if self.sequential_downloads_var.get() and (
            self._has_running_workers() or self.pending_workers
        ):
            initial_status = "waiting"

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
            status=initial_status,
            palette=self.current_palette,
            title_font=self.bold_font,
            status_font=self.small_font,
        )
        task_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.tasks = {task_id: task_row, **self.tasks}
        self.task_order.insert(0, task_id)

        record: dict[str, Any] = {
            "task_id": task_id,
            "title": display_title,
            "status": initial_status,
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
            convert_to_mp4=self.convert_to_mp4_var.get(),
            start_seconds=start_seconds,
            end_seconds=clip_end_for_worker,
            event_queue=self.event_queue,
            language=self.language,
        )
        if initial_status == "waiting":
            self.pending_workers[task_id] = worker
            self._maybe_start_next_waiting_task()
        else:
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
        if PIL_AVAILABLE:
            self.preview_image_label.configure(image="", text="")
            self.preview_image_text_key = None
        else:
            self._set_preview_image_text("preview_thumbnail_pillow_required")
        self.thumbnail_image = None
        self._set_button_enabled(self.download_button, False)
        self._set_clip_controls_enabled(False)
        self._update_search_button_state()

    def _update_search_button_state(self) -> None:
        if not hasattr(self, "search_button"):
            return
        should_enable = (
            not self.preview_fetch_in_progress
            and _is_supported_video_url(self.url_var.get().strip())
        )
        self._set_button_enabled(self.search_button, should_enable)

    def _fetch_preview(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(
                self._("warning_url_title"), self._("warning_url_body")
            )
            return
        if self.preview_fetch_in_progress:
            return
        if not _is_supported_video_url(url):
            self._update_search_button_state()
            return

        self.preview_fetch_in_progress = True
        self._update_search_button_state()
        self.preview_token += 1
        token = self.preview_token
        self._set_preview_status("loading")
        self._set_button_enabled(self.download_button, False)
        self._set_preview_title(None)
        self._set_preview_duration(None)
        self._set_clip_controls_enabled(False)
        if PIL_AVAILABLE:
            self.preview_image_label.configure(image="", text="")
            self.preview_image_text_key = None
        else:
            self._set_preview_image_text("preview_thumbnail_pillow_required")
        self.thumbnail_image = None

        def worker() -> None:
            try:
                data = fetch_video_metadata(url)
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
            except BackendError as exc:
                self.after(0, lambda: self._preview_error(token, str(exc)))
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
        image: Optional["ImageTk.PhotoImage"],
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
            self._set_button_enabled(self.download_button, True)
            self._set_preview_status("ready")
        else:
            self.duration_seconds = None
            self._set_preview_duration(None)
            self.start_time_var.set("00:00")
            self.end_time_var.set("")
            self._set_clip_controls_enabled(False)
            self._set_button_enabled(self.download_button, False)
            self._set_preview_status("no_duration")

        if PIL_AVAILABLE and image is not None:
            self.thumbnail_image = image
            self.preview_image_label.configure(image=image, text="")
            self.preview_image_text_key = None
        elif not PIL_AVAILABLE and thumbnail_url:
            self._set_preview_image_text("preview_thumbnail_unavailable")
            self.thumbnail_image = None
        else:
            if PIL_AVAILABLE:
                self.preview_image_label.configure(text="", image="")
                self.preview_image_text_key = None
            else:
                self._set_preview_image_text("preview_thumbnail_pillow_required")
            self.thumbnail_image = None

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
        self._set_button_enabled(self.download_button, False)
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
        if PIL_AVAILABLE:
            self.preview_image_label.configure(text="", image="")
            self.preview_image_text_key = None
        else:
            self._set_preview_image_text("preview_thumbnail_pillow_required")
        self.thumbnail_image = None
        self._set_preview_status("idle")
        self._set_button_enabled(self.download_button, False)
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

        if self.sequential_downloads_var.get():
            self._maybe_start_next_waiting_task()
        elif self.pending_workers:
            self._start_all_pending_tasks()

        self.after(200, self._poll_queue)

    def _maybe_start_next_waiting_task(self) -> None:
        if self._has_running_workers():
            return
        if not self.pending_workers:
            return
        ordered = sorted(self.pending_workers, key=self._task_created_at)
        self._activate_pending_worker(ordered[0])

    def _start_all_pending_tasks(self) -> None:
        if not self.pending_workers:
            return
        for task_id in sorted(self.pending_workers, key=self._task_created_at):
            self._activate_pending_worker(task_id)

    def _activate_pending_worker(self, task_id: str) -> None:
        worker = self.pending_workers.pop(task_id, None)
        if worker is None:
            return
        task_row = self.tasks.get(task_id)
        if not task_row:
            self._remove_queue_record(task_id)
            return
        task_row.update_status("downloading")
        self._update_queue_record(task_id, status="downloading")
        self.workers[task_id] = worker
        worker.start()
        self._append_log(f"[{task_row.full_title}] {self._('log_process_started')}")

    def _has_running_workers(self) -> bool:
        return any(worker.is_alive() for worker in self.workers.values())

    def _task_created_at(self, task_id: str) -> float:
        record = self.queue_records.get(task_id, {})
        raw_timestamp = record.get("created_at") if isinstance(record, dict) else None
        if isinstance(raw_timestamp, str):
            try:
                return _dt.datetime.fromisoformat(raw_timestamp).timestamp()
            except Exception:
                pass
        return _dt.datetime.now().timestamp()

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "log_widget"):
            return
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
        self.pending_workers.clear()
        self.queue_state["items"] = []
        self._save_queue_state()
        self._reflow_task_rows()
        self.tasks_canvas.yview_moveto(0)
        self._update_clear_history_state()

    def _update_clear_history_state(self) -> None:
        if not hasattr(self, "clear_history_button"):
            return
        if self.tasks:
            self._set_button_enabled(self.clear_history_button, True)
        else:
            self._set_button_enabled(self.clear_history_button, False)

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
            if _is_supported_video_url(self.url_var.get().strip()):
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
        self.pending_workers.pop(task_id, None)
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
        self._refresh_queue_badge()

    def _refresh_queue_badge(self) -> None:
        if hasattr(self, "queue_count_var"):
            self.queue_count_var.set(str(len(self.tasks)))

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
                palette=self.current_palette,
                title_font=self.bold_font,
                status_font=self.small_font,
            )
            task_row.grid(row=len(self.tasks), column=0, sticky="ew", pady=(0, 6))
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
        self.pending_workers.pop(task_id, None)
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

    def _on_language_selected(self, selection: str | None) -> None:  # pragma: no cover - UI callback
        if not selection:
            selection = self.language_display_var.get()
        code = getattr(self, "_language_name_map", {}).get(selection)
        if not code:
            current_name = getattr(self, "_language_display_by_code", {}).get(
                self.language
            )
            if current_name:
                self.language_display_var.set(current_name)
                self.language_combo.set(current_name)
            return
        if code == self.language:
            return
        self.language = code
        self._store_language(code)
        self._apply_language()

    def _on_theme_selected(self, selection: str | None) -> None:  # pragma: no cover - UI callback
        if not selection:
            selection = self.theme_display_var.get()
        code = getattr(self, "_theme_name_map", {}).get(selection)
        if not code:
            current_name = getattr(self, "_theme_display_by_code", {}).get(self.theme)
            if current_name:
                self.theme_display_var.set(current_name)
                self.theme_combo.set(current_name)
            return
        if code == self.theme:
            return
        self.theme = code
        self._store_theme(code)
        self._apply_theme()

    def _apply_language(self) -> None:
        if not self._main_interface_initialized:
            return
        language_names = {code: translate(self.language, f"language_{code}") for code in SUPPORTED_LANGUAGES}
        self._language_display_by_code = language_names
        self._language_name_map = {name: code for code, name in language_names.items()}
        self.language_combo.configure(values=list(language_names.values()))
        current_language_name = language_names.get(self.language, language_names[DEFAULT_LANGUAGE])
        self.language_display_var.set(current_language_name)
        self.language_combo.set(current_language_name)

        theme_names = {key: self._(f"theme_{key}") for key in THEMES}
        self._theme_display_by_code = theme_names
        self._theme_name_map = {name: key for key, name in theme_names.items()}
        self.theme_combo.configure(values=list(theme_names.values()))
        current_theme_name = theme_names.get(self.theme, theme_names[DEFAULT_THEME])
        self.theme_display_var.set(current_theme_name)
        self.theme_combo.set(current_theme_name)

        self._set_window_title()
        if getattr(self, "header_title", None) is not None:
            self.header_title.configure(text=self._("app_title"))
        self.language_label.configure(text=self._("language_label"))
        self.theme_label.configure(text=self._("theme_label"))
        self.url_label.configure(text=self._("url_label"))
        self.search_button.configure(text=self._("search_button"))
        self.root_label.configure(text=self._("root_label"))
        self.browse_button.configure(text=self._("choose_button"))
        self.separate_check.configure(text=self._("separate_folder"))
        self.convert_check.configure(text=self._("convert_to_mp4"))
        self.sequential_check.configure(text=self._("sequential_downloads"))
        self.download_button.configure(text=self._("download_button"))
        self.preview_frame_label.configure(text=self._("preview_group"))
        self.queue_frame_label.configure(text=self._("queue_group"))
        self.log_frame_label.configure(text=self._("log_group"))
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
        self._refresh_update_dialog_language()

    def _apply_button_style(
        self,
        button: Optional[ctk.CTkButton],
        *,
        accent: str,
        hover: str,
        text_color: str,
        disabled_bg: str,
        disabled_text: str,
    ) -> None:
        if button is None:
            return
        button.configure(
            fg_color=accent,
            hover_color=hover,
            text_color=text_color,
            text_color_disabled=disabled_text,
        )
        setattr(button, "_normal_fg_color", accent)
        setattr(button, "_hover_fg_color", hover)
        setattr(button, "_disabled_fg_color", disabled_bg)
        setattr(button, "_normal_text_color", text_color)
        setattr(button, "_disabled_text_color", disabled_text)
        state = str(button.cget("state"))
        if state == "disabled":
            button.configure(fg_color=disabled_bg, hover_color=disabled_bg)
        else:
            button.configure(fg_color=accent, hover_color=hover)

    def _set_button_enabled(self, button: Optional[ctk.CTkButton], enabled: bool) -> None:
        if button is None:
            return
        normal_fg = getattr(button, "_normal_fg_color", None)
        hover_fg = getattr(button, "_hover_fg_color", normal_fg)
        disabled_fg = getattr(button, "_disabled_fg_color", normal_fg)
        disabled_text = getattr(button, "_disabled_text_color", None)
        normal_text = getattr(button, "_normal_text_color", None)
        if enabled:
            kwargs: dict[str, object] = {"state": "normal"}
            if normal_fg:
                kwargs["fg_color"] = normal_fg
            if hover_fg:
                kwargs["hover_color"] = hover_fg
            if normal_text:
                kwargs["text_color"] = normal_text
            button.configure(**kwargs)
        else:
            kwargs = {"state": "disabled"}
            if disabled_fg:
                kwargs["fg_color"] = disabled_fg
                kwargs["hover_color"] = disabled_fg
            if disabled_text:
                kwargs["text_color_disabled"] = disabled_text
            button.configure(**kwargs)

    def _apply_theme(self) -> None:
        if not self._main_interface_initialized:
            return
        theme_def = apply_theme(self.theme)
        colors = dict(theme_def.colors)
        self.current_palette = colors

        background = colors.get("background")
        surface = colors.get("surface") or background
        accent = colors.get("accent") or "#2563eb"
        hover = colors.get("accent_hover") or accent
        text = colors.get("text") or "#202124"
        muted = colors.get("muted") or text
        disabled = colors.get("disabled") or "#888888"
        entry_color = colors.get("entry", surface)
        canvas_color = colors.get("canvas", surface)
        log_bg = colors.get("log_bg", surface)
        log_fg = colors.get("log_fg", text)
        highlight = colors.get("highlight", surface)
        button_text = colors.get("button_text") or "#ffffff"

        if background:
            self.configure(fg_color=background, bg_color=background)
        frame_color = surface or background
        for frame_name in (
            "root_container",
            "header_frame",
            "content_frame",
            "left_frame",
            "right_column",
            "inputs_card",
            "thumbnail_card",
            "toggles_frame",
            "download_options_frame",
            "preview_frame",
            "clip_frame",
            "queue_shell",
            "queue_frame",
            "queue_header_frame",
            "queue_columns_frame",
            "tasks_container",
            "log_frame",
        ):
            frame = getattr(self, frame_name, None)
            if frame is not None and frame_color is not None:
                frame.configure(
                    fg_color=frame_color, bg_color=background, border_color=highlight
                )
        if highlight and getattr(self, "toggles_frame", None) is not None:
            self.toggles_frame.configure(fg_color=highlight, bg_color=background)
        if getattr(self, "brand_frame", None) is not None:
            self.brand_frame.configure(fg_color=accent, bg_color=background)

        label_targets = (
            "language_label",
            "theme_label",
            "url_label",
            "root_label",
            "preview_frame_label",
            "preview_title_label",
            "preview_duration_label",
            "queue_frame_label",
            "queue_title_header",
            "queue_actions_header",
            "log_frame_label",
        )
        for label_name in label_targets:
            label = getattr(self, label_name, None)
            if label is not None:
                label.configure(text_color=text)
        if getattr(self, "header_title", None) is not None:
            self.header_title.configure(text_color=text)
        if getattr(self, "version_badge", None) is not None:
            self.version_badge.configure(fg_color=highlight, text_color=accent or text)
        if getattr(self, "queue_count_badge", None) is not None:
            self.queue_count_badge.configure(fg_color=accent, text_color=button_text)
        if getattr(self, "brand_icon", None) is not None:
            self.brand_icon.configure(text_color=button_text)
        self.preview_status_label.configure(text_color=muted)
        self.queue_status_header.configure(text_color=muted)

        button_targets = (
            self.search_button,
            self.browse_button,
            self.download_button,
            self.clear_history_button,
        )
        disabled_text = colors.get("button_disabled_text", disabled or button_text)
        disabled_bg = colors.get("button_disabled", disabled or accent)
        for button in button_targets:
            self._apply_button_style(
                button,
                accent=accent,
                hover=hover,
                text_color=button_text,
                disabled_bg=disabled_bg,
                disabled_text=disabled_text,
            )

        for checkbox in (getattr(self, "separate_check", None), getattr(self, "convert_check", None)):
            if checkbox is None:
                continue
            checkbox.configure(
                text_color=text,
                fg_color=accent,
                border_color=accent,
                hover_color=hover,
                checkmark_color=surface or "white",
            )

        combo_kwargs = {
            "fg_color": entry_color,
            "border_color": highlight,
            "button_color": accent,
            "button_hover_color": hover,
            "text_color": text,
            "dropdown_fg_color": entry_color,
            "dropdown_hover_color": highlight,
            "dropdown_text_color": text,
        }
        self.language_combo.configure(**combo_kwargs)
        self.theme_combo.configure(**combo_kwargs)

        entry_kwargs = {
            "fg_color": entry_color,
            "border_color": highlight,
            "text_color": text,
            "text_color_disabled": disabled,
        }
        self.url_entry.configure(**entry_kwargs)
        self.root_entry.configure(**entry_kwargs)
        self.start_entry.configure(**entry_kwargs)
        self.end_entry.configure(**entry_kwargs)

        if surface is not None:
            self.preview_image_label.configure(text_color=muted, fg_color=surface)
            self.queue_columns_frame.configure(fg_color=surface)

        canvas_bg = canvas_color or surface
        self.tasks_canvas.configure(
            background=canvas_bg,
            bg=canvas_bg,
            highlightbackground=canvas_bg,
            highlightcolor=canvas_bg,
            highlightthickness=0,
            bd=0,
        )
        if self.tasks_inner is not None and canvas_bg is not None:
            self.tasks_inner.configure(fg_color=canvas_bg, bg_color=canvas_bg)
        self.tasks_scroll.configure(
            fg_color=surface,
            button_color=accent,
            button_hover_color=hover,
        )

        self.log_widget.configure(
            fg_color=log_bg,
            text_color=log_fg,
            border_color=highlight,
            border_width=1,
        )

        for row in self.tasks.values():
            row.apply_palette(colors)

        if self.update_view is not None:
            self.update_view.configure(fg_color=surface)
            if self.update_title_label is not None:
                self.update_title_label.configure(text_color=text)
            if self.update_button_frame is not None:
                self.update_button_frame.configure(fg_color=surface)
            for button in (self.update_primary_button, self.update_secondary_button):
                if button is not None:
                    self._apply_button_style(
                        button,
                        accent=accent,
                        hover=hover,
                        text_color=button_text,
                        disabled_bg=disabled_bg,
                        disabled_text=disabled_text,
                    )
            if self.update_dialog_progress is not None:
                self.update_dialog_progress.configure(
                    fg_color=highlight,
                    progress_color=accent,
                )

    def _set_window_title(self) -> None:
        self.title(self._("app_title_with_version", version=self.app_version))

    def _refresh_update_dialog_language(self) -> None:
        if not self.update_dialog:
            return
        if self._update_dialog_title_key is not None:
            title_text = self._(self._update_dialog_title_key)
            self.title(title_text)
            if self.update_title_label is not None:
                self.update_title_var.set(title_text)
        if self._update_message_key is not None:
            self.update_dialog_message_var.set(
                self._(self._update_message_key, **self._update_message_kwargs)
            )
        if self.update_primary_button and self._update_primary_button_key is not None:
            self.update_primary_button.configure(text=self._(self._update_primary_button_key))
        if self.update_secondary_button and self._update_secondary_button_key is not None:
            self.update_secondary_button.configure(text=self._(self._update_secondary_button_key))

    def _store_language(self, code: str) -> None:
        self.settings["language"] = code
        self._save_settings()

    def _store_theme(self, code: str) -> None:
        self.settings["theme"] = code
        self._save_settings()

    def _on_convert_to_mp4_toggle(self, *_: object) -> None:
        value = self.convert_to_mp4_var.get()
        if self.settings.get("convert_to_mp4") != value:
            self.settings["convert_to_mp4"] = value
            self._save_settings()

    def _on_sequential_downloads_toggle(self, *_: object) -> None:
        value = self.sequential_downloads_var.get()
        if self.settings.get("sequential_downloads") != value:
            self.settings["sequential_downloads"] = value
            self._save_settings()
        if value:
            self._maybe_start_next_waiting_task()
        else:
            self._start_all_pending_tasks()

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
        editable_classes = (ctk.CTkEntry, ctk.CTkTextbox, tk.Entry, tk.Text)
        if not isinstance(widget, editable_classes):
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
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(folder)], close_fds=True)
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror(
                self._("error_root_title"),
                self._("error_open_folder_failed", error=exc),
            )

    def _initiate_update_check(self) -> None:
        if self.update_dialog is not None:
            return
        self._log_update_event("Starting update check dialog")
        self.pending_update_info = None
        self.pending_install_result = None
        self._update_download_total = None
        self._build_update_dialog()
        self._set_update_dialog_title("update_check_title")
        self._set_update_dialog_message("update_check_message")
        if self.update_dialog_progress is not None:
            self.update_dialog_progress.configure(mode="indeterminate")
            self.update_dialog_progress.start()
        self._set_update_dialog_closable(False)
        self._update_check_started_at = None
        self._update_check_finished_at = None
        if self._update_check_start_job is not None:
            try:
                self.after_cancel(self._update_check_start_job)
            except tk.TclError:
                pass
        self._log_update_event("Delaying update check start", extra={"delay_ms": 1000})

        def _start_worker() -> None:
            self._update_check_start_job = None
            self._update_check_started_at = time.monotonic()
            threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

        self._update_check_start_job = str(self.after(1000, _start_worker))

    def _build_update_dialog(self) -> None:
        self._log_update_event("Building update view")
        if self.update_view is None:
            container = ctk.CTkFrame(self, corner_radius=12, fg_color="transparent")
            container.grid(row=0, column=0, sticky="nsew", padx=24, pady=24)
            self.grid_rowconfigure(0, weight=1)
            self.grid_columnconfigure(0, weight=1)

            self.update_title_label = ctk.CTkLabel(
                container,
                textvariable=self.update_title_var,
                font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
                anchor="center",
                justify="center",
            )
            self.update_title_label.pack(fill="x", pady=(0, 12))

            message_label = ctk.CTkLabel(
                container,
                textvariable=self.update_dialog_message_var,
                justify="center",
                wraplength=460,
                anchor="center",
            )
            message_label.pack(fill="x", padx=6, pady=(0, 18))

            progress = ctk.CTkProgressBar(container, mode="indeterminate")
            progress.pack(fill="x", padx=30, pady=(0, 24))
            self.update_dialog_progress = progress

            button_frame = ctk.CTkFrame(container, fg_color="transparent")
            button_frame.pack(fill="x", pady=(0, 12))
            self.update_button_frame = button_frame
            self.update_secondary_button = ctk.CTkButton(button_frame)
            self.update_primary_button = ctk.CTkButton(button_frame)
            self.update_secondary_button.pack_forget()
            self.update_primary_button.pack_forget()

            self.update_view = container

        self.update_dialog = self.update_view
        self._configure_update_dialog_buttons()
        self._present_update_view()

    def _present_update_view(self) -> None:
        self._update_dialog_shown_at = time.monotonic()
        self.deiconify()
        self.update()
        self._log_update_event("Update view shown")

    def _set_update_dialog_title(self, key: str) -> None:
        self._update_dialog_title_key = key
        title_text = self._(key)
        self.title(title_text)
        if self.update_title_label is not None:
            self.update_title_var.set(title_text)

    def _set_update_dialog_message(self, key: str, **kwargs: object) -> None:
        self._update_message_key = key
        self._update_message_kwargs = kwargs
        self.update_dialog_message_var.set(self._(key, **kwargs))

    def _configure_update_dialog_buttons(
        self,
        primary: Optional[tuple[str, Callable[[], None]]] = None,
        secondary: Optional[tuple[str, Callable[[], None]]] = None,
    ) -> None:
        if self.update_button_frame is None:
            return
        if self.update_secondary_button is None:
            self.update_secondary_button = ctk.CTkButton(self.update_button_frame)
        if self.update_primary_button is None:
            self.update_primary_button = ctk.CTkButton(self.update_button_frame)

        self.update_primary_button.pack_forget()
        self.update_secondary_button.pack_forget()

        if secondary is not None:
            key, command = secondary
            self.update_secondary_button.configure(text=self._(key), command=command)
            self.update_secondary_button.pack(side="right", padx=6)
            self._update_secondary_button_key = key
        else:
            self._update_secondary_button_key = None

        if primary is not None:
            key, command = primary
            self.update_primary_button.configure(text=self._(key), command=command)
            self.update_primary_button.pack(side="right", padx=6)
            self._update_primary_button_key = key
        else:
            self._update_primary_button_key = None

    def _set_update_dialog_closable(self, value: bool) -> None:
        self._update_dialog_can_close = value

    def _on_update_dialog_close_attempt(self) -> None:
        if self._update_dialog_can_close:
            self._close_update_dialog()

    def _show_main_window(self) -> None:
        self._build_main_interface()
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _on_root_close_attempt(self) -> None:
        if self.update_dialog is not None and not self._main_interface_initialized:
            if self._update_dialog_can_close:
                self._close_update_dialog()
            return
        self.destroy()

    def _close_update_dialog(self, show_main: bool = True) -> None:
        self._log_update_event(
            "Closing update dialog",
            extra={"show_main": show_main, "pending_info": bool(self.pending_update_info)},
        )
        if self._update_check_start_job is not None:
            try:
                self.after_cancel(self._update_check_start_job)
            except tk.TclError:
                pass
            self._update_check_start_job = None
        if self._post_update_check_job is not None:
            try:
                self.after_cancel(self._post_update_check_job)
            except tk.TclError:
                pass
            self._post_update_check_job = None
        if self._update_auto_close_after is not None:
            try:
                self.after_cancel(self._update_auto_close_after)
            except tk.TclError:
                pass
            self._update_auto_close_after = None
        if self.update_dialog_progress is not None:
            try:
                self.update_dialog_progress.stop()
            except tk.TclError:
                pass
        if self.update_view is not None:
            try:
                self.update_view.destroy()
            except tk.TclError:
                pass
        self.update_view = None
        self.update_dialog = None
        self.update_dialog_progress = None
        self.update_button_frame = None
        self.update_primary_button = None
        self.update_secondary_button = None
        self.update_title_label = None
        self._update_message_key = None
        self._update_message_kwargs = {}
        self._update_dialog_title_key = None
        self._update_primary_button_key = None
        self._update_secondary_button_key = None
        self._update_dialog_can_close = False
        self._update_dialog_shown_at = None
        self.pending_update_info = None
        self.pending_install_result = None
        self._update_download_total = None
        if show_main:
            self._show_main_window()

    def _check_for_updates_worker(self) -> None:
        self._log_update_event(
            "Update worker started", extra={"current_version": self.app_version}
        )
        try:
            info = check_for_update(self.app_version)
        except UpdateError as exc:
            self._log_update_event(
                "Update check failed", extra={"error": str(exc)}
            )
            self.after(
                0,
                lambda: self._queue_post_update_check(
                    lambda: self._on_update_check_failed(str(exc))
                ),
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._log_update_event(
                "Unexpected update check error",
                extra={"error": str(exc)},
                include_exception=True,
            )
            self.after(
                0,
                lambda: self._queue_post_update_check(
                    lambda: self._on_update_check_failed(str(exc))
                ),
            )
        else:
            self._log_update_event(
                "Update check completed",
                extra={
                    "update_available": bool(info),
                    "latest": getattr(info, "latest_version", None),
                },
            )
            self.after(
                0,
                lambda: self._queue_post_update_check(
                    lambda: self._on_update_check_completed(info)
                ),
            )

    def _queue_post_update_check(self, callback: Callable[[], None]) -> None:
        self._update_check_finished_at = time.monotonic()
        if self._post_update_check_job is not None:
            try:
                self.after_cancel(self._post_update_check_job)
            except tk.TclError:
                pass
            self._post_update_check_job = None

        def _invoke_callback() -> None:
            self._post_update_check_job = None
            callback()

        delay_ms = 1000
        self._log_update_event(
            "Delaying post update check handling",
            extra={"delay_ms": delay_ms},
        )
        self._post_update_check_job = str(self.after(delay_ms, _invoke_callback))

    def _on_update_check_completed(self, info: Optional[UpdateInfo]) -> None:
        self._log_update_event(
            "Handling update check result", extra={"has_update": bool(info)}
        )
        if self.update_dialog_progress is not None:
            self.update_dialog_progress.stop()
            self.update_dialog_progress.configure(mode="determinate")
            self.update_dialog_progress.set(0.0)
        if info is None:
            self._set_update_dialog_title("update_check_title")
            self._set_update_dialog_message(
                "update_check_no_updates", version=self.app_version
            )
            self._configure_update_dialog_buttons()
            self._set_update_dialog_closable(False)
            self._schedule_update_dialog_close()
            return

        self.pending_update_info = info
        if not info.asset_url:
            self._set_update_dialog_title("update_available_title")
            self._set_update_dialog_message(
                "update_available_manual", latest=info.latest_version
            )
            self._configure_update_dialog_buttons(
                primary=(
                    "update_button_open_page",
                    lambda url=info.release_page: self._on_open_release_page(url),
                ),
                secondary=("update_button_later", lambda: self._close_update_dialog(True)),
            )
            self._set_update_dialog_closable(True)
            return

        self._set_update_dialog_title("update_available_title")
        self._set_update_dialog_message(
            "update_available_message",
            latest=info.latest_version,
            current=self.app_version,
        )
        self._configure_update_dialog_buttons(
            primary=("update_button_install", lambda data=info: self._start_update_download(data)),
            secondary=("update_button_later", lambda: self._close_update_dialog(True)),
        )
        self._set_update_dialog_closable(True)

    def _on_update_check_failed(self, error_message: str) -> None:
        self._log_update_event(
            "Displaying update check failure", extra={"error": error_message}
        )
        if self.update_dialog_progress is not None:
            self.update_dialog_progress.stop()
            self.update_dialog_progress.configure(mode="determinate")
            self.update_dialog_progress.set(0.0)
        self._set_update_dialog_title("update_error_title")
        self._set_update_dialog_message("update_check_failed", error=error_message)
        self._configure_update_dialog_buttons()
        self._set_update_dialog_closable(False)
        self._schedule_update_dialog_close()

    def _start_update_download(self, info: UpdateInfo) -> None:
        self._log_update_event(
            "Starting update download",
            extra={
                "version": info.latest_version,
                "asset": info.asset_name,
                "has_url": bool(info.asset_url),
            },
        )
        self.pending_update_info = info
        self.pending_install_result = None
        self._update_download_total = None
        if self.update_dialog_progress is not None:
            self.update_dialog_progress.stop()
            self.update_dialog_progress.configure(mode="indeterminate")
            self.update_dialog_progress.start()
        self._set_update_dialog_title("update_download_title")
        self._set_update_dialog_message(
            "update_download_preparing", version=info.latest_version
        )
        self._configure_update_dialog_buttons()
        self._set_update_dialog_closable(False)
        threading.Thread(
            target=self._download_update_worker,
            args=(info,),
            daemon=True,
        ).start()

    def _handle_download_progress(self, downloaded: int, total: Optional[int]) -> None:
        self.after(0, lambda: self._update_download_progress_ui(downloaded, total))

    def _update_download_progress_ui(self, downloaded: int, total: Optional[int]) -> None:
        if self.update_dialog_progress is None:
            return
        if total and total > 0:
            if self.update_dialog_progress.cget("mode") != "determinate":
                self.update_dialog_progress.stop()
                self.update_dialog_progress.configure(mode="determinate")
            self._update_download_total = total
            value = min(downloaded, total)
            progress_ratio = value / total if total else 0.0
            self.update_dialog_progress.set(progress_ratio)
            percent = min(int(round(progress_ratio * 100)), 100)
            self._set_update_dialog_message("update_download_progress", percent=percent)
        else:
            if self.update_dialog_progress.cget("mode") != "indeterminate":
                self.update_dialog_progress.configure(mode="indeterminate")
                self.update_dialog_progress.start()
            if self.pending_update_info is not None:
                self._set_update_dialog_message(
                    "update_download_preparing",
                    version=self.pending_update_info.latest_version,
                )

    def _download_update_worker(self, info: UpdateInfo) -> None:
        self._log_update_event(
            "Download worker started",
            extra={
                "version": info.latest_version,
                "asset": info.asset_name,
                "has_url": bool(info.asset_url),
            },
        )
        try:
            download_path = download_update_asset(
                info,
                self.update_cache_dir,
                progress_callback=self._handle_download_progress,
            )
            result = install_downloaded_asset(
                download_path, info.latest_version, self.update_cache_dir
            )
        except UpdateError as exc:
            self._log_update_event("Download failed", extra={"error": str(exc)})
            self.after(0, lambda: self._on_update_install_failed(str(exc), info))
        except Exception as exc:  # pylint: disable=broad-except
            self._log_update_event(
                "Unexpected download error",
                extra={"error": str(exc)},
                include_exception=True,
            )
            self.after(0, lambda: self._on_update_install_failed(str(exc), info))
        else:
            self._log_update_event(
                "Download completed",
                extra={
                    "version": info.latest_version,
                    "downloaded_path": str(download_path),
                },
            )
            self.after(0, lambda: self._on_update_install_succeeded(result))

    def _on_update_install_succeeded(self, result: InstallResult) -> None:
        self._log_update_event(
            "Installation succeeded",
            extra={
                "version": result.version,
                "executable": str(result.executable) if result.executable else None,
            },
        )
        self.pending_install_result = result
        self.pending_update_info = None
        self._update_download_total = None
        self._pending_updater_command = None
        if self.update_dialog_progress is not None:
            self.update_dialog_progress.stop()
            self.update_dialog_progress.configure(mode="determinate")
            self.update_dialog_progress.set(1.0)
        self._set_update_dialog_title("update_install_title")

        launched = False
        launch_error: Optional[Exception] = None
        if result.executable is not None:
            if self._prepare_self_update(result):
                return
            try:
                self._launch_file(result.executable)
                launched = True
            except Exception as exc:  # pylint: disable=broad-except
                launch_error = exc

        if launched:
            self._set_update_dialog_message(
                "update_install_success_launched", version=result.version
            )
            self._configure_update_dialog_buttons(
                primary=("update_button_exit", self._exit_after_update),
            )
            self._set_update_dialog_closable(True)
            self.after(4000, self._exit_after_update)
            return

        if launch_error is not None:
            self._set_update_dialog_message(
                "update_install_launch_failed",
                version=result.version,
                path=str(result.base_path),
                error=launch_error,
            )
        else:
            self._set_update_dialog_message(
                "update_install_success_manual",
                version=result.version,
                path=str(result.base_path),
            )
        self._configure_update_dialog_buttons(
            primary=(
                "update_button_open_folder",
                lambda target=result.base_path: self._open_directory(target),
            ),
            secondary=("update_button_continue", lambda: self._close_update_dialog(True)),
        )
        self._set_update_dialog_closable(True)
        self.after(200, lambda target=result.base_path: self._open_directory(target))

    def _on_update_install_failed(self, error_message: str, info: UpdateInfo) -> None:
        self._log_update_event(
            "Installation failed",
            extra={"error": error_message, "version": info.latest_version},
        )
        self.pending_update_info = info
        self.pending_install_result = None
        self._update_download_total = None
        if self.update_dialog_progress is not None:
            self.update_dialog_progress.stop()
            self.update_dialog_progress.configure(mode="determinate")
            self.update_dialog_progress.set(0.0)
        self._set_update_dialog_title("update_error_title")
        self._set_update_dialog_message("update_install_failed", error=error_message)
        self._configure_update_dialog_buttons(
            primary=("update_button_retry", lambda data=info: self._start_update_download(data)),
            secondary=("update_button_continue", lambda: self._close_update_dialog(True)),
        )
        self._set_update_dialog_closable(True)

    def _schedule_update_dialog_close(self, delay: int = 1200) -> None:
        if self.update_dialog is None:
            return

        self._log_update_event("Scheduling update dialog close", extra={"delay_ms": delay})

        def _close_if_pending() -> None:
            self._update_auto_close_after = None
            if self.update_dialog is not None:
                self._close_update_dialog(True)

        min_visible_ms = 1000
        if self._update_dialog_shown_at is not None:
            elapsed_ms = int((time.monotonic() - self._update_dialog_shown_at) * 1000)
            remaining_to_min = max(0, min_visible_ms - elapsed_ms)
            delay = max(delay, remaining_to_min)

        if self._update_auto_close_after is not None:
            try:
                self.after_cancel(self._update_auto_close_after)
            except tk.TclError:
                pass
            self._update_auto_close_after = None

        try:
            self._update_auto_close_after = str(self.after(delay, _close_if_pending))
        except tk.TclError:
            _close_if_pending()

    def _exit_after_update(self) -> None:
        self._close_update_dialog(show_main=False)
        self.after(50, self.destroy)

    def _can_self_update(self, result: InstallResult) -> bool:
        return (
            sys.platform.startswith("win")
            and getattr(sys, "frozen", False)
            and result.executable is not None
        )

    def _build_self_update_command(self, result: InstallResult) -> Optional[list[str]]:
        if not self._can_self_update(result):
            return None
        current_executable = Path(sys.executable).resolve()
        if not current_executable.exists():
            return None
        try:
            relaunch_helper = current_executable.with_name(
                f"{current_executable.stem}-relauncher{current_executable.suffix}"
            )
            if not relaunch_helper.exists():
                self._log_update_event(
                    "Relaunch helper not found, falling back to direct relaunch",
                    extra={"expected_path": str(relaunch_helper)},
                )
                relaunch_helper = None
            command = build_updater_command(
                result.executable,
                current_executable,
                current_executable,
                sys.argv[1:],
                self.update_log_path,
                wait_before=0.5,
                max_wait=120.0,
                relaunch_helper=relaunch_helper,
                relaunch_wait=0.5,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._log_update_event(
                "Failed to build self-update command", extra={"error": str(exc)}
            )
            return None
        return command

    def _prepare_self_update(self, result: InstallResult) -> bool:
        if not self._can_self_update(result):
            return False
        command = self._build_self_update_command(result)
        if not command:
            return False
        self._pending_updater_command = command
        self._set_update_dialog_message(
            "update_install_ready_restart", version=result.version
        )
        self._configure_update_dialog_buttons(
            primary=("update_button_restart", self._run_self_update_now),
            secondary=("update_button_continue", lambda: self._close_update_dialog(True)),
        )
        self._set_update_dialog_closable(True)
        return True

    def _run_self_update_now(self) -> None:
        if not self.pending_install_result or not self._pending_updater_command:
            return
        result = self.pending_install_result
        try:
            subprocess.Popen(self._pending_updater_command, close_fds=False)
        except Exception as exc:  # pylint: disable=broad-except
            self._log_update_event(
                "Failed to launch self-update helper",
                extra={"error": str(exc)},
            )
            self._pending_updater_command = None
            self._set_update_dialog_message(
                "update_install_self_update_failed",
                version=result.version,
                path=str(result.base_path),
                error=exc,
            )
            self._configure_update_dialog_buttons(
                primary=(
                    "update_button_open_folder",
                    lambda target=result.base_path: self._open_directory(target),
                ),
                secondary=("update_button_continue", lambda: self._close_update_dialog(True)),
            )
            self._set_update_dialog_closable(True)
            return

        self._log_update_event(
            "Self-update helper launched",
            extra={"command": self._pending_updater_command},
        )
        self._set_update_dialog_message(
            "update_install_restarting", version=result.version
        )
        self._configure_update_dialog_buttons()
        self._set_update_dialog_closable(False)
        self.after(400, self._exit_after_update)

    def _launch_file(self, path: Path) -> None:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], close_fds=True)
        else:
            subprocess.Popen([str(path)], close_fds=True)

    def _open_directory(self, path: Path) -> None:
        target = path if path.is_dir() else path.parent
        if not target.exists():
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(target)], close_fds=True)
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror(
                self._("error_root_title"),
                self._("error_open_folder_failed", error=exc),
            )

    def _on_open_release_page(self, url: str) -> None:
        try:
            webbrowser.open(url)
        finally:
            self._close_update_dialog(True)



def main() -> None:
    exit_code = maybe_run_updater()
    if exit_code is not None:
        raise SystemExit(exit_code)
    app = DownloaderUI()
    app.mainloop()
