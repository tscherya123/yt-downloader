"""Backward-compatible entry point for the modularized UI package."""

from yt_downloader.app import DownloaderUI, main

__all__ = ["DownloaderUI", "main"]


if __name__ == "__main__":
    main()
