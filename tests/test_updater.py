import subprocess
import sys
from pathlib import Path

import pytest

from yt_downloader.updater import cleanup_old_versions, install_update_and_restart


def test_install_update_replaces_executable_without_restart(monkeypatch, tmp_path) -> None:
    current = tmp_path / "app.exe"
    replacement = tmp_path / "new.exe"
    current.write_text("old-version")
    replacement.write_text("new-version")

    monkeypatch.setattr(sys, "executable", str(current))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    install_update_and_restart(replacement, restart=False)

    assert current.read_text() == "new-version"
    backup = current.with_suffix(current.suffix + ".old")
    assert backup.read_text() == "old-version"


def test_install_update_restarts_application(monkeypatch, tmp_path) -> None:
    current = tmp_path / "app.exe"
    replacement = tmp_path / "new.exe"
    current.write_text("old-version")
    replacement.write_text("new-version")

    monkeypatch.setattr(sys, "executable", str(current))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(SystemExit):
        install_update_and_restart(replacement)

    assert current.read_text() == "new-version"
    assert captured["cmd"][0] == str(current)
    kwargs = captured["kwargs"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True


def test_cleanup_old_versions_removes_backups(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "app.exe"
    backup = executable.with_suffix(executable.suffix + ".old")
    executable.write_text("current")
    backup.write_text("old")

    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    cleanup_old_versions()

    assert not backup.exists()
