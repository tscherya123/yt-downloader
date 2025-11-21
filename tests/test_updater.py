import sys

import pytest

from yt_downloader.updater import apply_update_files, cleanup_old_versions


def test_apply_update_replaces_executable(monkeypatch, tmp_path) -> None:
    current = tmp_path / "app.exe"
    replacement = tmp_path / "new.exe"
    backup = current.with_suffix(current.suffix + ".old")
    current.write_text("old-version")
    replacement.write_text("new-version")
    backup.write_text("stale")

    monkeypatch.setattr(sys, "executable", str(current))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert apply_update_files(replacement) is True

    assert current.read_text() == "new-version"
    assert backup.read_text() == "old-version"


def test_apply_update_requires_frozen(monkeypatch, tmp_path) -> None:
    replacement = tmp_path / "new.exe"
    replacement.write_text("new-version")

    monkeypatch.setattr(sys, "frozen", False, raising=False)

    with pytest.raises(RuntimeError):
        apply_update_files(replacement)


def test_cleanup_old_versions_removes_backups(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "app.exe"
    backup = executable.with_suffix(executable.suffix + ".old")
    executable.write_text("current")
    backup.write_text("old")

    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    cleanup_old_versions()

    assert not backup.exists()
