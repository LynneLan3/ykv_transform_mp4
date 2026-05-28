from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import subprocess

from ykv_transform.convert import ConvertError, find_ffmpeg, find_ffprobe, merge_segments
from ykv_transform.resources import SUPPORTED_EXTENSIONS
from ykv_transform.unpack import UnpackError, unpack_ykv

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNPACK = 2
EXIT_CONVERT = 3


@dataclass(frozen=True)
class JobItem:
    input_path: Path
    output_path: Path


@dataclass
class JobResult:
    input_path: Path
    output_path: Path | None
    success: bool
    message: str
    vip_warning: str | None = None
    compatibility_hint: str | None = None
    exit_code: int = EXIT_OK


@dataclass
class BatchSummary:
    results: list[JobResult] = field(default_factory=list)

    @property
    def worst_exit_code(self) -> int:
        if not self.results:
            return EXIT_OK
        return max(result.exit_code for result in self.results)


def resolve_ffmpeg(explicit_path: str | None = None) -> str:
    return find_ffmpeg(explicit_path)


def is_supported_input(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def default_output_path(input_path: Path) -> Path:
    return input_path.with_suffix(".mp4")


def collect_jobs(paths: list[Path]) -> list[JobItem]:
    jobs: list[JobItem] = []
    seen: set[Path] = set()

    for raw_path in paths:
        path = raw_path.resolve()
        if path.is_file():
            if is_supported_input(path):
                if path not in seen:
                    seen.add(path)
                    jobs.append(JobItem(path, default_output_path(path)))
            continue

        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if is_supported_input(candidate):
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        jobs.append(JobItem(resolved, default_output_path(resolved)))

    return jobs


def collect_jobs_from_directory(
    input_dir: Path,
    output_dir: Path,
) -> list[JobItem]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    jobs: list[JobItem] = []

    for candidate in sorted(input_dir.rglob("*")):
        if not is_supported_input(candidate):
            continue
        relative = candidate.relative_to(input_dir)
        jobs.append(JobItem(candidate.resolve(), (output_dir / relative).with_suffix(".mp4")))

    return jobs


def convert_job(
    item: JobItem,
    mode: str,
    ffmpeg_path: str | None = None,
    keep_temp: bool = False,
    force: bool = False,
    progress_callback: Callable[[int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> JobResult:
    if item.output_path.exists() and not force:
        return JobResult(
            input_path=item.input_path,
            output_path=item.output_path,
            success=True,
            message=f"跳过已存在: {item.output_path}",
            exit_code=EXIT_OK,
        )

    temp_dir_obj = tempfile.TemporaryDirectory(prefix="ykv_transform_")
    temp_dir = Path(temp_dir_obj.name)

    try:
        if progress_callback is not None:
            progress_callback(1)
        unpack_result = unpack_ykv(item.input_path, temp_dir)
        if progress_callback is not None:
            progress_callback(15)
        probe = merge_segments(
            unpack_result.segments,
            item.output_path,
            mode=mode,
            ffmpeg_path=ffmpeg_path or resolve_ffmpeg(),
            temp_dir=temp_dir,
            progress_callback=lambda p: progress_callback(min(95, 15 + int(p * 0.8)))
            if progress_callback is not None
            else None,
            cancel_requested=cancel_requested,
        )
        if progress_callback is not None:
            progress_callback(100)

        return JobResult(
            input_path=item.input_path,
            output_path=item.output_path,
            success=True,
            message=f"转换成功: {item.output_path}",
            vip_warning=unpack_result.vip_warning,
            compatibility_hint=probe.get("compatibility_hint"),
            exit_code=EXIT_OK,
        )
    except UnpackError as exc:
        return JobResult(
            input_path=item.input_path,
            output_path=None,
            success=False,
            message=f"解包失败: {exc}",
            exit_code=EXIT_UNPACK,
        )
    except ConvertError as exc:
        return JobResult(
            input_path=item.input_path,
            output_path=None,
            success=False,
            message=f"转换失败: {exc}",
            exit_code=EXIT_CONVERT,
        )
    except Exception as exc:
        return JobResult(
            input_path=item.input_path,
            output_path=None,
            success=False,
            message=f"处理失败: {exc}",
            exit_code=EXIT_CONVERT,
        )
    finally:
        if keep_temp:
            preserved = item.output_path.parent / f".{item.input_path.stem}_temp"
            if preserved.exists():
                shutil.rmtree(preserved, ignore_errors=True)
            if temp_dir.exists():
                shutil.copytree(temp_dir, preserved)
        temp_dir_obj.cleanup()


def run_batch(
    jobs: list[JobItem],
    mode: str,
    ffmpeg_path: str | None = None,
    keep_temp: bool = False,
    force: bool = False,
) -> BatchSummary:
    summary = BatchSummary()
    for item in jobs:
        item.output_path.parent.mkdir(parents=True, exist_ok=True)
        summary.results.append(
            convert_job(
                item,
                mode=mode,
                ffmpeg_path=ffmpeg_path,
                keep_temp=keep_temp,
                force=force,
            )
        )
    return summary


def estimate_job_duration_seconds(item: JobItem, ffmpeg_path: str | None = None) -> float | None:
    temp_dir_obj = tempfile.TemporaryDirectory(prefix="ykv_transform_estimate_")
    temp_dir = Path(temp_dir_obj.name)
    try:
        unpack_result = unpack_ykv(item.input_path, temp_dir)
        ffprobe = find_ffprobe(ffmpeg_path or resolve_ffmpeg())
        total = 0.0
        for seg in unpack_result.segments:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(seg),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            try:
                total += float((result.stdout or "").strip())
            except ValueError:
                return None
        return total if total > 0 else None
    except Exception:
        return None
    finally:
        temp_dir_obj.cleanup()
