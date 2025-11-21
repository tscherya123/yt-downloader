"""PyWebView-based entrypoint for YT Downloader.

This module exposes a JavaScript bridge that forwards UI actions to the
existing download worker implementation while streaming worker events back
into the webview via ``handlePyEvent``.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Optional

import webview

from yt_downloader.backend import fetch_video_metadata
from yt_downloader.logger import setup_logging
from yt_downloader.localization import DEFAULT_LANGUAGE
from yt_downloader.updater import cleanup_old_versions, install_update_and_restart
from yt_downloader.updates import (
    UpdateError,
    UpdateInfo,
    check_for_update,
    download_update_asset,
    install_downloaded_asset,
)
from yt_downloader.utils import (
    format_timestamp,
    is_supported_video_url,
    resolve_asset_path,
)
from yt_downloader.version import __version__
from yt_downloader.worker import DownloadWorker

CONFIG_DIR = Path.home() / "Documents" / "YT Downloader Settings"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
QUEUE_FILE = CONFIG_DIR / "download_queue.json"
DEFAULT_ROOT = Path.home() / "Videos" / "Downloaded Videos"


class Bridge:
    """JavaScript API exposed to the web frontend."""

    UPDATE_CHECK_TIMEOUT = 15.0
    UPDATE_NETWORK_TIMEOUT = 10.0

    def __init__(self, window: Optional["webview.Window"] = None) -> None:
        self.window = window
        self._event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._workers: dict[str, DownloadWorker] = {}
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._dispatch_events, daemon=True
        )
        self._monitor_thread.start()
        self._lock = threading.Lock()

        self.settings = self._load_settings()
        root_path = Path(self.settings.get("root_folder", DEFAULT_ROOT)).expanduser().resolve()
        self._ensure_root_folder(root_path)
        # Store paths as strings to avoid pywebview serialization issues
        self.root_folder = str(root_path)
        self.queue_items = self._load_queue()
        self._waiting_queue: list[dict[str, Any]] = []
        self.update_status = "checking"
        self.pending_update_info: UpdateInfo | None = None
        self.update_cache_dir = str(CONFIG_DIR / "updates")
        cleanup_old_versions()

    def __getstate__(self) -> dict[str, Any]:
        """Hide non-serializable internals from potential inspection/serialization."""

        state = self.__dict__.copy()
        for key in [
            "_event_queue",
            "_monitor_thread",
            "_workers",
            "_waiting_queue",
            "_lock",
        ]:
            state.pop(key, None)
        return state

    def get_init_data(self) -> dict[str, Any]:
        """Return static data used to initialize the UI."""

        try:
            threading.Thread(target=self.check_updates, daemon=True).start()
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            self.update_status = "error"
            self._emit_update_event(
                {"type": "update_error", "error": f"Failed to start update check: {exc}"}
            )

        return {
            "version": __version__,
            "settings": {
                "root_folder": str(Path(self.root_folder).resolve()),
                "mp4": bool(self.settings.get("mp4", True)),
                "sequential": bool(self.settings.get("sequential", False)),
            },
            "history": self.queue_items,
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

    def toggle_fullscreen(self) -> None:
        """Toggle fullscreen/maximized state."""

        if not self.window:
            return
        try:
            self.window.toggle_fullscreen()
        except Exception:
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
            selected_raw = result[0]
        else:
            selected_raw = result

        if not selected_raw:
            return ""

        selected_path = Path(str(selected_raw)).expanduser().resolve()
        self.root_folder = str(selected_path)
        self._ensure_root_folder(selected_path)
        self.settings["root_folder"] = str(selected_path)
        self._save_settings()

        return str(selected_path)

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
        root_folder_path = Path(self.root_folder)
        root_folder_str = str(root_folder_path)
        self._ensure_root_folder(root_folder_path)
        separate_folder = False
        convert_to_mp4 = bool(options.get("mp4", self.settings.get("mp4", True)))
        sequential_download = bool(options.get("sequential", self.settings.get("sequential", False)))
        start_seconds = float(options.get("start_seconds", 0.0) or 0.0)
        end_seconds_value = options.get("end_seconds")
        end_seconds = float(end_seconds_value) if end_seconds_value is not None else None

        self.settings["mp4"] = convert_to_mp4
        self.settings["sequential"] = sequential_download
        self.settings["root_folder"] = root_folder_str
        self._save_settings()

        worker_args = {
            "task_id": task_id,
            "url": url,
            "root": root_folder_str,
            "title": title,
            "separate_folder": separate_folder,
            "convert_to_mp4": convert_to_mp4,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
        }

        with self._lock:
            active_workers = len(self._workers)

        if sequential_download and (active_workers > 0 or self._waiting_queue):
            with self._lock:
                self._waiting_queue.append(worker_args)
            self._add_queue_item(
                task_id,
                url=url,
                title=title or url,
                status="queued",
                path="",
                error="",
            )
            return {"status": "ok", "task_id": task_id, "queued": True}

        self._add_queue_item(
            task_id,
            url=url,
            title=title or url,
            status="downloading",
            path="",
            error="",
        )
        self._start_worker(worker_args)
        return {"status": "ok", "task_id": task_id}

    def _start_worker(self, worker_args: dict[str, Any]) -> DownloadWorker:
        root_path = Path(worker_args["root"])
        worker = DownloadWorker(
            task_id=worker_args["task_id"],
            url=worker_args["url"],
            root=root_path,
            title=worker_args.get("title"),
            separate_folder=worker_args.get("separate_folder", False),
            convert_to_mp4=bool(worker_args.get("convert_to_mp4", True)),
            start_seconds=float(worker_args.get("start_seconds", 0.0) or 0.0),
            end_seconds=worker_args.get("end_seconds"),
            event_queue=self._event_queue,
            language=DEFAULT_LANGUAGE,
        )
        with self._lock:
            self._workers[worker.task_id] = worker
        worker.start()
        return worker

    def _remove_from_waiting(self, task_id: str) -> bool:
        with self._lock:
            for index, queued in enumerate(self._waiting_queue):
                if queued.get("task_id") == task_id:
                    self._waiting_queue.pop(index)
                    return True
        return False

    def _mark_status(self, task_id: str, status: str, error: str | None = None) -> None:
        updated = False
        with self._lock:
            for item in self.queue_items:
                if item.get("id") != task_id:
                    continue
                item["status"] = status
                if error is not None:
                    item["error"] = error
                elif status != "error":
                    item["error"] = ""
                updated = True
                break
            if updated:
                self._save_queue()

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

    def open_url(self, url: str) -> None:
        """Open a URL in the system default browser."""

        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            return

    def open_file(self, path: str) -> None:
        """Open the provided file with the system default handler."""

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

    def open_folder(self, path: str) -> None:
        """Open the folder containing the given file path."""

        if not path:
            return
        target = Path(path).expanduser()
        folder = target if target.is_dir() else target.parent
        if not folder.exists():
            return

        if sys.platform.startswith("win"):
            selection_target = target if target.exists() else folder
            subprocess.Popen(  # noqa: S603
                f'explorer /select,"{selection_target}"'
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])  # noqa: S603
        else:
            subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603

    def cancel_download(self, task_id: str) -> dict[str, str]:
        """Request cancellation of a running worker."""

        with self._lock:
            worker = self._workers.get(task_id)
            if worker is None and self._remove_from_waiting(task_id):
                self._mark_status(task_id, "cancelled")
                self._event_queue.put(
                    {"task_id": task_id, "type": "finished", "cancelled": True}
                )
                return {"status": "ok", "task_id": task_id}
        if worker is None:
            return {"status": "error", "error": "Task not found"}

        worker.cancel()
        return {"status": "ok", "task_id": task_id}

    def remove_task(self, task_id: str) -> dict[str, str]:
        """Remove a task from the persisted queue."""

        with self._lock:
            self.queue_items = [item for item in self.queue_items if item.get("id") != task_id]
            self._save_queue()
        self._remove_from_waiting(task_id)
        return {"status": "ok", "task_id": task_id}

    def get_queue_stats(self) -> dict[str, int]:
        with self._lock:
            return {"count": len(self.queue_items), "active": len(self._workers)}

    def clear_all_history(self) -> dict[str, int]:
        return self.get_queue_stats()

    def perform_clear(self) -> dict[str, str]:
        with self._lock:
            workers = list(self._workers.values())
            self._waiting_queue.clear()
            self.queue_items = []
            self._save_queue()
        for worker in workers:
            worker.cancel()
        return {"status": "ok"}

    def update_setting(self, key: str, value: Any) -> dict[str, str]:
        """Update a specific setting and persist it."""

        if key == "root_folder":
            try:
                folder = Path(str(value)).expanduser()
            except Exception:
                return {"status": "error", "error": "Invalid path"}
            self._ensure_root_folder(folder)
            self.root_folder = str(folder.resolve())
            self.settings["root_folder"] = self.root_folder
        elif key in {"mp4", "sequential"}:
            self.settings[key] = bool(value)
        else:
            return {"status": "error", "error": "Unknown setting"}

        self._save_settings()
        return {"status": "ok"}

    def shutdown(self) -> None:
        """Stop the monitor thread and cancel active workers."""

        if not self._running:
            return
        self._running = False
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        self._event_queue.put(None)
        for worker in workers:
            worker.join(timeout=1.0)
        if self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)

    def check_updates(self) -> None:
        self.update_status = "checking"
        self._emit_update_event({"type": "update_checking"})

        result: dict[str, Any] = {}
        finished = threading.Event()

        def _worker() -> None:
            try:
                info = check_for_update(
                    __version__, timeout=self.UPDATE_NETWORK_TIMEOUT
                )
                result["info"] = info
            except UpdateError as exc:
                result["error"] = str(exc)
            except Exception as exc:  # pylint: disable=broad-except
                result["error"] = str(exc)
            finally:
                finished.set()

        threading.Thread(target=_worker, daemon=True).start()

        if not finished.wait(self.UPDATE_CHECK_TIMEOUT):
            self.update_status = "error"
            self._emit_update_event(
                {"type": "update_error", "error": "Update check timed out"}
            )
            return

        if result.get("error"):
            self.update_status = "error"
            self._emit_update_event(
                {"type": "update_error", "error": str(result["error"])}
            )
            return

        info = result.get("info")
        if info is None:
            self.update_status = "ready"
            self.pending_update_info = None
            self._emit_update_event({"type": "update_not_found"})
            return

        self.update_status = "available"
        self.pending_update_info = info
        self._emit_update_event(
            {"type": "update_available", "version": info.latest_version}
        )

    def perform_update(self) -> None:
        threading.Thread(target=self._perform_update_worker, daemon=True).start()

    def _perform_update_worker(self) -> None:
        info = self.pending_update_info
        if info is None:
            self._emit_update_event(
                {"type": "update_error", "error": "Update metadata is missing"}
            )
            return

        self.update_status = "installing"

        def progress(downloaded: int, total: int | None) -> None:
            percent = 0
            if total and total > 0:
                percent = min(int((downloaded / total) * 100), 100)
            self._emit_update_event(
                {"type": "update_progress", "progress": percent, "stage": "download"}
            )

        try:
            download_path = download_update_asset(
                info, Path(self.update_cache_dir), progress_callback=progress
            )
            self._emit_update_event(
                {"type": "update_progress", "progress": 0, "stage": "install"}
            )
            install_result = install_downloaded_asset(
                download_path, info.latest_version, Path(self.update_cache_dir)
            )
        except UpdateError as exc:
            self._emit_update_event({"type": "update_error", "error": str(exc)})
            return
        except Exception as exc:  # pylint: disable=broad-except
            self._emit_update_event({"type": "update_error", "error": str(exc)})
            return

        executable = install_result.executable
        if executable is None:
            self._emit_update_event(
                {"type": "update_error", "error": "Update executable is missing"}
            )
            return

        self._emit_update_event(
            {"type": "update_ready", "version": install_result.version, "stage": "install"}
        )

        try:
            install_update_and_restart(Path(executable))
            if self.window:
                self.window.destroy()
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            self._emit_update_event({"type": "update_error", "error": str(exc)})

    def _emit_update_event(self, event: dict[str, Any]) -> None:
        if not self.window:
            return
        try:
            payload = json.dumps(event, ensure_ascii=False)
            self.window.evaluate_js(
                f"window.handlePyEvent && window.handlePyEvent({payload});"
            )
        except Exception:
            return

    def _process_queue(self) -> None:
        with self._lock:
            if self._workers or not self._waiting_queue:
                return
            next_args = self._waiting_queue.pop(0)

        self._mark_status(next_args["task_id"], "downloading", error="")
        self._event_queue.put(
            {"task_id": next_args["task_id"], "type": "status", "status": "downloading"}
        )
        self._start_worker(next_args)

    def _dispatch_events(self) -> None:
        while self._running:
            try:
                event = self._event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if event is None:
                break

            if event.get("type") == "finished":
                task_id = str(event.get("task_id", ""))
                with self._lock:
                    worker = self._workers.get(task_id)
                    if worker and not worker.is_alive():
                        self._workers.pop(task_id, None)

            self._update_queue_from_event(event)

            if event.get("type") in {"done", "error", "finished"}:
                self._process_queue()

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

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        default_root = DEFAULT_ROOT
        self._ensure_root_folder(default_root)
        defaults = {
            "root_folder": str(default_root),
            "mp4": True,
            "sequential": False,
        }

        if not SETTINGS_FILE.exists():
            self._save_settings_data(defaults)
            return defaults

        try:
            loaded: dict[str, Any] = json.loads(
                SETTINGS_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            self._save_settings_data(defaults)
            return defaults

        if not isinstance(loaded, dict):
            self._save_settings_data(defaults)
            return defaults

        merged = {**defaults, **loaded}
        if "convert_mp4" in loaded:
            merged["mp4"] = bool(loaded.get("convert_mp4", True))
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
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            return

    def _ensure_root_folder(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return

    def _load_queue(self) -> list[dict[str, Any]]:
        """Load persisted queue history."""

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not QUEUE_FILE.exists():
            self._save_queue_data([])
            return []
        try:
            loaded = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            self._save_queue_data([])
            return []
        if not isinstance(loaded, list):
            self._save_queue_data([])
            return []
        valid_items: list[dict[str, Any]] = []
        changed = False
        for item in loaded:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("id", "")).strip()
            if not task_id:
                continue
            status = item.get("status", "done")
            error = item.get("error", "")
            if status in {"downloading", "converting"}:
                status = "error"
                error = "Відео не було повністю завантажено"
                changed = True
            elif status == "queued":
                status = "cancelled"
                changed = True

            valid_items.append(
                {
                    "id": task_id,
                    "title": item.get("title") or item.get("url") or task_id,
                    "url": item.get("url", ""),
                    "status": status,
                    "path": item.get("path", ""),
                    "error": error,
                }
            )
        if changed or not QUEUE_FILE.exists():
            self._save_queue_data(valid_items)
        return valid_items

    def _save_queue(self) -> None:
        """Persist current queue items to disk."""

        self._save_queue_data(self.queue_items)

    def _save_queue_data(self, data: list[dict[str, Any]]) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            QUEUE_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            return

    def _add_queue_item(
        self, task_id: str, *, url: str, title: str, status: str, path: str, error: str
    ) -> None:
        item = {
            "id": task_id,
            "url": url,
            "title": title,
            "status": status,
            "path": path,
            "error": error,
        }
        with self._lock:
            self.queue_items.append(item)
            self._save_queue()

    def _update_queue_from_event(self, event: dict[str, Any]) -> None:
        task_id = str(event.get("task_id", ""))
        if not task_id:
            return

        updated = False
        with self._lock:
            for item in self.queue_items:
                if item.get("id") != task_id:
                    continue
                event_type = event.get("type")
                if event_type == "title" and event.get("title"):
                    item["title"] = str(event["title"])
                if event_type == "done":
                    item["status"] = "done"
                    item["path"] = str(event.get("path", item.get("path", "")))
                    updated = True
                if event_type == "error":
                    item["status"] = "error"
                    item["error"] = str(event.get("error", ""))
                    updated = True
                if event_type == "finished" and event.get("cancelled"):
                    item["status"] = "cancelled"
                    updated = True
                if event_type == "status" and event.get("status"):
                    item["status"] = str(event.get("status"))
                    updated = True
                if event_type == "progress" and event.get("status"):
                    item["status"] = str(event.get("status"))
                    updated = True
                break

            if updated:
                self._save_queue()


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
        height=920,
        min_size=(960, 640),
        js_api=bridge,
        frameless=True,
        easy_drag=True,
        resizable=True,
    )

    # 3. Attach created window back to bridge
    bridge.window = window

    window.events.closed += bridge.shutdown

    # 4. Expose handled via js_api
    webview.start()


if __name__ == "__main__":
    setup_logging()
    main()
