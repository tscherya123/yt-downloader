"""Helper utilities for performing in-place application updates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _launch_detached(executable: Path) -> None:
    args = [str(executable), *sys.argv[1:]]
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "nt":  # pragma: no cover - platform specific
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        popen_kwargs["creationflags"] = creation_flags
    else:  # pragma: no cover - platform specific
        popen_kwargs["start_new_session"] = True

    subprocess.Popen(args, **popen_kwargs)


def install_update_and_restart(downloaded_asset: Path, restart: bool = True) -> None:
    """Replace the current executable with ``downloaded_asset`` and restart."""

    if not downloaded_asset.exists():
        raise FileNotFoundError(downloaded_asset)

    if not _is_frozen():
        raise RuntimeError("In-place updates are only supported for frozen executables.")

    current_executable = Path(sys.executable).resolve()
    new_executable = downloaded_asset.resolve()

    if new_executable == current_executable:
        if restart:
            _launch_detached(current_executable)
            sys.exit(0)
        return

    backup = current_executable.with_suffix(current_executable.suffix + ".old")
    try:
        backup.unlink()
    except OSError:
        pass

    current_executable.replace(backup)
    new_executable.replace(current_executable)

    try:
        current_executable.chmod(0o755)
    except OSError:
        pass

    if restart:
        _launch_detached(current_executable)
        sys.exit(0)


def cleanup_old_versions() -> None:
    """Remove leftover ``*.old`` executables from previous updates."""

    if not _is_frozen():
        return

    base_dir = Path(sys.executable).resolve().parent
    for old_file in base_dir.glob("*.old"):
        try:
            old_file.unlink()
        except OSError:
            continue


__all__ = ["cleanup_old_versions", "install_update_and_restart"]
