"""Background worker responsible for downloading and transcoding videos."""

from __future__ import annotations

import datetime as _dt
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .localization import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, translate
from .utils import format_timestamp, sanitize_filename, unique_path


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
        self.start_seconds = max(start_seconds, 0.0)
        self.end_seconds = end_seconds
        self.event_queue = event_queue
        self.error: Optional[str] = None
        self.language = language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._cancelled = False

    # pylint: disable=too-many-locals
    def run(self) -> None:  # noqa: C901 - відтворюємо логіку батника для прозорості
        workdir: Optional[Path] = None
        tempdir: Optional[Path] = None
        final_destination: Optional[Path] = None
        cancelled = False
        self._cancelled = False
        try:
            if not self.url:
                raise ValueError(self._t("error_empty_url"))

            if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
                raise ValueError(self._t("error_end_before_start"))

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

            if self.start_seconds > 0 or self.end_seconds is not None:
                human_start = format_timestamp(self.start_seconds)
                if self.end_seconds is not None:
                    human_end = format_timestamp(self.end_seconds)
                else:
                    human_end = self._t("segment_end")
                self._log(self._t("log_segment", start=human_start, end=human_end))

            clip_requested = self.start_seconds > 0 or self.end_seconds is not None
            downloader_args: list[str] = []
            clip_applied_during_download = False
            if clip_requested:
                clip_parts: list[str] = []
                if self.start_seconds > 0:
                    clip_parts.append(f"-ss {format_timestamp(self.start_seconds)}")
                if self.end_seconds is not None:
                    clip_parts.append(f"-to {format_timestamp(self.end_seconds)}")
                if clip_parts:
                    downloader_args = [
                        "--downloader",
                        "ffmpeg",
                        "--downloader-args",
                        f"ffmpeg_i:{' '.join(clip_parts)}",
                    ]
                    clip_applied_during_download = True

            self._log(self._t("log_download_step"))
            yt_dlp_cmd = [
                "yt-dlp",
                "-f",
                "bv*+ba/b",
                "-S",
                "res,fps,br",
                "--hls-prefer-ffmpeg",
                "-N",
                "8",
                "-P",
                str(workdir),
                "--paths",
                f"temp:{tempdir}",
                "-o",
                "source.%(ext)s",
            ]
            yt_dlp_cmd.extend(downloader_args)
            yt_dlp_cmd.append(self.url)
            self._run(yt_dlp_cmd, cwd=workdir)

            template_placeholder = workdir / "source.%(ext)s"
            template_placeholder.unlink(missing_ok=True)

            src = next(workdir.glob("source.*"), None)
            if not src:
                raise RuntimeError(self._t("error_missing_source"))

            self._check_cancelled()
            video_codec, audio_codec = self._probe_codecs(src)
            self._log(self._t("log_codecs", video=video_codec, audio=audio_codec))

            final_path = workdir / f"{sanitized_title}.mp4"
            needs_transcode = not (
                video_codec.lower() == "h264" and audio_codec.lower() == "aac"
            )

            if not needs_transcode and not clip_applied_during_download:
                self._log(self._t("log_skip_transcode"))
                self._check_cancelled()
                src.rename(final_path)
                final_destination = final_path
            else:
                self._status("converting")
                ffmpeg_args = ["ffmpeg", "-hide_banner", "-stats", "-y"]
                clip_during_ffmpeg = (
                    (self.start_seconds > 0 or self.end_seconds is not None)
                    and not clip_applied_during_download
                )
                if clip_during_ffmpeg:
                    ffmpeg_args.extend(["-ss", format_timestamp(self.start_seconds)])
                ffmpeg_args.extend(["-i", str(src)])
                if clip_during_ffmpeg and self.end_seconds is not None:
                    segment = max(self.end_seconds - self.start_seconds, 0.0)
                    ffmpeg_args.extend(["-t", format_timestamp(segment)])

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
                            final_path.name,
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
                            "-movflags",
                            "+faststart",
                            final_path.name,
                        ]
                    )

                self._run(ffmpeg_args, cwd=workdir)
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
                "ffprobe",
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

    def _ensure_metadata(self) -> dict[str, str]:
        if self.title:
            return {"title": self.title}

        output = self._run(
            [
                "yt-dlp",
                "--dump-single-json",
                "--skip-download",
                self.url,
            ],
            capture_output=True,
        )
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"title": "video"}

    def _probe_codecs(self, src: Path) -> tuple[str, str]:
        output = self._run(
            [
                "ffprobe",
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
                "ffprobe",
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
        process = subprocess.Popen(  # noqa: S603 - свідоме виконання зовнішньої команди
            args,
            cwd=cwd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=capture_output,
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
