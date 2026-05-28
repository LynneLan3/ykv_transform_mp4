from __future__ import annotations

import json
import shutil
import subprocess
import sys
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
    segments: list[Path],
    output_path: Path,
) -> list[str]:
    args = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    for segment in segments:
        args.extend(["-i", str(segment)])

    concat_inputs = "".join(f"[{idx}:v:0][{idx}:a:0]" for idx in range(len(segments)))
    filter_graph = f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[v][a]"
    args.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-map",
            "[a]",
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
    return args


def probe_output(output_path: Path, ffprobe_path: str | None = None) -> dict:
    ffprobe = ffprobe_path or find_ffprobe()
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        **_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise ConvertError(result.stderr.strip() or "ffprobe 校验失败")

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
) -> dict:
    if mode not in SUPPORTED_MODES:
        raise ConvertError(f"不支持的输出模式: {mode}")
    if not segments:
        raise ConvertError("没有可合并的分片")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    ffprobe = find_ffprobe(ffmpeg)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "karaoke":
        args = _build_karaoke_filter_args(ffmpeg, segments, output_path)
    else:
        work_dir = temp_dir or output_path.parent
        concat_list = work_dir / "concat_list.txt"
        _write_concat_list(segments, concat_list)
        args = _build_ffmpeg_args(ffmpeg, concat_list, output_path, mode)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        **_subprocess_kwargs(),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "FFmpeg 合并失败"
        raise ConvertError(message)

    probe = probe_output(output_path, ffprobe)
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
