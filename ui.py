"""Entry point for launching the PyWebView UI."""

from yt_downloader.app import DownloaderUI
from main_webview import main

__all__ = ["DownloaderUI", "main"]


if __name__ == "__main__":
    main()
