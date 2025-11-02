"""Core package for the YouTube Downloader desktop application."""

__version__ = "0.1.3"

from .app import DownloaderUI, main
from .localization import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, translate
from .themes import DEFAULT_THEME, THEMES
from .worker import DownloadWorker, DownloadCancelled

__all__ = [
    "DownloaderUI",
    "DownloadWorker",
    "DownloadCancelled",
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "DEFAULT_THEME",
    "THEMES",
    "translate",
    "main",
    "__version__",
]
