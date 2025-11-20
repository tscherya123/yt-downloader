from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from .utils import shorten_title


class DownloadItem(ctk.CTkFrame):
    """Modern download row styled to match the new Fluent-inspired UI."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        *,
        task_id: str,
        title: str,
        open_callback: Callable[[Path], None],
        translator: Callable[..., str],
        cancel_callback: Optional[Callable[[str], None]] = None,
        remove_callback: Optional[Callable[[str], None]] = None,
        open_url_callback: Optional[Callable[[str], None]] = None,
        retry_callback: Optional[Callable[[str, str], None]] = None,
        source_url: Optional[str] = None,
        status: str = "waiting",
        final_path: Optional[Path] = None,
        palette: Optional[dict[str, str]] = None,
        title_font: Optional[ctk.CTkFont] = None,
        status_font: Optional[ctk.CTkFont] = None,
    ) -> None:
        super().__init__(master, corner_radius=12, border_width=1)
        self.task_id = task_id
        self.full_title = title
        self.display_title = shorten_title(title)
        self.translate = translator
        self.status_code = status
        self.status_var = ctk.StringVar()
        self._open_callback = open_callback
        self._cancel_callback = cancel_callback
        self._remove_callback = remove_callback
        self._open_url_callback = open_url_callback
        self._retry_callback = retry_callback
        if isinstance(source_url, str):
            stripped = source_url.strip()
            self.source_url = stripped or None
        else:
            self.source_url = None
        self.final_path: Optional[Path] = final_path

        self.palette = palette or {}
        self._button_normal_color: Optional[str] = None
        self._button_hover_color: Optional[str] = None
        self._button_disabled_color: Optional[str] = None
        self._button_text_color: Optional[str] = None
        self._button_disabled_text: Optional[str] = None

        self.columnconfigure(1, weight=1)

        self.icon_frame = ctk.CTkFrame(self, width=48, height=48, corner_radius=10)
        self.icon_frame.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=12, pady=12)
        self.icon_frame.grid_propagate(False)
        self.icon_label = ctk.CTkLabel(self.icon_frame, text="🎬", font=("Roboto", 18, "bold"))
        self.icon_label.place(relx=0.5, rely=0.5, anchor="center")

        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=1, sticky="nsew", pady=(12, 0), padx=(0, 12))
        info_frame.columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            info_frame,
            text=self.display_title,
            anchor="w",
            justify="left",
        )
        if title_font is not None:
            self.title_label.configure(font=title_font)
        self.title_label.grid(row=0, column=0, sticky="ew")

        self.status_label = ctk.CTkLabel(
            info_frame,
            textvariable=self.status_var,
            anchor="w",
            justify="left",
        )
        if status_font is not None:
            self.status_label.configure(font=status_font)
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        self.progress = ctk.CTkProgressBar(self, height=6)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 12))
        self.progress.set(0.0)

        self.actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.actions_frame.grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 12))
        for index in range(5):
            self.actions_frame.columnconfigure(index, weight=0)

        self.pause_button = ctk.CTkButton(
            self.actions_frame,
            text="❚❚",
            width=36,
            command=self._cancel_task,
        )
        self.cancel_button = ctk.CTkButton(
            self.actions_frame,
            text="×",
            width=36,
            command=self._remove_from_history,
        )
        self.open_button = ctk.CTkButton(
            self.actions_frame,
            text="⬓",
            width=36,
            command=self._open_folder,
        )
        self.open_link_button = ctk.CTkButton(
            self.actions_frame,
            text="↗",
            width=36,
            command=self._open_source_url,
        )
        self.retry_button = ctk.CTkButton(
            self.actions_frame,
            text="↻",
            width=36,
            command=self._trigger_retry,
        )

        self.pause_button.grid(row=0, column=0, padx=(0, 6))
        self.cancel_button.grid(row=0, column=1, padx=(0, 6))
        self.open_button.grid(row=0, column=2, padx=(0, 6))
        self.open_link_button.grid(row=0, column=3, padx=(0, 6))
        self.retry_button.grid(row=0, column=4)

        self._update_status_text()
        self._update_actions()
        self.apply_palette(self.palette)

    def update_status(self, status: str) -> None:
        self.status_code = status
        self._update_status_text()
        self._update_actions()

    def set_final_path(self, path: Path) -> None:
        self.final_path = path
        self._update_actions()

    def set_source_url(self, url: Optional[str]) -> None:
        if isinstance(url, str):
            stripped = url.strip()
            self.source_url = stripped or None
        else:
            self.source_url = None
        self._update_actions()

    def set_title(self, title: str) -> None:
        self.full_title = title
        self.display_title = shorten_title(title)
        self.title_label.configure(text=self.display_title)

    def retranslate(self, translator: Callable[..., str]) -> None:
        self.translate = translator
        self._update_status_text()
        self._update_actions()

    def mark_cancelling(self) -> None:
        self._set_button_state(self.pause_button, False)

    def _cancel_task(self) -> None:
        if self._cancel_callback is None:
            return
        self._cancel_callback(self.task_id)

    def _remove_from_history(self) -> None:
        if self._remove_callback is None:
            return
        self._remove_callback(self.task_id)

    def _open_folder(self) -> None:
        if self.final_path is None:
            return
        self._open_callback(self.final_path)

    def _open_source_url(self) -> None:
        if not self.source_url or self._open_url_callback is None:
            return
        self._open_url_callback(self.source_url)

    def _trigger_retry(self) -> None:
        if not self.source_url or self._retry_callback is None:
            return
        self._retry_callback(self.task_id, self.source_url)

    def _update_status_text(self) -> None:
        status_text = self.translate(f"status_{self.status_code}")
        self.status_var.set(self.translate("status_prefix", status=status_text))

    def _update_actions(self) -> None:
        show_pause = self.status_code in {"downloading", "converting"} and self._cancel_callback
        self.pause_button.grid() if show_pause else self.pause_button.grid_remove()

        show_cancel = (
            self._remove_callback is not None
            and self.status_code not in {"downloading", "converting"}
        )
        self.cancel_button.grid() if show_cancel else self.cancel_button.grid_remove()

        show_open = self.status_code == "done" and self.final_path is not None
        self.open_button.grid() if show_open else self.open_button.grid_remove()

        show_open_link = bool(self.source_url and self._open_url_callback)
        self.open_link_button.grid() if show_open_link else self.open_link_button.grid_remove()

        show_retry = (
            self._retry_callback is not None
            and self.source_url
            and self.status_code in {"error", "cancelled", "waiting"}
        )
        self.retry_button.grid() if show_retry else self.retry_button.grid_remove()

        for button in (
            self.pause_button,
            self.cancel_button,
            self.open_button,
            self.open_link_button,
            self.retry_button,
        ):
            self._set_button_state(button, str(button.cget("state")) != "disabled")

    def apply_palette(self, palette: dict[str, str]) -> None:
        """Update colors to match the active application palette."""

        self.palette = palette
        if not palette:
            return
        surface = palette.get("surface")
        text = palette.get("text")
        muted = palette.get("muted")
        accent = palette.get("accent")
        hover = palette.get("accent_hover", accent)
        disabled = palette.get("disabled")
        highlight = palette.get("highlight", surface)
        button_text = palette.get("button_text", text)

        if surface:
            self.configure(fg_color=surface, border_color=palette.get("border", highlight))
            self.actions_frame.configure(fg_color=surface)
        if text:
            self.title_label.configure(text_color=text)
        if muted:
            self.status_label.configure(text_color=muted)
            self.icon_label.configure(text_color=muted)
        if highlight:
            self.icon_frame.configure(fg_color=palette.get("input", highlight))

        base_accent = accent or self._button_normal_color or "#6366f1"
        base_hover = hover or base_accent
        base_disabled_bg = palette.get("button_disabled", disabled or base_accent)
        base_text = button_text or self._button_text_color or text or "#ffffff"
        base_disabled_text = (
            palette.get("button_disabled_text")
            or self._button_disabled_text
            or muted
            or "#6f6f6f"
        )

        self.progress.configure(
            fg_color=palette.get("input", surface),
            progress_color=base_accent,
        )

        self._button_normal_color = base_accent
        self._button_hover_color = base_hover
        self._button_disabled_color = base_disabled_bg
        self._button_text_color = base_text
        self._button_disabled_text = base_disabled_text

        for button in (
            self.pause_button,
            self.cancel_button,
            self.open_button,
            self.open_link_button,
            self.retry_button,
        ):
            configure_kwargs: dict[str, object] = {"border_width": 0}
            configure_kwargs["text_color_disabled"] = base_disabled_text
            button.configure(**configure_kwargs)
            self._set_button_state(
                button,
                str(button.cget("state")) != "disabled",
            )

    def _set_button_state(self, button: ctk.CTkButton, enabled: bool) -> None:
        normal = self._button_normal_color or button.cget("fg_color")
        hover = self._button_hover_color or normal
        disabled_bg = self._button_disabled_color or normal
        disabled_text = self._button_disabled_text or button.cget("text_color_disabled")
        text_color = self._button_text_color or button.cget("text_color")
        if disabled_text:
            button.configure(text_color_disabled=disabled_text)
        if enabled:
            button.configure(
                state="normal",
                fg_color=normal,
                hover_color=hover,
                text_color=text_color,
            )
        else:
            button.configure(
                state="disabled",
                fg_color=disabled_bg,
                hover_color=disabled_bg,
                text_color_disabled=disabled_text,
            )


# Backward compatibility for existing imports
TaskRow = DownloadItem


__all__ = ["DownloadItem", "TaskRow"]
