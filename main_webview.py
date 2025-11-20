"""PyWebView-based entrypoint for YT Downloader.

This module exposes a JavaScript bridge that forwards UI actions to the
existing download worker implementation while streaming worker events back
into the webview via ``handlePyEvent``.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

import webview

from yt_downloader.backend import fetch_video_metadata
from yt_downloader.localization import DEFAULT_LANGUAGE
from yt_downloader.utils import (
    format_timestamp,
    is_supported_video_url,
    resolve_asset_path,
)
from yt_downloader.version import __version__
from yt_downloader.worker import DownloadWorker


class Bridge:
    """JavaScript API exposed to the web frontend."""

    CONFIG_DIR = Path.home() / "Documents" / "YT Downloader Settings"
    SETTINGS_FILE = CONFIG_DIR / "settings.json"
    DEFAULT_ROOT = Path.home() / "Videos" / "Downloaded Videos"

    def __init__(self, window: Optional["webview.Window"] = None) -> None:
        self.window = window
        self.event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.workers: dict[str, DownloadWorker] = {}
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._dispatch_events, daemon=True
        )
        self._monitor_thread.start()
        self._lock = threading.Lock()

        self.settings = self._load_settings()
        self.root_folder = Path(self.settings.get("root_folder", self.DEFAULT_ROOT)).expanduser()
        self._ensure_root_folder(self.root_folder)

    def get_init_data(self) -> dict[str, str]:
        """Return static data used to initialize the UI."""

        return {
            "version": __version__,
            "root_folder": str(self.root_folder),
            "convert_mp4": bool(self.settings.get("convert_mp4", True)),
            "sequential": bool(self.settings.get("sequential", False)),
        }

    def fetch_metadata(self, url: str) -> dict[str, Any]:
        """Fetch video metadata for the given URL."""

        if not is_supported_video_url(url):
            return {"status": "error", "error": "Invalid URL"}

        try:
            meta = fetch_video_metadata(url)
            duration_raw = meta.get("duration") or 0
            try:
                duration_seconds = int(float(duration_raw))
            except (TypeError, ValueError):
                duration_seconds = 0

            return {
                "status": "ok",
                "title": meta.get("title"),
                "duration": duration_seconds,
                "duration_str": format_timestamp(duration_seconds) if duration_seconds else "00:00",
                "thumbnail": meta.get("thumbnail"),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            return {"status": "error", "error": str(exc)}

    def minimize_window(self) -> None:
        """Minimize the application window."""

        if not self.window:
            return
        try:
            self.window.minimize()
        except Exception:
            # Minimize is not supported on all platforms; ignore failures.
            return

    def close_window(self) -> None:
        """Close the application window and stop workers."""

        self.shutdown()
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            return

    def select_folder(self) -> str:
        """Open a native folder selection dialog and return the chosen path."""

        dialog_window = self.window if self.window else webview.windows[0]
        try:
            result = dialog_window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            return ""

        if not result:
            return ""

        if isinstance(result, (list, tuple)):
            selected = str(result[0])
        else:
            selected = str(result)

        if selected:
            self.root_folder = Path(selected).expanduser()
            self._ensure_root_folder(self.root_folder)
            self.settings["root_folder"] = str(self.root_folder)
            self._save_settings()

        return selected

    def start_download(
        self,
        url: str,
        folder: str,
        options: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> dict[str, str]:
        """Validate input and start a new DownloadWorker."""

        options = options or {}
        if not is_supported_video_url(url):
            return {"status": "error", "error": "Invalid URL"}

        task_id = str(uuid.uuid4())
        root_folder = self.root_folder
        separate_folder = False
        convert_to_mp4 = bool(options.get("mp4", True))
        start_seconds = float(options.get("start_seconds", 0.0) or 0.0)
        end_seconds_value = options.get("end_seconds")
        end_seconds = float(end_seconds_value) if end_seconds_value is not None else None

        worker = DownloadWorker(
            task_id=task_id,
            url=url,
            root=root_folder,
            title=title,
            separate_folder=separate_folder,
            convert_to_mp4=convert_to_mp4,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            event_queue=self.event_queue,
            language=DEFAULT_LANGUAGE,
        )

        with self._lock:
            self.workers[task_id] = worker
        worker.start()
        return {"status": "ok", "task_id": task_id}

    def open_path(self, path: str) -> None:
        """Open the given file or folder in the native file explorer."""

        if not path:
            return
        target = Path(path).expanduser()
        if not target.exists():
            return

        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])  # noqa: S603
        else:
            subprocess.Popen(["xdg-open", str(target)])  # noqa: S603

    def cancel_download(self, task_id: str) -> dict[str, str]:
        """Request cancellation of a running worker."""

        with self._lock:
            worker = self.workers.get(task_id)
        if worker is None:
            return {"status": "error", "error": "Task not found"}

        worker.cancel()
        return {"status": "ok", "task_id": task_id}

    def shutdown(self) -> None:
        """Stop the monitor thread and cancel active workers."""

        if not self._running:
            return
        self._running = False
        with self._lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.cancel()
        self.event_queue.put(None)
        for worker in workers:
            worker.join(timeout=1.0)
        if self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)

    def _dispatch_events(self) -> None:
        while self._running:
            try:
                event = self.event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if event is None:
                break

            if event.get("type") == "finished":
                task_id = str(event.get("task_id", ""))
                with self._lock:
                    worker = self.workers.get(task_id)
                    if worker and not worker.is_alive():
                        self.workers.pop(task_id, None)

            if self.window:
                try:
                    payload = json.dumps(event, ensure_ascii=False)
                    self.window.evaluate_js(
                        f"window.handlePyEvent && window.handlePyEvent({payload});"
                    )
                except Exception:
                    continue

    def _load_settings(self) -> dict[str, Any]:
        """Load persisted settings or return defaults if missing."""

        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        default_root = self.DEFAULT_ROOT
        self._ensure_root_folder(default_root)
        defaults = {
            "root_folder": str(default_root),
            "convert_mp4": True,
            "sequential": False,
        }

        if not self.SETTINGS_FILE.exists():
            self._save_settings_data(defaults)
            return defaults

        try:
            loaded: dict[str, Any] = json.loads(
                self.SETTINGS_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            self._save_settings_data(defaults)
            return defaults

        if not isinstance(loaded, dict):
            self._save_settings_data(defaults)
            return defaults

        merged = {**defaults, **loaded}
        merged["root_folder"] = str(
            Path(merged.get("root_folder", default_root)).expanduser()
        )
        self._ensure_root_folder(Path(merged["root_folder"]))
        self._save_settings_data(merged)
        return merged

    def _save_settings(self) -> None:
        """Persist current settings to disk."""

        self._save_settings_data(self.settings)

    def _save_settings_data(self, data: dict[str, Any]) -> None:
        try:
            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self.SETTINGS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            return

    def _ensure_root_folder(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return


def _resolve_web_path() -> str:
    html_path = resolve_asset_path("web/index.html")
    if html_path is None:
        raise FileNotFoundError("Unable to locate web/index.html")
    return html_path.as_uri()


def main() -> None:
    html_uri = _resolve_web_path()

    # 1. Create Bridge without window
    bridge = Bridge(window=None)

    # 2. Create window passing bridge via js_api
    window = webview.create_window(
        "YT Downloader",
        html_uri,
        width=1200,
        height=850,
        min_size=(960, 640),
        js_api=bridge,
    )

    # 3. Attach created window back to bridge
    bridge.window = window

    window.events.closed += bridge.shutdown

    # 4. Expose handled via js_api
    webview.start()


if __name__ == "__main__":
    main()
