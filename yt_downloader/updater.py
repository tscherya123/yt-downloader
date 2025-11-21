"""Helper utilities for performing in-place application updates."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def apply_update_files(downloaded_asset: Path) -> bool:
    """Replace the current executable with ``downloaded_asset`` without restarting."""

    if not downloaded_asset.exists():
        raise FileNotFoundError(downloaded_asset)

    if not _is_frozen():
        raise RuntimeError("In-place updates are only supported for frozen executables.")

    current_executable = Path(sys.executable).resolve()
    new_executable = downloaded_asset.resolve()

    if new_executable == current_executable:
        return True

    backup = current_executable.with_suffix(current_executable.suffix + ".old")
    try:
        backup.unlink()
    except OSError:
        pass

    current_executable.replace(backup)
    shutil.copy2(new_executable, current_executable)

    try:
        current_executable.chmod(0o755)
    except OSError:
        pass

    return True


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


__all__ = ["apply_update_files", "cleanup_old_versions"]
