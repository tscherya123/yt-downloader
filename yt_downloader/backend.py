"""High-level helpers for interacting with external media tooling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

__all__ = [
    "BackendError",
    "fetch_video_metadata",
    "download_video",
]


class BackendError(RuntimeError):
    """Raised when a required backend dependency is unavailable."""


@dataclass
class _YtDlpContext:
    module: Any

    @property
    def YoutubeDL(self) -> Any:  # noqa: N802 - property mirrors module attribute
        return self.module.YoutubeDL


def _ensure_yt_dlp() -> _YtDlpContext:
    try:
        import yt_dlp  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
        raise BackendError(
            "yt-dlp is not installed. Install the 'yt-dlp' package to enable video downloads."
        ) from exc
    return _YtDlpContext(module=yt_dlp)


class _SilentLogger:
    """A ``yt_dlp`` compatible logger that suppresses console output."""

    def debug(self, *_: object, **__: object) -> None:  # noqa: D401 - tiny helper
        """Ignore debug messages."""

    def info(self, *_: object, **__: object) -> None:  # noqa: D401 - tiny helper
        """Ignore info messages."""

    def warning(self, *_: object, **__: object) -> None:  # noqa: D401 - tiny helper
        """Ignore warnings."""

    def error(self, message: str) -> None:
        raise RuntimeError(message)


def _build_base_options() -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
    }


def fetch_video_metadata(url: str) -> Dict[str, Any]:
    """Return the metadata for ``url`` using the ``yt_dlp`` Python API."""

    context = _ensure_yt_dlp()
    options = {"skip_download": True, **_build_base_options()}
    with context.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(
    *,
    url: str,
    workdir: Path,
    tempdir: Path,
    clip_arguments: Optional[Iterable[str]] = None,
    progress_hooks: Optional[Iterable[Callable[[Dict[str, Any]], None]]] = None,
) -> Path:
    """Download ``url`` into ``workdir`` and return the resulting file path."""

    context = _ensure_yt_dlp()
    base_options = _build_base_options()
    options: Dict[str, Any] = {
        **base_options,
        "paths": {
            "home": str(workdir),
            "temp": str(tempdir),
        },
        "outtmpl": "source.%(ext)s",
        "format": "bv*+ba/b",
        "format_sort": ["res", "fps", "br"],
        "concurrent_fragment_downloads": 8,
        "hls_prefer_ffmpeg": True,
        "noprogress": True,
    }
    if progress_hooks:
        options["progress_hooks"] = list(progress_hooks)
    if clip_arguments:
        clip_args = list(clip_arguments)
        if clip_args:
            options.update(
                {
                    "downloader": "ffmpeg",
                    "downloader_args": {"ffmpeg_i": clip_args},
                }
            )
    with context.YoutubeDL(options) as ydl:
        ydl.download([url])

    placeholder = workdir / "source.%(ext)s"
    if placeholder.exists():
        placeholder.unlink()

    for candidate in workdir.glob("source.*"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Downloaded file not found in workdir")
