"""Core package for the Video Downloader desktop application."""

from .version import __version__

from .localization import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, translate
from .worker import DownloadCancelled, DownloadWorker

__all__ = [
    "DownloadWorker",
    "DownloadCancelled",
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "translate",
    "__version__",
]
