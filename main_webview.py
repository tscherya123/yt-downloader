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

from yt_downloader.localization import DEFAULT_LANGUAGE
from yt_downloader.utils import is_supported_video_url, resolve_asset_path
from yt_downloader.worker import DownloadWorker


class Bridge:
    """JavaScript API exposed to the web frontend."""

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
            return str(result[0])
        return str(result)

    def start_download(
        self, url: str, folder: str, options: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Validate input and start a new DownloadWorker."""

        options = options or {}
        if not is_supported_video_url(url):
            return {"status": "error", "error": "Invalid URL"}

        task_id = str(uuid.uuid4())
        root_folder = Path(folder).expanduser() if folder else Path.home() / "Downloads"
        separate_folder = bool(options.get("separate_folder"))
        convert_to_mp4 = bool(options.get("mp4", True))
        start_seconds = float(options.get("start_seconds", 0.0) or 0.0)
        end_seconds_value = options.get("end_seconds")
        end_seconds = float(end_seconds_value) if end_seconds_value is not None else None

        worker = DownloadWorker(
            task_id=task_id,
            url=url,
            root=root_folder,
            title=None,
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
        height=760,
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
