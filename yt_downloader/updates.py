"""Utility helpers for checking and installing application updates."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

DEFAULT_REPOSITORY = "tscherya123/yt-downloader"
API_URL_TEMPLATE = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "yt-downloader-updater"


class UpdateError(RuntimeError):
    """Raised when an update operation fails."""


@dataclass(frozen=True)
class UpdateInfo:
    """Metadata describing an available application update."""

    latest_version: str
    release_page: str
    asset_name: Optional[str]
    asset_url: Optional[str]
    asset_size: Optional[int]
    repository: str = DEFAULT_REPOSITORY


@dataclass(frozen=True)
class InstallResult:
    """Information about an installed update artifact."""

    version: str
    base_path: Path
    executable: Optional[Path]


__all__ = [
    "DEFAULT_REPOSITORY",
    "UpdateError",
    "UpdateInfo",
    "InstallResult",
    "check_for_update",
    "download_update_asset",
    "install_downloaded_asset",
    "is_version_newer",
    "normalize_version",
    "select_preferred_asset",
    "find_windows_executable",
]


_VERSION_SPLIT_RE = re.compile(r"[._-]")
_DIGITS_RE = re.compile(r"(\d+)")


def normalize_version(value: str) -> tuple[int, ...]:
    """Return a tuple representation of ``value`` suitable for comparison."""

    cleaned = value.strip().lstrip("vV")
    if not cleaned:
        return (0,)
    parts: list[int] = []
    for component in _VERSION_SPLIT_RE.split(cleaned):
        if not component:
            continue
        try:
            parts.append(int(component))
            continue
        except ValueError:
            match = _DIGITS_RE.search(component)
            if match:
                parts.append(int(match.group(1)))
                continue
        parts.append(0)
    return tuple(parts) if parts else (0,)


def is_version_newer(latest: str, current: str) -> bool:
    """Return ``True`` when ``latest`` represents a newer version than ``current``."""

    latest_tuple = normalize_version(latest)
    current_tuple = normalize_version(current)
    # Extend the shorter tuple with zeros to allow element-wise comparison.
    length = max(len(latest_tuple), len(current_tuple))
    padded_latest = latest_tuple + (0,) * (length - len(latest_tuple))
    padded_current = current_tuple + (0,) * (length - len(current_tuple))
    return padded_latest > padded_current


def select_preferred_asset(assets: Iterable[dict[str, object]]) -> Optional[dict[str, object]]:
    """Return the most suitable asset description for Windows users."""

    candidates = []
    for asset in assets:
        name = str(asset.get("name") or "")
        if not name:
            continue
        lowered = name.lower()
        score = 0
        if "windows" in lowered or "win" in lowered:
            score += 4
        if lowered.endswith(".zip"):
            score += 3
        if lowered.endswith(".exe"):
            score += 2
        if "yt" in lowered and "download" in lowered:
            score += 1
        candidates.append((score, name, asset))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _build_request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )


def check_for_update(
    current_version: str,
    repo: str = DEFAULT_REPOSITORY,
    timeout: float = 10.0,
) -> Optional[UpdateInfo]:
    """Fetch release information and determine whether an update is available."""

    request = _build_request(API_URL_TEMPLATE.format(repo=repo))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover - network failure handling
        raise UpdateError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise UpdateError("invalid_response") from exc

    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag:
        raise UpdateError("missing_version")
    latest_version = tag.lstrip("vV")
    if not latest_version:
        raise UpdateError("missing_version")
    if not is_version_newer(latest_version, current_version):
        return None

    assets = payload.get("assets") or []
    asset = select_preferred_asset(assets)
    release_page = str(payload.get("html_url") or f"https://github.com/{repo}/releases/latest")
    asset_name = str(asset.get("name")) if asset else None
    asset_url = str(asset.get("browser_download_url")) if asset else None
    asset_size = int(asset.get("size")) if asset and asset.get("size") is not None else None

    return UpdateInfo(
        latest_version=latest_version,
        release_page=release_page,
        asset_name=asset_name,
        asset_url=asset_url,
        asset_size=asset_size,
        repository=repo,
    )


def download_update_asset(
    info: UpdateInfo,
    destination_dir: Path,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    timeout: float = 30.0,
) -> Path:
    """Download the binary asset associated with ``info`` into ``destination_dir``."""

    if not info.asset_url or not info.asset_name:
        raise UpdateError("asset_unavailable")

    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / info.asset_name

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".download", dir=str(destination_dir))
    os.close(tmp_fd)
    tmp_file = Path(tmp_path)

    request = _build_request(info.asset_url)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, tmp_file.open("wb") as handle:
            header = response.getheader("Content-Length")
            total = int(header) if header and header.isdigit() else info.asset_size
            downloaded = 0
            while True:
                chunk = response.read(131072)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
    except urllib.error.URLError as exc:  # pragma: no cover - network failure handling
        tmp_file.unlink(missing_ok=True)
        raise UpdateError(str(exc)) from exc
    except OSError as exc:
        tmp_file.unlink(missing_ok=True)
        raise UpdateError(str(exc)) from exc

    try:
        if target.exists():
            target.unlink()
        tmp_file.replace(target)
    except OSError as exc:
        tmp_file.unlink(missing_ok=True)
        raise UpdateError(str(exc)) from exc

    if progress_callback:
        final_size = target.stat().st_size
        progress_callback(final_size, final_size)

    return target


def find_windows_executable(root: Path) -> Optional[Path]:
    """Locate the most relevant Windows executable in ``root``."""

    if not root.exists():
        return None
    candidates = []
    for path in root.rglob("*.exe"):
        name = path.name.lower()
        score = 0
        if name.startswith("yt-downloader"):
            score += 4
        if "yt" in name and "download" in name:
            score += 3
        if "setup" in name or "installer" in name:
            score += 1
        candidates.append((score, -len(path.parts), path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def install_downloaded_asset(
    download_path: Path,
    version: str,
    install_root: Path,
) -> InstallResult:
    """Install the downloaded archive/executable into ``install_root``."""

    if not download_path.exists():
        raise UpdateError("missing_download")

    install_root.mkdir(parents=True, exist_ok=True)
    version_dir = install_root / version

    if version_dir.exists():
        shutil.rmtree(version_dir, ignore_errors=True)
    version_dir.mkdir(parents=True, exist_ok=True)

    suffix = download_path.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(download_path) as archive:
                archive.extractall(version_dir)
        except zipfile.BadZipFile as exc:
            raise UpdateError("bad_archive") from exc
        executable = find_windows_executable(version_dir)
        return InstallResult(version=version, base_path=version_dir, executable=executable)

    target_path = version_dir / download_path.name
    try:
        shutil.copy2(download_path, target_path)
    except OSError as exc:
        raise UpdateError(str(exc)) from exc

    executable = target_path if target_path.suffix.lower() in {".exe", ".msi"} else None
    return InstallResult(version=version, base_path=version_dir, executable=executable)
