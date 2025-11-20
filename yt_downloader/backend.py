"""High-level helpers for interacting with external media tooling."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from .logger import get_logger
from .utils import resolve_executable

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


class _FileLogger:
    """A ``yt_dlp`` compatible logger that forwards messages to the app logger."""

    def __init__(self) -> None:
        self._logger = LOGGER

    def debug(self, message: str) -> None:  # noqa: D401 - tiny helper
        """Log debug messages."""

        self._logger.debug(str(message))

    def info(self, message: str) -> None:  # noqa: D401 - tiny helper
        """Log info messages."""

        self._logger.info(str(message))

    def warning(self, message: str) -> None:  # noqa: D401 - tiny helper
        """Log warnings."""

        self._logger.warning(str(message))

    def error(self, message: str) -> None:
        self._logger.error(str(message))


def _build_base_options() -> Dict[str, Any]:
    return {
        "quiet": False,
        "no_warnings": False,
        "verbose": True,
        "logger": _FileLogger(),
    }


def _setup_environment() -> None:
    """Ensure bundled runtimes are discoverable by ``yt-dlp``.

    Adds the folder containing ``qjs.exe`` and ``ffmpeg.exe`` to ``PATH`` so
    the dependencies are available without global installation.
    """

    for tool in ["qjs.exe", "ffmpeg.exe"]:
        path = resolve_executable(tool)
        if not path:
            continue

        folder = str(path.parent)
        if folder not in os.environ["PATH"]:
            os.environ["PATH"] = folder + os.pathsep + os.environ["PATH"]


def fetch_video_metadata(url: str) -> Dict[str, Any]:
    """Return the metadata for ``url`` using the ``yt_dlp`` Python API."""

    try:
        context = _ensure_yt_dlp()
    except BackendError as exc:
        try:
            return _fetch_video_metadata_subprocess(url)
        except BackendError as subprocess_exc:
            raise subprocess_exc from exc
    options = {"skip_download": True, **_build_base_options()}
    with context.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(
    *,
    url: str,
    workdir: Path,
    tempdir: Path,
    clip_start: Optional[float] = None,
    clip_end: Optional[float] = None,
    progress_hooks: Optional[Iterable[Callable[[Dict[str, Any]], None]]] = None,
) -> Path:
    """Download ``url`` into ``workdir`` and return the resulting file path."""

    _setup_environment()
    context = _ensure_yt_dlp()
    base_options = _build_base_options()
    options: Dict[str, Any] = {
        **base_options,
        "paths": {
            "home": str(workdir),
            "temp": str(tempdir),
        },
        "outtmpl": "source.%(ext)s",
        "format": "bestvideo*+bestaudio/best",
        "format_sort": ["res:2160", "res:1440", "res:1080", "fps", "br"],
        "concurrent_fragment_downloads": 8,
        "hls_prefer_ffmpeg": True,
        "noprogress": True,
        "extractor_args": {
            "youtube": {
                # "web" client supports 4K and works with QuickJS
                "player_client": ["web", "tv"],
            }
        },
    }
    if progress_hooks:
        options["progress_hooks"] = list(progress_hooks)

    if clip_start is not None or clip_end is not None:
        # ``download_ranges`` expects absolute seconds and performs partial downloads
        start = 0.0 if clip_start is None else max(clip_start, 0.0)
        end = float("inf") if clip_end is None else clip_end
        if end <= start:
            raise ValueError("clip_end must be greater than clip_start")
        download_ranges = context.module.utils.download_range_func([], [(start, end)], False)
        options["download_ranges"] = download_ranges
        options["force_keyframes_at_cuts"] = True
    with context.YoutubeDL(options) as ydl:
        ydl.download([url])

    placeholder = workdir / "source.%(ext)s"
    if placeholder.exists():
        placeholder.unlink()

    for candidate in workdir.glob("source.*"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Downloaded file not found in workdir")


def _locate_yt_dlp_executable() -> Optional[Path]:
    """Try to locate a ``yt-dlp`` executable on the current system."""

    executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if executable:
        return Path(executable)

    current_executable = Path(sys.executable)
    search_roots = {current_executable.resolve().parent, Path(__file__).resolve().parent}
    for root in search_roots:
        for name in ("yt-dlp.exe", "yt-dlp"):
            candidate = root / name
            if candidate.exists():
                return candidate
    return None


def _fetch_video_metadata_subprocess(url: str) -> Dict[str, Any]:
    """Fetch metadata by invoking an external ``yt-dlp`` process."""

    executable = _locate_yt_dlp_executable()
    if not executable:
        raise BackendError(
            "yt-dlp is not installed. Install the 'yt-dlp' package to enable video downloads."
        )

    command = [
        str(executable),
        "--dump-single-json",
        "--no-warnings",
        "--quiet",
        "--skip-download",
        url,
    ]
    try:
        completed = subprocess.run(  # noqa: S603,S607 - trusted executable discovery
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        raise BackendError(
            "yt-dlp executable is not accessible. Install 'yt-dlp' to enable video downloads."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr_output = (exc.stderr or "").strip()
        if stderr_output:
            raise BackendError(stderr_output) from exc
        raise BackendError("yt-dlp failed to fetch video metadata.") from exc

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BackendError("Failed to decode yt-dlp output.") from exc
LOGGER = get_logger("yt_dlp")

