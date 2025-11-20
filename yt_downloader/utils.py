"""Assorted helper utilities used across the application."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional


def sanitize_filename(title: str) -> str:
    """Return a filesystem-safe variant of ``title``."""

    invalid = set('<>:"/\\|?*')
    cleaned = ["_" if ch in invalid or ord(ch) < 32 else ch for ch in title]
    sanitized = "".join(cleaned).strip().rstrip(". ")
    if not sanitized:
        sanitized = "video"
    return sanitized


def format_timestamp(value: float) -> str:
    """Format seconds into ``hh:mm:ss(.ms)`` style string."""

    total_ms = int(round(max(value, 0.0) * 1000))
    seconds, milliseconds = divmod(total_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}".rstrip("0.")
    if milliseconds:
        return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}".rstrip("0.")
    return f"{minutes:02d}:{seconds:02d}"


def shorten_title(title: str, limit: int = 40) -> str:
    """Return a shortened version of ``title`` for display purposes."""

    if len(title) <= limit:
        return title
    cutoff = max(limit - 3, 1)
    return title[:cutoff] + "..."


def is_supported_video_url(value: str) -> bool:
    """Validate that ``value`` looks like a downloadable media URL."""

    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    try:
        parsed = urlparse(candidate)
    except Exception:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


def parse_time_input(text: str) -> Optional[float]:
    """Parse a ``hh:mm:ss`` style string into seconds."""

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


def unique_path(candidate: Path) -> Path:
    """Return a non-conflicting path derived from ``candidate``."""

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


def resolve_executable(*names: str) -> Optional[Path]:
    """Return the first accessible executable matching ``names``.

    The lookup emulates the behaviour of ``shutil.which`` but also checks common
    locations used by bundled PyInstaller binaries so that dependencies such as
    ``ffmpeg``/``ffprobe`` can be shipped alongside the application.
    """

    for name in names:
        located = shutil.which(name)
        if located:
            return Path(located)

    search_roots: list[Path] = []

    if getattr(sys, "frozen", False):  # PyInstaller onefile executable
        executable_dir = Path(sys.executable).resolve().parent
        search_roots.append(executable_dir)
        bundle_dir = Path(getattr(sys, "_MEIPASS", executable_dir))
        search_roots.append(bundle_dir)
    else:
        search_roots.append(Path(__file__).resolve().parent)

    search_roots.append(Path(sys.executable).resolve().parent)
    search_roots.append(Path.cwd())

    seen: set[Path] = set()
    for root in search_roots:
        try:
            resolved_root = root.resolve()
        except FileNotFoundError:
            continue
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        for name in names:
            candidate = resolved_root / name
            if candidate.exists():
                if os.name != "nt":
                    try:
                        mode = candidate.stat().st_mode
                    except OSError:
                        mode = 0
                    if not mode or not bool(mode & 0o111):
                        continue
                return candidate
    return None


def resolve_asset_path(*relative_paths: str) -> Optional[Path]:
    """Return the first existing asset matching ``relative_paths``.

    The lookup searches a handful of locations that cover running from source
    as well as the frozen PyInstaller bundle used for the Windows release.
    """

    search_roots: list[Path] = []

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        bundle_dir = Path(getattr(sys, "_MEIPASS", executable_dir))
        search_roots.extend([bundle_dir, executable_dir])
    else:
        package_dir = Path(__file__).resolve().parent
        search_roots.extend([package_dir, package_dir.parent, Path.cwd()])

    seen: set[Path] = set()
    for root in search_roots:
        try:
            resolved_root = root.resolve()
        except FileNotFoundError:
            continue
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        for relative_path in relative_paths:
            candidate = resolved_root / relative_path
            if candidate.exists():
                return candidate
    return None
