"""Standalone helper to relaunch the application after an update."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence


def _parse_args(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return list(parsed)
    return []


def run_relauncher(
    target: Path,
    launch_args: Sequence[str] | None = None,
    wait_seconds: float = 0.5,
) -> int:
    """Wait for ``wait_seconds`` and relaunch ``target`` with ``launch_args``."""

    wait_seconds = max(0.0, float(wait_seconds))
    time.sleep(wait_seconds)

    args = [str(target)]
    if launch_args:
        args.extend(list(launch_args))

    popen_kwargs: dict[str, object] = {
        "close_fds": True,
        "cwd": str(target.parent),
    }
    if os.name == "nt":  # pragma: no cover - platform dependent
        creation_flags = 0
        creation_flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creation_flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creation_flags:
            popen_kwargs["creationflags"] = creation_flags

    try:
        subprocess.Popen(args, **popen_kwargs)
    except Exception:  # pragma: no cover - defensive
        return 1
    return 0


def run_from_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="yt-downloader-relauncher")
    parser.add_argument("--target", required=True)
    parser.add_argument("--wait", type=float, default=0.5)
    parser.add_argument("--args-json")

    args = parser.parse_args(argv)
    target = Path(args.target)
    launch_args = _parse_args(args.args_json)

    return run_relauncher(target, launch_args, wait_seconds=args.wait)


__all__ = ["run_relauncher", "run_from_cli"]


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(run_from_cli())
