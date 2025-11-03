"""Tests for the update helper utilities."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from yt_downloader.updates import (
    InstallResult,
    find_windows_executable,
    install_downloaded_asset,
    is_version_newer,
    normalize_version,
    select_preferred_asset,
)


def test_normalize_version_strips_prefix() -> None:
    assert normalize_version("v1.2.3") == (1, 2, 3)
    assert normalize_version("1.2.3") == (1, 2, 3)
    assert normalize_version(" 2.0 ") == (2, 0)


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.2.0", "0.1.9", True),
        ("0.1.0", "0.1.5", False),
        ("1.0.0", "1.0", False),
        ("1.0.1", "1.0.1", False),
    ],
)
def test_is_version_newer(latest: str, current: str, expected: bool) -> None:
    assert is_version_newer(latest, current) is expected


def test_select_preferred_asset_prefers_windows_zip() -> None:
    assets = [
        {"name": "yt-downloader-0.1-linux.tar.gz"},
        {"name": "yt-downloader-0.1-windows.zip", "browser_download_url": "#"},
        {"name": "yt-downloader-setup.exe"},
    ]
    selected = select_preferred_asset(assets)
    assert selected is not None
    assert selected["name"] == "yt-downloader-0.1-windows.zip"


def test_find_windows_executable_prefers_named_binary(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    default_exe = tmp_path / "nested" / "setup.exe"
    default_exe.write_bytes(b"")
    preferred = tmp_path / "yt-downloader.exe"
    preferred.write_bytes(b"")

    found = find_windows_executable(tmp_path)
    assert found is not None
    assert found.name == "yt-downloader.exe"


def test_install_downloaded_asset_extracts_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("yt-downloader.exe", "binary")
        archive.writestr("readme.txt", "info")

    install_root = tmp_path / "installed"
    result = install_downloaded_asset(archive_path, "0.2.0", install_root)

    assert isinstance(result, InstallResult)
    assert result.base_path == install_root / "0.2.0"
    assert result.base_path.exists()
    assert result.executable is not None
    assert result.executable.name == "yt-downloader.exe"
    assert (result.base_path / "readme.txt").exists()
