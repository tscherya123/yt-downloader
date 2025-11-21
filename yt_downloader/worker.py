"""Background worker responsible for downloading and transcoding videos."""

from __future__ import annotations

import datetime as _dt
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from .backend import BackendError, download_video, fetch_video_metadata
from .localization import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, translate
from .logger import get_logger
from .utils import format_timestamp, resolve_executable, sanitize_filename, unique_path


LOGGER = get_logger("Worker")


class DownloadCancelled(Exception):
    """Виняток, що сигналізує про скасування завантаження."""


class DownloadWorker(threading.Thread):
    """Потік, що виконує повний цикл завантаження й перекодування."""

    def __init__(
        self,
        *,
        task_id: str,
        url: str,
        root: Path,
        title: Optional[str],
        separate_folder: bool,
        convert_to_mp4: bool,
        start_seconds: float,
        end_seconds: Optional[float],
        event_queue: "queue.Queue[dict[str, object]]",
        language: str,
    ) -> None:
        super().__init__(daemon=True)
        self.task_id = task_id
        self.url = url.strip()
        self.root = root
        self.title = title
        self.separate_folder = separate_folder
        self.convert_to_mp4 = convert_to_mp4
        self.start_seconds = max(start_seconds, 0.0)
        self.end_seconds = end_seconds
        self.event_queue = event_queue
        self.error: Optional[str] = None
        self.language = language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._cancelled = False
        self._ffmpeg_path: Optional[Path] = None
        self._ffprobe_path: Optional[Path] = None

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - відтворюємо логіку батника для прозорості
        workdir: Optional[Path] = None
        tempdir: Optional[Path] = None
        final_destination: Optional[Path] = None
        cancelled = False
        self._cancelled = False
        LOGGER.info("Starting task %s for URL %s", self.task_id, self.url)
        try:
            if not self.url:
                raise ValueError(self._t("error_empty_url"))

            if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
                raise ValueError(self._t("error_end_before_start"))

            def progress_hook(d: dict[str, object]) -> None:
                if d.get("status") == "downloading":
                    progress_value = 0.0
                    try:
                        downloaded = float(d.get("downloaded_bytes") or 0)
                        total = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                        if total > 0:
                            progress_value = (downloaded / total) * 100
                    except (TypeError, ValueError):
                        progress_value = 0.0

                    if progress_value <= 0:
                        try:
                            fragment_index = float(d.get("fragment_index") or 0)
                            fragment_count = float(d.get("fragment_count") or 0)
                            if fragment_index > 0 and fragment_count > 0:
                                progress_value = min((fragment_index / fragment_count) * 100, 100.0)
                        except (TypeError, ValueError):
                            progress_value = 0.0

                    speed_display = "-"
                    speed_value = d.get("speed")
                    if speed_value is not None:
                        try:
                            speed_mib = float(speed_value) / 1024 / 1024
                            speed_display = f"{speed_mib:.1f} MiB/s"
                        except (TypeError, ValueError):
                            pass

                    self._emit(
                        "progress",
                        progress=progress_value,
                        speed=speed_display,
                        status="downloading",
                    )
                elif d.get("status") == "finished":
                    self._emit("progress", progress=100, status="converting")

            self._initialize_backends()

            self._log(self._t("log_root", root=self.root))
            self.root.mkdir(parents=True, exist_ok=True)
            self._check_cancelled()

            timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            workdir = self.root / f"DL_{timestamp}_{self.task_id.replace('-', '_')}"
            tempdir = workdir / "temp"
            tempdir.mkdir(parents=True, exist_ok=True)
            self._log(self._t("log_workdir", folder=workdir))
            self._status("downloading")
            self._check_cancelled()

            meta = self._ensure_metadata()
            title = meta.get("title") or "video"
            sanitized_title = sanitize_filename(title)
            self._log(self._t("log_title", title=title))
            self._emit("title", title=title)
            self._check_cancelled()

            try:
                duration = float(meta.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0

            is_start_zero = self.start_seconds < 0.5
            is_end_full = self.end_seconds is None or (
                duration > 0 and self.end_seconds >= duration - 0.5
            )
            clip_requested = not (is_start_zero and is_end_full)

            if clip_requested:
                human_start = format_timestamp(self.start_seconds)
                if self.end_seconds is not None:
                    human_end = format_timestamp(self.end_seconds)
                else:
                    human_end = self._t("segment_end")
                self._log(self._t("log_segment", start=human_start, end=human_end))
            self._log(self._t("log_download_step"))
            try:
                src = download_video(
                    url=self.url,
                    workdir=workdir,
                    tempdir=tempdir,
                    clip_start=self.start_seconds if clip_requested else None,
                    clip_end=self.end_seconds if clip_requested else None,
                    progress_hooks=[progress_hook],
                )
                LOGGER.info("yt-dlp download finished for task %s at %s", self.task_id, src)
            except BackendError as exc:
                raise RuntimeError(str(exc)) from exc

            clip_applied_during_download = clip_requested

            if not src:
                raise RuntimeError(self._t("error_missing_source"))

            self._check_cancelled()
            video_codec, audio_codec = self._probe_codecs(src)
            self._log(self._t("log_codecs", video=video_codec, audio=audio_codec))

            if self.convert_to_mp4:
                final_path = workdir / f"{sanitized_title}.mp4"
                needs_transcode = not (
                    video_codec.lower() == "h264" and audio_codec.lower() == "aac"
                )
            else:
                suffix = src.suffix
                final_name = f"{sanitized_title}{suffix}" if suffix else sanitized_title
                final_path = workdir / final_name
                needs_transcode = False

            if not needs_transcode and not clip_applied_during_download:
                self._log(self._t("log_skip_transcode"))
                self._check_cancelled()
                if src.name == final_path.name:
                    final_destination = final_path
                else:
                    src.rename(final_path)
                    final_destination = final_path
            else:
                self._status("converting")
                ffmpeg_args = [self._ffmpeg(), "-hide_banner", "-stats", "-y"]
                clip_during_ffmpeg = clip_requested and not clip_applied_during_download
                if clip_during_ffmpeg:
                    ffmpeg_args.extend(["-ss", format_timestamp(self.start_seconds)])
                ffmpeg_args.extend(["-i", str(src)])
                if clip_during_ffmpeg and self.end_seconds is not None:
                    segment = max(self.end_seconds - self.start_seconds, 0.0)
                    ffmpeg_args.extend(["-t", format_timestamp(segment)])

                output_path = final_path
                if output_path == src:
                    temp_suffix = final_path.suffix
                    temp_stem = final_path.stem or "output"
                    temp_name = f"{temp_stem}_tmp{temp_suffix}" if temp_suffix else f"{temp_stem}_tmp"
                    output_path = workdir / temp_name

                if needs_transcode:
                    vbit = self._compute_vbit(src)
                    self._log(self._t("log_target_bitrate", bitrate=vbit))
                    self._log(self._t("log_transcoding"))
                    ffmpeg_args.extend(
                        [
                            "-c:v",
                            "libx264",
                            "-preset",
                            "slow",
                            "-pix_fmt",
                            "yuv420p",
                            "-b:v",
                            vbit,
                            "-minrate",
                            vbit,
                            "-maxrate",
                            vbit,
                            "-bufsize",
                            "100M",
                            "-profile:v",
                            "high",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "320k",
                            "-movflags",
                            "+faststart",
                            output_path.name,
                        ]
                    )
                else:
                    self._log(self._t("log_copy_streams"))
                    ffmpeg_args.extend(
                        [
                            "-c:v",
                            "copy",
                            "-c:a",
                            "copy",
                        ]
                    )
                    if self.convert_to_mp4:
                        ffmpeg_args.extend(["-movflags", "+faststart"])
                    ffmpeg_args.append(output_path.name)

                self._run(ffmpeg_args, cwd=workdir)
                if output_path != final_path:
                    if final_path.exists():
                        final_path.unlink()
                    output_path.rename(final_path)
                final_destination = final_path

            if final_destination is None:
                raise RuntimeError(self._t("error_final_file"))

            self._check_cancelled()
            if not self.separate_folder:
                library = self.root / "YT_DOWNLOADER_FILES"
                library.mkdir(parents=True, exist_ok=True)
                target_path = unique_path(library / final_destination.name)
                shutil.move(str(final_destination), target_path)
                final_destination = target_path

            self._check_cancelled()
            self._status("done")
            self._emit("done", path=str(final_destination))
            self._log(self._t("log_done_path", path=final_destination))
            LOGGER.info(
                "Task %s completed successfully at %s", self.task_id, final_destination
            )
        except DownloadCancelled:
            cancelled = True
            self._cancelled = True
            self._status("cancelled")
            self.error = None
            self._log(self._t("log_cancelled"))
        except Exception as exc:  # pylint: disable=broad-except
            self.error = str(exc)
            self._status("error")
            self._emit("error", error=self.error)
            self._log(self._t("log_error_message", error=self.error))
            LOGGER.error(
                "Task %s failed with an unexpected error", self.task_id, exc_info=True
            )
        finally:
            self._emit("finished", cancelled=cancelled, error=self.error)
            if tempdir is not None:
                shutil.rmtree(tempdir, ignore_errors=True)
            if workdir is not None and (cancelled or not self.separate_folder):
                shutil.rmtree(workdir, ignore_errors=True)

    def _compute_vbit(self, src: Path) -> str:
        # Обчислюємо тривалість (у секундах) і розмір файлу (у байтах), щоб оцінити бітрейт
        duration = self._run(
            [
                self._ffprobe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
        ).strip()

        try:
            dur_value = max(float(duration), 1.0)
        except ValueError:
            dur_value = 1.0

        file_size = src.stat().st_size
        total = int((file_size * 8) // dur_value)

        # Повторюємо арифметику зі старого батника
        audio = 320_000
        video = max(800_000, total - audio)
        headroom = int(video * 1.15)
        mbit = max((headroom + 999_999) // 1_000_000, 4)
        return f"{mbit}M"

    def _ensure_metadata(self) -> dict[str, Any]:
        try:
            return fetch_video_metadata(self.url)
        except Exception as exc:  # noqa: BLE001 - propagated below
            if self.title:
                LOGGER.warning("Failed to fetch metadata, using cached title: %s", exc)
                return {"title": self.title}
            raise RuntimeError(str(exc)) from exc

    def _probe_codecs(self, src: Path) -> tuple[str, str]:
        output = self._run(
            [
                self._ffprobe(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
        )
        video_codec = output.strip()

        output = self._run(
            [
                self._ffprobe(),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
        )
        audio_codec = output.strip()

        return video_codec or "unknown", audio_codec or "unknown"

    def _run(
        self,
        args: list[str],
        *,
        cwd: Optional[Path] = None,
        capture_output: bool = False,
    ) -> str:
        self._check_cancelled()
        creationflags = 0
        startupinfo = None
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(  # noqa: S603 - свідоме виконання зовнішньої команди
            args,
            cwd=cwd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=capture_output,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        with self._process_lock:
            self._active_process = process
        stdout: Optional[str] = ""
        stderr: Optional[str] = ""
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if self._cancel_event.is_set():
                        try:
                            process.kill()
                        except Exception:
                            pass
                        stdout, stderr = process.communicate()
                        break

            if self._cancel_event.is_set():
                raise DownloadCancelled()

            if process.returncode and process.returncode != 0:
                raise subprocess.CalledProcessError(
                    process.returncode,
                    args,
                    output=stdout,
                    stderr=stderr,
                )

            if capture_output:
                return stdout or ""
            return ""
        except subprocess.CalledProcessError as exc:
            if self._cancel_event.is_set():
                raise DownloadCancelled() from exc
            raise
        finally:
            with self._process_lock:
                self._active_process = None

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._process_lock:
            process = self._active_process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise DownloadCancelled()

    def _initialize_backends(self) -> None:
        self._ffmpeg_path = resolve_executable("ffmpeg.exe", "ffmpeg")
        if self._ffmpeg_path is None:
            raise RuntimeError(self._t("error_missing_ffmpeg"))

        self._ffprobe_path = resolve_executable("ffprobe.exe", "ffprobe")
        if self._ffprobe_path is None:
            raise RuntimeError(self._t("error_missing_ffprobe"))

        self._ensure_backends_on_path()

    def _ensure_backends_on_path(self) -> None:
        directories = []
        if self._ffmpeg_path is not None:
            directories.append(self._ffmpeg_path.parent)
        if self._ffprobe_path is not None:
            directories.append(self._ffprobe_path.parent)

        if not directories:
            return

        existing = os.environ.get("PATH")
        segments = existing.split(os.pathsep) if existing else []
        added = False
        for directory in directories:
            candidate = str(directory)
            if candidate not in segments:
                segments.insert(0, candidate)
                added = True
        if added:
            os.environ["PATH"] = os.pathsep.join(segments)

    def _ffmpeg(self) -> str:
        if self._ffmpeg_path is None or not self._ffmpeg_path.exists():
            self._ffmpeg_path = resolve_executable("ffmpeg.exe", "ffmpeg")
            if self._ffmpeg_path is not None:
                self._ensure_backends_on_path()
        if self._ffmpeg_path is None:
            raise RuntimeError(self._t("error_missing_ffmpeg"))
        return str(self._ffmpeg_path)

    def _ffprobe(self) -> str:
        if self._ffprobe_path is None or not self._ffprobe_path.exists():
            self._ffprobe_path = resolve_executable("ffprobe.exe", "ffprobe")
            if self._ffprobe_path is not None:
                self._ensure_backends_on_path()
        if self._ffprobe_path is None:
            raise RuntimeError(self._t("error_missing_ffprobe"))
        return str(self._ffprobe_path)

    def _log(self, message: str) -> None:
        self._emit("log", message=message)

    def _status(self, status: str) -> None:
        self._emit("status", status=status)

    def _emit(self, event_type: str, **payload: object) -> None:
        data: dict[str, object] = {"task_id": self.task_id, "type": event_type}
        data.update(payload)
        self.event_queue.put(data)

    def _t(self, key: str, **kwargs: object) -> str:
        return translate(self.language, key, **kwargs)
