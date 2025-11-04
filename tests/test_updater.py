from __future__ import annotations

import sys
from pathlib import Path

from yt_downloader.updater import (
    build_updater_command,
    maybe_run_updater,
    run_updater,
)


def test_build_updater_command_contains_expected_arguments(tmp_path) -> None:
    source = tmp_path / "source.exe"
    target = tmp_path / "target.exe"
    log_path = tmp_path / "update.log"
    command = build_updater_command(
        source,
        target,
        target,
        ["--foo", "bar"],
        log_path,
        wait_before=0.25,
        max_wait=12.5,
    )
    assert command[0] == str(Path(sys.executable))
    assert command[1] == "--run-updater"
    assert "--source" in command
    assert "--target" in command
    assert "--launch" in command
    assert "--launch-args-json" in command
    assert "--log-file" in command
    assert "0.25" in command
    assert "12.5" in command


def test_run_updater_replaces_file(tmp_path) -> None:
    source = tmp_path / "new.exe"
    target = tmp_path / "app.exe"
    source.write_text("new-version")
    target.write_text("old-version")

    exit_code = run_updater(
        source=source,
        target=target,
        launch_path=None,
        launch_args=None,
        log_file=None,
        wait_before=0.0,
        max_wait=1.0,
    )
    assert exit_code == 0
    assert target.read_text() == "new-version"


def test_maybe_run_updater_no_flag_returns_none() -> None:
    assert maybe_run_updater(["--other", "value"]) is None
