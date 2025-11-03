"""Localization utilities and translation catalog."""

from __future__ import annotations

from typing import Dict

SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "app_title": {"uk": "YouTube Downloader", "en": "YouTube Downloader"},
    "app_title_with_version": {
        "uk": "YouTube Downloader {version}",
        "en": "YouTube Downloader {version}",
    },
    "update_check_title": {
        "uk": "Перевірка оновлень",
        "en": "Checking for updates",
    },
    "update_check_message": {
        "uk": "Зачекайте, виконується перевірка оновлення…",
        "en": "Please wait while we check for updates…",
    },
    "update_check_no_updates": {
        "uk": "Ви користуєтеся останньою версією {version}.",
        "en": "You are running the latest version {version}.",
    },
    "update_button_continue": {"uk": "Продовжити", "en": "Continue"},
    "update_available_title": {
        "uk": "Доступне оновлення",
        "en": "Update available",
    },
    "update_available_manual": {
        "uk": "Доступна версія {latest}. Відкрийте сторінку релізу, щоб завантажити оновлення вручну.",
        "en": "Version {latest} is available. Open the release page to download it manually.",
    },
    "update_button_open_page": {
        "uk": "Відкрити сторінку",
        "en": "Open page",
    },
    "update_button_later": {"uk": "Потім", "en": "Later"},
    "update_available_message": {
        "uk": "Доступна нова версія {latest}. Поточна версія {current}. Оновити зараз?",
        "en": "Version {latest} is available. Current version is {current}. Update now?",
    },
    "update_button_install": {"uk": "Оновити", "en": "Update"},
    "update_error_title": {"uk": "Оновлення", "en": "Update"},
    "update_check_failed": {
        "uk": "Не вдалося перевірити оновлення: {error}",
        "en": "Failed to check for updates: {error}",
    },
    "update_download_title": {
        "uk": "Завантаження оновлення",
        "en": "Downloading update",
    },
    "update_download_preparing": {
        "uk": "Підготовка до завантаження версії {version}…",
        "en": "Preparing download for version {version}…",
    },
    "update_download_progress": {
        "uk": "Завантажено {percent}%…",
        "en": "Downloaded {percent}%…",
    },
    "update_install_title": {
        "uk": "Встановлення оновлення",
        "en": "Installing update",
    },
    "update_install_success_launched": {
        "uk": "Версію {version} встановлено та запущено. Поточне вікно буде закрито.",
        "en": "Version {version} has been installed and launched. This window will close.",
    },
    "update_button_exit": {"uk": "Закрити", "en": "Close"},
    "update_install_launch_failed": {
        "uk": "Версію {version} збережено в {path}, але не вдалося запустити файл: {error}",
        "en": "Version {version} was saved to {path}, but launching it failed: {error}",
    },
    "update_install_success_manual": {
        "uk": "Оновлення {version} завантажено до {path}. Запустіть нову версію вручну.",
        "en": "Update {version} was downloaded to {path}. Launch the new version manually.",
    },
    "update_button_open_folder": {
        "uk": "Відкрити теку",
        "en": "Open folder",
    },
    "update_button_retry": {"uk": "Повторити", "en": "Retry"},
    "update_install_failed": {
        "uk": "Не вдалося встановити оновлення: {error}",
        "en": "Failed to install update: {error}",
    },
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
    "error_folder_missing_file": {
        "uk": "Файл не знайдено. Переконайтеся, що його не було переміщено або видалено.",
        "en": "File not found. Ensure it wasn't moved or deleted.",
    },
    "error_generic_title": {"uk": "Помилка", "en": "Error"},
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
