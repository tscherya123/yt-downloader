"""Core package for the YouTube Downloader desktop application."""

from .version import __version__

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


def __getattr__(name: str):
    if name == "DownloaderUI":
        from .app import DownloaderUI

        return DownloaderUI
    if name == "main":
        from .app import main

        return main
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__():
    return sorted(set(globals()) | set(__all__))
