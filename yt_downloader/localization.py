"""Localization utilities and translation catalog."""

from __future__ import annotations

from typing import Dict

SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"

TRANSLATIONS: Dict[str, Dict[str, str]] = {
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
    "log_codecs": {
        "uk": "[2/4] Відео: {video}   Аудіо: {audio}",
        "en": "[2/4] Video: {video}   Audio: {audio}",
    },
    "log_skip_transcode": {
        "uk": "[INFO] Уже H.264+AAC. Перейменовую без перекодування.",
        "en": "[INFO] Already H.264+AAC. Renaming without transcoding.",
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
    "log_cancelled": {
        "uk": "[INFO] Завантаження скасовано користувачем.",
        "en": "[INFO] Download cancelled by user.",
    },
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
    "error_missing_ffmpeg": {
        "uk": "Не знайдено ffmpeg. Додайте ffmpeg до PATH або розмістіть поруч із програмою.",
        "en": "ffmpeg is not available. Add it to PATH or place it next to the application.",
    },
    "error_missing_ffprobe": {
        "uk": "Не знайдено ffprobe. Додайте ffprobe до PATH або розмістіть поруч із програмою.",
        "en": "ffprobe is not available. Add it to PATH or place it next to the application.",
    },
    "error_final_file": {
        "uk": "Не вдалося створити фінальний файл",
        "en": "Failed to create the final file",
    },
    "segment_end": {"uk": "кінець", "en": "end"},
}


def translate(language: str, key: str, **kwargs: object) -> str:
    """Return a translated string for the provided key."""

    mapping = TRANSLATIONS.get(key, {})
    fallback = TRANSLATIONS.get(key, {}).get(DEFAULT_LANGUAGE, key)
    text = mapping.get(language, fallback)
    try:
        return text.format(**kwargs)
    except Exception:
        # Некоректні параметри не повинні ламати інтерфейс.
        return text
