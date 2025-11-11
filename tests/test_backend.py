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


class _DummyYoutubeDL:
    """Test double that mimics the context manager behaviour of ``YoutubeDL``."""

    def __init__(self, options: dict[str, object], workdir: Path) -> None:
        self.params = options
        self._workdir = workdir

    def __enter__(self) -> "_DummyYoutubeDL":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None

    def download(self, urls: list[str]) -> None:
        del urls
        (self._workdir / "source.mp4").write_bytes(b"data")


class _DummyContext:
    def __init__(self, workdir: Path, captured: dict[str, object]) -> None:
        self._workdir = workdir
        self._captured = captured

    @property
    def YoutubeDL(self):  # noqa: N802 - mimic yt_dlp API
        captured = self._captured
        default_dir = self._workdir

        class Factory:
            def __init__(self, options: dict[str, object]) -> None:
                captured.update(options)
                home = options.get("paths", {}).get("home")  # type: ignore[assignment]
                target = Path(home) if isinstance(home, str) else default_dir
                self._delegate = _DummyYoutubeDL(options, target)

            def __enter__(self) -> _DummyYoutubeDL:
                return self._delegate.__enter__()

            def __exit__(self, exc_type, exc, tb) -> None:
                return self._delegate.__exit__(exc_type, exc, tb)

            def download(self, urls: list[str]) -> None:
                self._delegate.download(urls)

        return Factory


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


def test_download_video_uses_download_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a clip is requested the yt-dlp options should include download sections."""

    captured: dict[str, object] = {}

    def fake_ensure() -> _DummyContext:
        return _DummyContext(tmp_path, captured)

    monkeypatch.setattr(backend, "_ensure_yt_dlp", fake_ensure)

    workdir = tmp_path / "work"
    tempdir = tmp_path / "temp"
    workdir.mkdir()
    tempdir.mkdir()

    result = backend.download_video(
        url="https://example.com/video",
        workdir=workdir,
        tempdir=tempdir,
        clip_section="*00:00:05-00:00:10",
    )

    assert result.exists()
    assert captured.get("download_sections") == ["*00:00:05-00:00:10"]
