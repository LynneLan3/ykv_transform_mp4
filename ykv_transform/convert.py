from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
import time
from pathlib import Path

from ykv_transform.resources import bundled_ffmpeg, bundled_ffprobe


class ConvertError(Exception):
    """Raised when FFmpeg merge or validation fails."""


SUPPORTED_MODES = {"copy", "karaoke"}


def _subprocess_kwargs() -> dict:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _format_cmd(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in arg else arg for arg in args)


def _ffprobe_sibling(ffmpeg_path: Path) -> Path | None:
    for name in ("ffprobe", "ffprobe.exe"):
        candidate = ffmpeg_path.parent / name
        if candidate.is_file():
            return candidate
    return None


def find_ffmpeg(explicit_path: str | None = None) -> str:
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_file():
            raise ConvertError(f"指定的 ffmpeg 不存在: {explicit_path}")
        return str(path)

    bundled = bundled_ffmpeg()
    if bundled is not None:
        return str(bundled)

    found = shutil.which("ffmpeg")
    if not found:
        raise ConvertError("未找到 ffmpeg，请先安装并加入 PATH")
    return found


def find_ffprobe(explicit_ffmpeg: str | None = None) -> str:
    if explicit_ffmpeg:
        sibling = _ffprobe_sibling(Path(explicit_ffmpeg))
        if sibling is not None:
            return str(sibling)

    bundled = bundled_ffprobe()
    if bundled is not None:
        return str(bundled)

    found = shutil.which("ffprobe")
    if not found and sys.platform == "win32":
        found = shutil.which("ffprobe.exe")
    if not found:
        raise ConvertError("未找到 ffprobe，请先安装并加入 PATH")
    return found


def _write_concat_list(segments: list[Path], list_path: Path) -> None:
    lines = []
    for segment in segments:
        escaped = segment.as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_ffmpeg_args(
    ffmpeg_path: str,
    concat_list: Path,
    output_path: Path,
    mode: str,
) -> list[str]:
    args = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
    ]

    if mode == "copy":
        args.extend(["-c", "copy", "-movflags", "+faststart", "-y", str(output_path)])
    elif mode == "karaoke":
        args.extend(
            [
                "-c:v",
                "libx264",
                "-profile:v",
                "main",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
        )
    else:
        raise ConvertError(f"不支持的输出模式: {mode}")

    return args


def _build_karaoke_filter_args(
    ffmpeg_path: str,
    concat_list: Path,
    output_path: Path,
) -> list[str]:
    args = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
    ]

    args.extend(
        [
            "-vf",
            "setpts=PTS-STARTPTS,fps=25",
            "-af",
            "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0",
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-r",
            "25",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
    )
    return args


def _validate_segment_decoding(
    ffmpeg_path: str,
    segments: list[Path],
) -> None:
    for index, segment in enumerate(segments, start=1):
        args = [
            ffmpeg_path,
            "-v",
            "error",
            "-xerror",
            "-i",
            str(segment),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            **_subprocess_kwargs(),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "无法解码分片"
            raise ConvertError(
                f"[stage=segment-decode] index={index} returncode={result.returncode}\n"
                f"segment={segment}\n"
                f"cmd: {_format_cmd(args)}\n"
                f"detail: 第 {index} 段音视频分片不可解码（可能损坏或受保护）: {detail}"
            )


def _estimate_total_duration_seconds(segments: list[Path], ffprobe_path: str) -> float | None:
    total = 0.0
    for segment in segments:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(segment),
            ],
            capture_output=True,
            text=True,
            check=False,
            **_subprocess_kwargs(),
        )
        if result.returncode != 0:
            return None
        try:
            duration = float((result.stdout or "").strip())
        except ValueError:
            return None
        if duration <= 0:
            return None
        total += duration
    return total if total > 0 else None


def _run_ffmpeg_with_progress(
    args: list[str],
    duration_seconds: float | None,
    progress_callback: Callable[[int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    progress_args = args[:1] + ["-progress", "pipe:1", "-nostats"] + args[1:]
    process = subprocess.Popen(
        progress_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_subprocess_kwargs(),
    )
    assert process.stdout is not None
    assert process.stderr is not None

    total_us = max((duration_seconds or 0.0) * 1_000_000.0, 1.0)
    last_percent = -1
    while True:
        if cancel_requested is not None and cancel_requested():
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            return 255, "用户已取消转换"

        line = process.stdout.readline()
        if line == "":
            if process.poll() is not None:
                break
            time.sleep(0.05)
            continue
        line = line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "out_time_ms" and duration_seconds is not None:
            try:
                out_us = int(value)
            except ValueError:
                continue
            percent = int(max(0.0, min(99.0, (out_us / total_us) * 100.0)))
            if percent > last_percent:
                last_percent = percent
                progress_callback(percent)

    stderr_text = process.stderr.read().strip()
    stdout_tail = process.stdout.read().strip() if process.stdout else ""
    process.wait()
    payload = stderr_text or stdout_tail
    return process.returncode, payload


def probe_output(output_path: Path, ffprobe_path: str | None = None) -> dict:
    ffprobe = ffprobe_path or find_ffprobe()
    args = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        str(output_path),
    ]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        **_subprocess_kwargs(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "ffprobe 校验失败"
        raise ConvertError(
            f"[stage=probe-output] returncode={result.returncode}\n"
            f"cmd: {_format_cmd(args)}\n"
            f"detail: {detail}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ConvertError("ffprobe 输出无法解析") from exc

    streams = payload.get("streams", [])
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    return {"has_video": has_video, "has_audio": has_audio, "streams": streams}


def merge_segments(
    segments: list[Path],
    output_path: Path,
    mode: str = "copy",
    ffmpeg_path: str | None = None,
    temp_dir: Path | None = None,
    progress_callback: Callable[[int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict:
    if mode not in SUPPORTED_MODES:
        raise ConvertError(f"不支持的输出模式: {mode}")
    if not segments:
        raise ConvertError("没有可合并的分片")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    ffprobe = find_ffprobe(ffmpeg)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _validate_segment_decoding(ffmpeg, segments)

    if mode == "karaoke":
        work_dir = temp_dir or output_path.parent
        concat_list = work_dir / "concat_list.txt"
        _write_concat_list(segments, concat_list)
        args = _build_karaoke_filter_args(ffmpeg, concat_list, output_path)
    else:
        work_dir = temp_dir or output_path.parent
        concat_list = work_dir / "concat_list.txt"
        _write_concat_list(segments, concat_list)
        args = _build_ffmpeg_args(ffmpeg, concat_list, output_path, mode)
    duration_seconds = _estimate_total_duration_seconds(segments, ffprobe)
    if progress_callback is not None:
        progress_callback(10)
    returncode, ffmpeg_message = _run_ffmpeg_with_progress(
        args,
        duration_seconds=duration_seconds,
        progress_callback=progress_callback,
        cancel_requested=cancel_requested,
    )
    if progress_callback is not None:
        progress_callback(95)
    if returncode != 0:
        message = ffmpeg_message or "FFmpeg 合并失败"
        raise ConvertError(
            f"[stage=ffmpeg-merge] returncode={returncode}\n"
            f"cmd: {_format_cmd(args)}\n"
            f"detail: {message}"
        )

    probe = probe_output(output_path, ffprobe)
    if progress_callback is not None:
        progress_callback(100)
    if not probe["has_video"]:
        raise ConvertError("输出文件缺少视频流")
    if not probe["has_audio"]:
        raise ConvertError("输出文件缺少音频流")

    if mode == "copy":
        video_codecs = {
            stream.get("codec_name")
            for stream in probe["streams"]
            if stream.get("codec_type") == "video"
        }
        audio_codecs = {
            stream.get("codec_name")
            for stream in probe["streams"]
            if stream.get("codec_type") == "audio"
        }
        unusual_video = video_codecs - {"h264", "hevc", "av1"}
        unusual_audio = audio_codecs - {"aac", "mp3", "ac3"}
        if unusual_video or unusual_audio:
            probe["compatibility_hint"] = (
                "检测到非常规编码，若点唱机无法播放，请尝试 --mode karaoke"
            )

    return probe
