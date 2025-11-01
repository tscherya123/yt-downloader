"""Reusable Tkinter widgets for the downloader UI."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import ttk

from .utils import shorten_title


class TaskRow(ttk.Frame):
    """Row widget representing a single download task."""

    def __init__(
        self,
        master: tk.Misc,
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
    ) -> None:
        super().__init__(master, padding=(12, 8), style="TaskRow.TFrame")
        self.task_id = task_id
        self.full_title = title
        self.display_title = shorten_title(title)
        self.translate = translator
        self.status_code = status
        self.status_var = tk.StringVar()
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

        self.title_label = ttk.Label(
            self,
            text=self.display_title,
            style="TaskTitle.TLabel",
            anchor="w",
            justify="left",
        )
        self.status_label = ttk.Label(
            self,
            textvariable=self.status_var,
            style="TaskStatus.TLabel",
            anchor="w",
            justify="left",
        )
        self.actions_frame = ttk.Frame(self, style="TaskActions.TFrame")
        self.cancel_button = ttk.Button(
            self.actions_frame,
            text=self.translate("button_cancel"),
            command=self._cancel_task,
        )
        self.open_button = ttk.Button(
            self.actions_frame,
            text=self.translate("button_open_folder"),
            command=self._open_folder,
            state="disabled",
        )
        self.open_link_button = ttk.Button(
            self.actions_frame,
            text="»",
            width=3,
            command=self._open_source_url,
            takefocus=False,
        )
        self.retry_button = ttk.Button(
            self.actions_frame,
            text="↻",
            width=3,
            command=self._trigger_retry,
            takefocus=False,
        )
        self.remove_button = ttk.Button(
            self.actions_frame,
            text="×",
            width=3,
            command=self._remove_from_history,
            takefocus=False,
        )

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.columnconfigure(2, weight=0)
        self.title_label.grid(row=0, column=0, sticky="w")
        self.status_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.actions_frame.grid(row=0, column=2, sticky="e")
        self.actions_frame.columnconfigure(0, weight=0)

        self.cancel_button.grid(row=0, column=0, padx=(0, 6))
        self.open_button.grid(row=0, column=1, padx=(0, 6))
        self.open_link_button.grid(row=0, column=2, padx=(0, 6))
        self.retry_button.grid(row=0, column=3, padx=(0, 6))
        self.remove_button.grid(row=0, column=4)

        self.cancel_button.state(["disabled"])
        self.open_button.state(["disabled"])
        self.open_link_button.state(["disabled"])
        self.retry_button.state(["disabled"])
        self.remove_button.state(["disabled"])

        self.cancel_button.grid_remove()
        self.open_button.grid_remove()
        self.open_link_button.grid_remove()
        self.retry_button.grid_remove()
        self.remove_button.grid_remove()

        self._update_status_text()
        self._update_actions()

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
        self.cancel_button.configure(text=self.translate("button_cancel"))
        self.open_button.configure(text=self.translate("button_open_folder"))
        self._update_status_text()
        self._update_actions()

    def mark_cancelling(self) -> None:
        self.cancel_button.state(["disabled"])

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
        show_cancel = (
            self.status_code in {"downloading", "converting"}
            and self._cancel_callback is not None
        )
        if show_cancel:
            self.cancel_button.state(["!disabled"])
            self.cancel_button.grid()
        else:
            self.cancel_button.state(["disabled"])
            self.cancel_button.grid_remove()

        show_open = self.status_code == "done"
        if show_open:
            if self.final_path is not None:
                self.open_button.state(["!disabled"])
            else:
                self.open_button.state(["disabled"])
            self.open_button.grid()
        else:
            self.open_button.state(["disabled"])
            self.open_button.grid_remove()

        show_open_link = bool(self.source_url and self._open_url_callback)
        if show_open_link:
            self.open_link_button.state(["!disabled"])
            self.open_link_button.grid()
        else:
            self.open_link_button.state(["disabled"])
            self.open_link_button.grid_remove()

        show_retry = (
            self._retry_callback is not None
            and self.source_url
            and self.status_code in {"error", "cancelled", "waiting"}
        )
        if show_retry:
            self.retry_button.state(["!disabled"])
            self.retry_button.grid()
        else:
            self.retry_button.state(["disabled"])
            self.retry_button.grid_remove()

        show_remove = (
            self._remove_callback is not None
            and self.status_code not in {"downloading", "converting"}
        )
        if show_remove:
            self.remove_button.state(["!disabled"])
            self.remove_button.grid()
        else:
            self.remove_button.state(["disabled"])
            self.remove_button.grid_remove()
