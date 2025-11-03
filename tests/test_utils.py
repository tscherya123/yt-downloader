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
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/shorts/abc123",
        "https://www.youtube.com/live/eventid",
        "https://www.youtube.com/embed/abc123",
        "https://youtu.be/abc123",
    ],
)
def test_is_youtube_video_url_recognizes_valid_urls(url):
    assert utils.is_youtube_video_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v= ",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=123",
    ],
)
def test_is_youtube_video_url_rejects_invalid_urls(url):
    assert not utils.is_youtube_video_url(url)


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


def test_yt_dlp_command_prefers_pythonw_when_available(monkeypatch):
    monkeypatch.setattr(utils, "_locate_pythonw", lambda: "C:/Python/pythonw.exe")
    cmd = utils.yt_dlp_command("--version")
    assert cmd == ["C:/Python/pythonw.exe", "-m", "yt_dlp", "--version"]


def test_yt_dlp_command_can_skip_gui(monkeypatch):
    monkeypatch.setattr(utils, "_locate_pythonw", lambda: "C:/Python/pythonw.exe")
    cmd = utils.yt_dlp_command("--help", prefer_gui=False)
    assert cmd == ["yt-dlp", "--help"]
