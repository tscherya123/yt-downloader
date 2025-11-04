"""Helper utilities for performing in-place application updates."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence

_LOGGER = logging.getLogger("yt_downloader.updater")


class UpdateReplacementError(RuntimeError):
    """Raised when the updater fails to swap executables."""


def _configure_logging(log_file: Optional[Path]) -> None:
    if log_file is None:
        logging.basicConfig(level=logging.INFO)
        return

    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _copy_with_permissions(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    try:
        destination.chmod(0o755)
    except OSError:
        pass


def _attempt_replace(target: Path, replacement: Path, backup: Path) -> None:
    if target.exists():
        if backup.exists():
            try:
                backup.unlink()
            except OSError:
                pass
        target.replace(backup)
    replacement.replace(target)


def replace_executable_with_retry(
    source: Path,
    target: Path,
    attempts: int = 60,
    delay: float = 1.0,
) -> Path:
    """Copy ``source`` over ``target`` retrying until success."""

    source = source.resolve()
    target = target.resolve()

    if not source.exists():
        raise UpdateReplacementError(f"Source {source} does not exist")

    if source == target:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_suffix(target.suffix + ".bak")
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        temp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}-{attempt}")
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass

        try:
            _copy_with_permissions(source, temp_path)
            _attempt_replace(target, temp_path, backup)
            _LOGGER.info("Replaced executable on attempt %s", attempt)
            try:
                backup.unlink()
            except OSError:
                pass
            return target
        except OSError as exc:
            last_error = exc
            _LOGGER.warning("Executable replacement attempt %s failed: %s", attempt, exc)
            try:
                temp_path.unlink()
            except OSError:
                pass
            time.sleep(delay)

    raise UpdateReplacementError(str(last_error) if last_error else "unknown_error")


def _parse_launch_args(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return list(parsed)
    return []


def run_updater(
    source: Path,
    target: Path,
    launch_path: Optional[Path] = None,
    launch_args: Sequence[str] | None = None,
    log_file: Optional[Path] = None,
    wait_before: float = 0.5,
    max_wait: float = 90.0,
) -> int:
    """Perform the replacement and optionally relaunch the application."""

    _configure_logging(log_file)
    _LOGGER.info("Updater started", extra={
        "source": str(source),
        "target": str(target),
        "launch_path": str(launch_path) if launch_path else None,
    })

    time.sleep(max(0.0, wait_before))

    deadline = time.monotonic() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            replace_executable_with_retry(source, target, attempts=1)
            break
        except UpdateReplacementError as exc:
            if time.monotonic() >= deadline:
                _LOGGER.error("Updater failed after %s attempts: %s", attempt, exc)
                return 1
            _LOGGER.info("Replacement not yet possible (attempt %s): %s", attempt, exc)
            time.sleep(1.0)

    if launch_path:
        args = [str(launch_path)]
        if launch_args:
            args.extend(launch_args)
        _LOGGER.info("Launching new executable: %s", args)
        try:
            subprocess.Popen(args, close_fds=False)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.error("Failed to launch new executable: %s", exc)
            return 1

    _LOGGER.info("Updater finished successfully")
    return 0


def build_updater_command(
    source: Path,
    target: Path,
    launch_path: Optional[Path],
    launch_args: Iterable[str],
    log_file: Optional[Path],
    wait_before: float = 0.5,
    max_wait: float = 90.0,
) -> list[str]:
    """Return command-line arguments to invoke the updater helper."""

    command = [
        str(Path(sys.executable)),
        "--run-updater",
        "--source",
        str(source),
        "--target",
        str(target),
        "--wait-before",
        str(wait_before),
        "--max-wait",
        str(max_wait),
    ]
    if launch_path:
        command.extend(["--launch", str(launch_path)])
    launch_list = list(launch_args)
    if launch_list:
        command.extend(["--launch-args-json", json.dumps(launch_list)])
    if log_file:
        command.extend(["--log-file", str(log_file)])
    return command


def run_from_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="yt-downloader-updater")
    parser.add_argument("--run-updater", action="store_true")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--launch")
    parser.add_argument("--launch-args-json")
    parser.add_argument("--log-file")
    parser.add_argument("--wait-before", type=float, default=0.5)
    parser.add_argument("--max-wait", type=float, default=90.0)

    args = parser.parse_args(argv)
    launch_args = _parse_launch_args(args.launch_args_json)
    launch_path = Path(args.launch) if args.launch else None
    log_path = Path(args.log_file) if args.log_file else None

    return run_updater(
        source=Path(args.source),
        target=Path(args.target),
        launch_path=launch_path,
        launch_args=launch_args,
        log_file=log_path,
        wait_before=args.wait_before,
        max_wait=args.max_wait,
    )


def maybe_run_updater(argv: Optional[Sequence[str]] = None) -> Optional[int]:
    """Execute updater mode when the flag is present."""

    args = list(sys.argv[1:] if argv is None else argv)
    if "--run-updater" not in args:
        return None
    return run_from_cli(args)


__all__ = [
    "UpdateReplacementError",
    "build_updater_command",
    "maybe_run_updater",
    "replace_executable_with_retry",
    "run_from_cli",
    "run_updater",
]
