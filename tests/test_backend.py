"""Tests for backend helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from yt_downloader import backend


class DummyCompletedProcess:
    """A lightweight stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


def test_fetch_video_metadata_falls_back_to_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the Python module is unavailable the CLI fallback should be used."""

    called_commands: list[list[str]] = []
    metadata = {"title": "Example", "duration": 12.5}

    def fake_ensure() -> None:
        raise backend.BackendError("missing module")

    def fake_locate() -> Path:
        return Path("/usr/bin/yt-dlp")

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        called_commands.append(list(args[0]))
        return DummyCompletedProcess(json.dumps(metadata))

    monkeypatch.setattr(backend, "_ensure_yt_dlp", fake_ensure)
    monkeypatch.setattr(backend, "_locate_yt_dlp_executable", fake_locate)
    monkeypatch.setattr(backend.subprocess, "run", fake_run)

    result = backend.fetch_video_metadata("https://youtu.be/example")

    assert result == metadata
    assert called_commands and "--dump-single-json" in called_commands[0]


def test_fetch_video_metadata_subprocess_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI failures should be surfaced as ``BackendError`` instances."""

    def fake_ensure() -> None:
        raise backend.BackendError("missing module")

    def fake_locate() -> Path:
        return Path("/usr/bin/yt-dlp")

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return DummyCompletedProcess("not-json")

    monkeypatch.setattr(backend, "_ensure_yt_dlp", fake_ensure)
    monkeypatch.setattr(backend, "_locate_yt_dlp_executable", fake_locate)
    monkeypatch.setattr(backend.subprocess, "run", fake_run)

    with pytest.raises(backend.BackendError):
        backend.fetch_video_metadata("https://youtu.be/example")
