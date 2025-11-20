import os
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yt_downloader import utils


def test_sanitize_filename_replaces_invalid_characters():
    result = utils.sanitize_filename('inva:lid*name?<>"/\\|')
    assert result == "inva_lid_name_______"


def test_sanitize_filename_returns_fallback_for_empty():
    result = utils.sanitize_filename(" ..")
    assert result == "video"


def test_format_timestamp_handles_hours_and_milliseconds():
    assert utils.format_timestamp(3661.256) == "01:01:01.256"


def test_format_timestamp_trims_trailing_zeroes():
    assert utils.format_timestamp(65.5) == "01:05.5"


def test_shorten_title_truncates_with_ellipsis():
    result = utils.shorten_title("a" * 50, limit=10)
    assert result == "aaaaaaa..."


def test_shorten_title_returns_within_limit_unchanged():
    text = "short"
    assert utils.shorten_title(text, limit=len(text)) == text


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://vimeo.com/123456",
        "https://example.com/videos/clip",
        "https://sub.domain.example/path?query=1",
    ],
)
def test_is_supported_video_url_recognizes_valid_urls(url):
    assert utils.is_supported_video_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "mailto:user@example.com",
        "//example.com/path",
    ],
)
def test_is_supported_video_url_rejects_invalid_urls(url):
    assert not utils.is_supported_video_url(url)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", None),
        ("   ", None),
        ("42", 42.0),
        ("01:02", 62.0),
        ("1:02:03", 3723.0),
        ("00:00:00.5", 0.5),
    ],
)
def test_parse_time_input_valid_values(text, expected):
    result = utils.parse_time_input(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize("text", ["::", "1::2", "abc", "1:2:3:4"])
def test_parse_time_input_invalid_values(text):
    with pytest.raises(ValueError):
        utils.parse_time_input(text)


def test_unique_path_generates_next_available(tmp_path: Path):
    target = tmp_path / "file.txt"
    for name in ["file.txt", "file_1.txt"]:
        (tmp_path / name).write_text("content")
    new_path = utils.unique_path(target)
    assert new_path.name == "file_2.txt"


@pytest.mark.parametrize("filename", ["new_file.txt", "subdir/new_file.txt"])
def test_unique_path_returns_candidate_when_available(tmp_path: Path, filename: str):
    candidate = tmp_path / filename
    candidate.parent.mkdir(parents=True, exist_ok=True)
    assert utils.unique_path(candidate) == candidate


def test_resolve_executable_finds_binary_in_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "custom-tool.exe"
    binary.write_text("echo test")
    try:
        binary.chmod(0o755)
    except PermissionError:
        pass
    monkeypatch.setenv("PATH", str(tmp_path))

    resolved = utils.resolve_executable("custom-tool.exe", "custom-tool")

    assert resolved == binary


def test_resolve_executable_uses_executable_directory_when_not_in_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "ffmpeg.exe"
    binary.write_text("echo ffmpeg")
    try:
        binary.chmod(0o755)
    except PermissionError:
        pass

    python_executable = tmp_path / "python.exe"
    python_executable.write_text("echo python")
    monkeypatch.setattr(utils.sys, "executable", str(python_executable))
    monkeypatch.setattr(utils.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("PATH", "")

    resolved = utils.resolve_executable("ffmpeg.exe", "ffmpeg")

    assert resolved == binary
