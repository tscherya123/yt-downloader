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


SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"
DEFAULT_THEME = "light"


TRANSLATIONS: dict[str, dict[str, str]] = {
    "app_title": {"uk": "YouTube Downloader", "en": "YouTube Downloader"},
    "url_label": {"uk": "YouTube URL", "en": "YouTube URL"},
    "search_button": {"uk": "Знайти відео", "en": "Find video"},
    "root_label": {"uk": "Коренева тека", "en": "Root folder"},
    "choose_button": {"uk": "Вибрати", "en": "Browse"},
    "separate_folder": {
        "uk": "В окрему папку",
        "en": "Use separate folder",
    },
    "download_button": {"uk": "Скачати", "en": "Download"},
    "preview_group": {"uk": "Попередній перегляд", "en": "Preview"},
    "preview_title": {"uk": "Назва: {title}", "en": "Title: {title}"},
    "preview_duration": {
        "uk": "Тривалість: {duration}",
        "en": "Duration: {duration}",
    },
    "preview_status_loading": {
        "uk": "Завантаження даних…",
        "en": "Fetching data…",
    },
    "preview_status_ready": {"uk": "Готово", "en": "Ready"},
    "preview_status_no_duration": {
        "uk": "Не вдалося визначити тривалість",
        "en": "Failed to determine duration",
    },
    "preview_status_error": {
        "uk": "Не вдалося отримати дані за цим посиланням. Перевірте, що це сторінка відео.",
        "en": "Failed to fetch data for this link. Please ensure it is a video page.",
    },
    "preview_thumbnail_pillow_required": {
        "uk": "Щоб бачити обкладинку, встановіть пакет Pillow",
        "en": "Install Pillow to display thumbnails",
    },
    "preview_thumbnail_unavailable": {
        "uk": "Обкладинка недоступна без пакета Pillow",
        "en": "Thumbnail unavailable without Pillow",
    },
    "preview_thumbnail_failed": {
        "uk": "Не вдалося завантажити прев’ю",
        "en": "Failed to load preview",
    },
    "clip_start_label": {"uk": "Початок", "en": "Start"},
    "clip_end_label": {"uk": "Кінець", "en": "End"},
    "queue_group": {"uk": "Черга завантажень", "en": "Download queue"},
    "clear_history": {"uk": "Очистити історію", "en": "Clear history"},
    "queue_column_title": {"uk": "Назва", "en": "Title"},
    "queue_column_status": {"uk": "Статус", "en": "Status"},
    "queue_column_actions": {"uk": "Дії", "en": "Actions"},
    "log_group": {"uk": "Журнал", "en": "Log"},
    "button_open_folder": {
        "uk": "Відкрити папку",
        "en": "Open folder",
    },
    "button_cancel": {"uk": "Скасувати", "en": "Cancel"},
    "status_prefix": {
        "uk": "Статус: {status}",
        "en": "Status: {status}",
    },
    "status_waiting": {"uk": "очікує", "en": "waiting"},
    "status_downloading": {"uk": "завантажується", "en": "downloading"},
    "status_converting": {"uk": "конвертується", "en": "converting"},
    "status_done": {"uk": "готово", "en": "done"},
    "status_error": {"uk": "помилка", "en": "error"},
    "status_cancelled": {"uk": "скасовано", "en": "cancelled"},
    "dialog_choose_folder": {
        "uk": "Оберіть теку для завантажень",
        "en": "Choose download folder",
    },
    "warning_url_title": {"uk": "URL", "en": "URL"},
    "warning_url_body": {
        "uk": "Вставте посилання на відео.",
        "en": "Paste a video link.",
    },
    "warning_metadata_title": {"uk": "Метадані", "en": "Metadata"},
    "warning_metadata_body": {
        "uk": "Спочатку натисніть «Знайти відео» та дочекайтесь попереднього перегляду.",
        "en": "Click \"Find video\" first and wait for the preview.",
    },
    "error_time_title": {"uk": "Час", "en": "Time"},
    "error_time_format": {
        "uk": "Некоректний формат часу. Використовуйте формат гг:хх:сс.",
        "en": "Invalid time format. Use hh:mm:ss.",
    },
    "error_time_start_range": {
        "uk": "Початковий час має бути в межах тривалості відео.",
        "en": "The start time must be within the video duration.",
    },
    "error_time_end_range": {
        "uk": "Кінцевий час має бути більшим за початковий і не перевищувати тривалість відео.",
        "en": "The end time must be greater than the start time and not exceed the duration.",
    },
    "error_root_title": {"uk": "Папка", "en": "Folder"},
    "error_root_body": {
        "uk": "Неможливо використати цю теку.",
        "en": "This folder cannot be used.",
    },
    "log_process_started": {
        "uk": "Запущено процес",
        "en": "Process started",
    },
    "preview_status_idle": {"uk": "", "en": ""},
    "warning_history_title": {"uk": "Історія", "en": "History"},
    "warning_history_running": {
        "uk": "Спочатку дочекайтеся завершення всіх завантажень.",
        "en": "Wait for all downloads to finish first.",
    },
    "info_history_empty": {
        "uk": "Історія вже порожня.",
        "en": "History is already empty.",
    },
    "history_title": {"uk": "Історія", "en": "History"},
    "confirm_clear_history_title": {
        "uk": "Очистити історію",
        "en": "Clear history",
    },
    "confirm_clear_history_prompt": {
        "uk": "Видалити всі записи зі списку? Файли залишаться на диску.",
        "en": "Remove all entries from the list? Files will remain on disk.",
    },
    "error_open_file_missing": {
        "uk": "Файл не знайдено. Переконайтеся, що його не було переміщено або видалено.",
        "en": "File not found. Ensure it wasn't moved or deleted.",
    },
    "error_open_folder_failed": {
        "uk": "Не вдалося відкрити теку: {error}",
        "en": "Failed to open folder: {error}",
    },
    "error_open_url_failed": {
        "uk": "Не вдалося відкрити посилання: {error}",
        "en": "Failed to open link: {error}",
    },
    "error_open_url_failed_unknown": {
        "uk": "невідома причина",
        "en": "unknown reason",
    },
    "language_label": {"uk": "Мова", "en": "Language"},
    "theme_label": {"uk": "Тема", "en": "Theme"},
    "language_uk": {"uk": "Українська", "en": "Ukrainian"},
    "language_en": {"uk": "Англійська", "en": "English"},
    "theme_light": {"uk": "Світла", "en": "Light"},
    "theme_dark": {"uk": "Темна", "en": "Dark"},
    "log_root": {"uk": "[CONFIG] ROOT={root}", "en": "[CONFIG] ROOT={root}"},
    "log_workdir": {
        "uk": "[INFO] Робоча папка: {folder}",
        "en": "[INFO] Working directory: {folder}",
    },
    "log_title": {
        "uk": "[INFO] Назва: {title}",
        "en": "[INFO] Title: {title}",
    },
    "log_segment": {
        "uk": "[INFO] Відрізок: {start} – {end}",
        "en": "[INFO] Segment: {start} – {end}",
    },
    "log_download_step": {
        "uk": "[1/4] Завантаження...",
        "en": "[1/4] Downloading...",
    },
    "log_missing_source": {
        "uk": "Не знайдено source.*",
        "en": "source.* not found",
    },
    "log_codecs": {
        "uk": "[2/4] Відео: {video}   Аудіо: {audio}",
        "en": "[2/4] Video: {video}   Audio: {audio}",
    },
    "log_skip_transcode": {
        "uk": "[INFO] Уже H.264+AAC. Перейменовую без перекодування.",
        "en": "[INFO] Already H.264+AAC. Renaming without transcoding.",
    },
    "log_cancelled": {
        "uk": "[INFO] Завантаження скасовано користувачем.",
        "en": "[INFO] Download cancelled by user.",
    },
    "log_target_bitrate": {
        "uk": "[3/4] Цільовий відео-бітрейт: {bitrate}",
        "en": "[3/4] Target video bitrate: {bitrate}",
    },
    "log_transcoding": {
        "uk": "[4/4] Перекодування у MP4...",
        "en": "[4/4] Transcoding to MP4...",
    },
    "log_copy_streams": {
        "uk": "[INFO] Копіюю доріжки без повторного кодування.",
        "en": "[INFO] Copying streams without re-encoding.",
    },
    "log_done_path": {"uk": "[DONE] {path}", "en": "[DONE] {path}"},
    "log_error_message": {"uk": "[ERROR] {error}", "en": "[ERROR] {error}"},
    "error_empty_url": {"uk": "Порожній URL.", "en": "Empty URL."},
    "error_end_before_start": {
        "uk": "Кінцева позначка має бути більшою за початкову.",
        "en": "End time must be greater than start time.",
    },
    "error_missing_source": {
        "uk": "Не знайдено source.*",
        "en": "source.* not found",
    },
    "error_final_file": {
        "uk": "Не вдалося створити фінальний файл",
        "en": "Failed to create the final file",
    },
    "segment_end": {"uk": "кінець", "en": "end"},
    "error_folder_missing_file": {
        "uk": "Файл не знайдено. Переконайтеся, що його не було переміщено або видалено.",
        "en": "File not found. Ensure it wasn't moved or deleted.",
    },
    "error_generic_title": {"uk": "Помилка", "en": "Error"},
}


THEMES: dict[str, dict[str, str]] = {
    "light": {
        "background": "#f4f4f8",
        "frame": "#ffffff",
        "text": "#202124",
        "muted": "#5f6368",
        "button_bg": "#e6e6eb",
        "button_active_bg": "#d0d0d6",
        "button_fg": "#202124",
        "disabled_fg": "#a0a4a8",
        "entry_bg": "#ffffff",
        "canvas_bg": "#ffffff",
        "log_bg": "#ffffff",
        "log_fg": "#202124",
        "dropdown_bg": "#ffffff",
        "dropdown_fg": "#202124",
        "dropdown_select_bg": "#202124",
        "dropdown_select_fg": "#f5f5f5",
    },
    "dark": {
        "background": "#121212",
        "frame": "#1f1f1f",
        "text": "#f5f5f5",
        "muted": "#b0b0b0",
        "button_bg": "#2c2c2c",
        "button_active_bg": "#3a3a3a",
        "button_fg": "#f5f5f5",
        "disabled_fg": "#6f6f6f",
        "entry_bg": "#2a2a2a",
        "canvas_bg": "#1f1f1f",
        "log_bg": "#161616",
        "log_fg": "#f5f5f5",
        "dropdown_bg": "#000000",
        "dropdown_fg": "#f5f5f5",
        "dropdown_select_bg": "#f4f4f8",
        "dropdown_select_fg": "#202124",
    },
}


def translate(language: str, key: str, **kwargs: object) -> str:
    mapping = TRANSLATIONS.get(key, {})
    fallback = TRANSLATIONS.get(key, {}).get(DEFAULT_LANGUAGE, key)
    text = mapping.get(language, fallback)
    try:
        return text.format(**kwargs)
    except Exception:  # pragma: no cover - некоректні параметри не повинні ламати інтерфейс
        return text


# ----- Реалізація робітника -------------------------------------------------


class DownloadCancelled(Exception):
    """Виняток, що сигналізує про скасування завантаження."""


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
        language: str,
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
        self.language = language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._cancelled = False

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - відтворюємо логіку батника для прозорості
        workdir: Optional[Path] = None
        tempdir: Optional[Path] = None
        final_destination: Optional[Path] = None
        cancelled = False
        self._cancelled = False
        try:
            if not self.url:
                raise ValueError(self._t("error_empty_url"))

            if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
                raise ValueError(self._t("error_end_before_start"))

            self._log(self._t("log_root", root=self.root))
            self.root.mkdir(parents=True, exist_ok=True)
            self._check_cancelled()

            timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            workdir = self.root / f"DL_{timestamp}_{self.task_id.replace('-', '_')}"
            tempdir = workdir / "temp"
            tempdir.mkdir(parents=True, exist_ok=True)
            self._log(self._t("log_workdir", folder=workdir))
            self._status("downloading")
            self._check_cancelled()

            meta = self._ensure_metadata()
            title = meta.get("title") or "video"
            sanitized_title = _sanitize_filename(title)
            self._log(self._t("log_title", title=title))
            self._emit("title", title=title)
            self._check_cancelled()

            if self.start_seconds > 0 or self.end_seconds is not None:
                human_start = _format_timestamp(self.start_seconds)
                if self.end_seconds is not None:
                    human_end = _format_timestamp(self.end_seconds)
                else:
                    human_end = self._t("segment_end")
                self._log(self._t("log_segment", start=human_start, end=human_end))

            clip_requested = self.start_seconds > 0 or self.end_seconds is not None
            downloader_args: list[str] = []
            clip_applied_during_download = False
            if clip_requested:
                clip_parts: list[str] = []
                if self.start_seconds > 0:
                    clip_parts.append(f"-ss {_format_timestamp(self.start_seconds)}")
                if self.end_seconds is not None:
                    clip_parts.append(f"-to {_format_timestamp(self.end_seconds)}")
                if clip_parts:
                    downloader_args = [
                        "--downloader",
                        "ffmpeg",
                        "--downloader-args",
                        f"ffmpeg_i:{' '.join(clip_parts)}",
                    ]
                    clip_applied_during_download = True

            self._log(self._t("log_download_step"))
            yt_dlp_cmd = [
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
            ]
            yt_dlp_cmd.extend(downloader_args)
            yt_dlp_cmd.append(self.url)
            self._run(yt_dlp_cmd, cwd=workdir)

            template_placeholder = workdir / "source.%(ext)s"
            template_placeholder.unlink(missing_ok=True)

            src = next(workdir.glob("source.*"), None)
            if not src:
                raise RuntimeError(self._t("error_missing_source"))

            self._check_cancelled()
            video_codec, audio_codec = self._probe_codecs(src)
            self._log(self._t("log_codecs", video=video_codec, audio=audio_codec))

            final_path = workdir / f"{sanitized_title}.mp4"
            needs_transcode = not (
                video_codec.lower() == "h264" and audio_codec.lower() == "aac"
            )

            if not needs_transcode and not clip_applied_during_download:
                self._log(self._t("log_skip_transcode"))
                self._check_cancelled()
                src.rename(final_path)
                final_destination = final_path
            else:
                self._status("converting")
                ffmpeg_args = ["ffmpeg", "-hide_banner", "-stats", "-y"]
                clip_during_ffmpeg = (
                    (self.start_seconds > 0 or self.end_seconds is not None)
                    and not clip_applied_during_download
                )
                if clip_during_ffmpeg:
                    ffmpeg_args.extend(["-ss", _format_timestamp(self.start_seconds)])
                ffmpeg_args.extend(["-i", str(src)])
                if clip_during_ffmpeg and self.end_seconds is not None:
                    segment = max(self.end_seconds - self.start_seconds, 0.0)
                    ffmpeg_args.extend(["-t", _format_timestamp(segment)])

                if needs_transcode:
                    vbit = self._compute_vbit(src)
                    self._log(self._t("log_target_bitrate", bitrate=vbit))
                    self._log(self._t("log_transcoding"))
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
                    self._log(self._t("log_copy_streams"))
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
                raise RuntimeError(self._t("error_final_file"))

            self._check_cancelled()
            if not self.separate_folder:
                library = self.root / "YT_DOWNLOADER_FILES"
                library.mkdir(parents=True, exist_ok=True)
                target_path = _unique_path(library / final_destination.name)
                shutil.move(str(final_destination), target_path)
                final_destination = target_path

            self._check_cancelled()
            self._status("done")
            self._emit("done", path=str(final_destination))
            self._log(self._t("log_done_path", path=final_destination))
        except DownloadCancelled:
            cancelled = True
            self._cancelled = True
            self.error = None
            self._status("cancelled")
            self._log(self._t("log_cancelled"))
        except Exception as exc:  # pylint: disable=broad-except
            self.error = str(exc)
            self._status("error")
            self._emit("error", message=self.error)
            self._log(self._t("log_error_message", error=self.error))
        finally:
            cleanup_final = cancelled or self.error is not None
            if cleanup_final and final_destination is not None:
                try:
                    if final_destination.exists():
                        if final_destination.is_dir():
                            shutil.rmtree(final_destination, ignore_errors=True)
                        else:
                            final_destination.unlink()
                except Exception:
                    pass
                if not self.separate_folder:
                    library_dir = final_destination.parent
                    try:
                        if (
                            library_dir.name == "YT_DOWNLOADER_FILES"
                            and library_dir.exists()
                            and not any(library_dir.iterdir())
                        ):
                            library_dir.rmdir()
                    except Exception:
                        pass
            if workdir is not None:
                try:
                    leftover = next(workdir.glob("source.*"), None)
                    if leftover and leftover.exists():
                        leftover.unlink()
                except Exception:
                    pass
            if tempdir is not None:
                shutil.rmtree(tempdir, ignore_errors=True)
            if workdir is not None and (cancelled or not self.separate_folder):
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
        self._check_cancelled()
        process = subprocess.Popen(  # noqa: S603 - свідоме виконання зовнішньої команди
            args,
            cwd=cwd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=capture_output,
        )
        with self._process_lock:
            self._active_process = process
        stdout: Optional[str] = ""
        stderr: Optional[str] = ""
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if self._cancel_event.is_set():
                        try:
                            process.kill()
                        except Exception:
                            pass
                        stdout, stderr = process.communicate()
                        break

            if self._cancel_event.is_set():
                raise DownloadCancelled()

            if process.returncode and process.returncode != 0:
                raise subprocess.CalledProcessError(
                    process.returncode,
                    args,
                    output=stdout,
                    stderr=stderr,
                )

            if capture_output:
                return stdout or ""
            return ""
        except subprocess.CalledProcessError as exc:
            if self._cancel_event.is_set():
                raise DownloadCancelled() from exc
            raise
        finally:
            with self._process_lock:
                self._active_process = None

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._process_lock:
            process = self._active_process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise DownloadCancelled()

    def _log(self, message: str) -> None:
        self._emit("log", message=message)

    def _status(self, status: str) -> None:
        self._emit("status", status=status)

    def _emit(self, event_type: str, **payload: object) -> None:
        data: dict[str, object] = {"task_id": self.task_id, "type": event_type}
        data.update(payload)
        self.event_queue.put(data)

    def _t(self, key: str, **kwargs: object) -> str:
        return translate(self.language, key, **kwargs)


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
        translator: Callable[..., str],
        cancel_callback: Optional[Callable[[str], None]] = None,
        remove_callback: Optional[Callable[[str], None]] = None,
        open_url_callback: Optional[Callable[[str], None]] = None,
        source_url: Optional[str] = None,
        status: str = "waiting",
        final_path: Optional[Path] = None,
    ) -> None:
        super().__init__(master, padding=(12, 8), style="TaskRow.TFrame")
        self.task_id = task_id
        self.full_title = title
        self.display_title = _shorten_title(title)
        self.translate = translator
        self.status_code = status
        self.status_var = tk.StringVar()
        self._open_callback = open_callback
        self._cancel_callback = cancel_callback
        self._remove_callback = remove_callback
        self._open_url_callback = open_url_callback
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
        self.remove_button.grid(row=0, column=3)

        self.cancel_button.state(["disabled"])
        self.open_button.state(["disabled"])
        self.open_link_button.state(["disabled"])
        self.remove_button.state(["disabled"])

        self.cancel_button.grid_remove()
        self.open_button.grid_remove()
        self.open_link_button.grid_remove()
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
        self.display_title = _shorten_title(title)
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

class DownloaderUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings_path = Path(__file__).with_name("settings.json")
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

        self.queue_state_path = Path(__file__).with_name("download_queue.json")
        self.queue_state = self._load_queue_state()
        self.queue_records: dict[str, dict[str, Any]] = {
            item["task_id"]: item for item in self.queue_state.get("items", [])
        }
        self._save_queue_state()

        self.event_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
        self.workers: dict[str, DownloadWorker] = {}
        self.tasks: dict[str, TaskRow] = {}
        self.task_counter = self._compute_next_task_counter()
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

        if PIL_AVAILABLE:
            self.preview_image_label = tk.Label(preview_frame)
        else:
            self.preview_image_label = tk.Label(preview_frame)
        self.preview_image_label.grid(row=2, column=0, padx=12, pady=6, sticky="nsew")
        self.preview_image_text_key = None
        if not PIL_AVAILABLE:
            self.preview_image_text_key = "preview_thumbnail_pillow_required"
            self.preview_image_label.configure(text=self._("preview_thumbnail_pillow_required"))

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
            source_url=url,
            status="downloading",
        )
        task_row.grid(row=len(self.tasks), column=0, sticky="ew", pady=(0, 8))
        self.tasks[task_id] = task_row

        record: dict[str, Any] = {
            "task_id": task_id,
            "title": display_title,
            "status": "downloading",
            "path": None,
            "created_at": _dt.datetime.now().isoformat(),
            "url": url,
        }
        self.queue_state["items"].append(record)
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
        if PIL_AVAILABLE:
            self.preview_image_label.configure(image="", text="")
            self.preview_image_text_key = None
        else:
            self._set_preview_image_text("preview_thumbnail_pillow_required")
        self.thumbnail_image = None
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)

    def _fetch_preview(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(
                self._("warning_url_title"), self._("warning_url_body")
            )
            return
        if self.preview_fetch_in_progress:
            return

        self.preview_fetch_in_progress = True
        self.preview_token += 1
        token = self.preview_token
        self._set_preview_status("loading")
        self.search_button.state(["disabled"])
        self.download_button.state(["disabled"])
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
        self.download_button.state(["disabled"])
        self._set_clip_controls_enabled(False)
        self.search_button.state(["!disabled"])
        try:
            self.search_button.configure(state=tk.NORMAL)
        except tk.TclError:
            pass
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
        self._remove_queue_record(task_id)
        self._reflow_task_rows()
        self._update_clear_history_state()

    def _reflow_task_rows(self) -> None:
        for index, row in enumerate(self.tasks.values()):
            row.grid_configure(row=index)
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
                source_url=source_url,
                status=status,
                final_path=final_path,
            )
            task_row.grid(row=len(self.tasks), column=0, sticky="ew", pady=(0, 8))
            self.tasks[task_id] = task_row
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


def main() -> None:
    app = DownloaderUI()
    app.mainloop()


if __name__ == "__main__":
    main()
