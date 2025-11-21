"""Helper utilities for performing in-place application updates."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _launch_detached(executable: Path) -> None:
    exe_path = f'"{str(executable)}"'
    args = " ".join(f'"{arg}"' for arg in sys.argv[1:])

    cmd_command = f"timeout /t 3 /nobreak > NUL & start \"\" {exe_path} {args}"

    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "shell": True,
    }

    if os.name == "nt":  # pragma: no cover - platform specific
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        popen_kwargs["creationflags"] = creation_flags

    subprocess.Popen(f'cmd /c "{cmd_command}"', **popen_kwargs)


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
            return
        return

    backup = current_executable.with_suffix(current_executable.suffix + ".old")
    try:
        backup.unlink()
    except OSError:
        pass

    local_update = current_executable.with_suffix(".exe.new")
    shutil.copy2(new_executable, local_update)

    try:
        local_update.chmod(0o755)
    except OSError:
        pass

    current_executable.replace(backup)
    local_update.replace(current_executable)

    try:
        current_executable.chmod(0o755)
    except OSError:
        pass

    if restart:
        _launch_detached(current_executable)
        return


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
