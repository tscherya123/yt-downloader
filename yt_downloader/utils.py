"""Assorted helper utilities used across the application."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse
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


def is_youtube_video_url(value: str) -> bool:
    """Validate that ``value`` looks like a YouTube video URL."""

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
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if host.endswith("youtube.com"):
        if path.startswith("/watch"):
            query = parse_qs(parsed.query)
            return any(part for part in query.get("v", []) if part.strip())
        if path.startswith("/shorts/"):
            return bool(path.split("/shorts/", 1)[-1].strip("/"))
        if path.startswith("/live/"):
            return bool(path.split("/live/", 1)[-1].strip("/"))
        if path.startswith("/embed/"):
            return bool(path.split("/embed/", 1)[-1].strip("/"))
        return False
    if host == "youtu.be" or host.endswith(".youtu.be"):
        return bool(path.strip("/"))
    return False


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
