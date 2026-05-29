#!/usr/bin/env python3
"""One-command local preflight before pushing and building Windows installer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ykv_transform.convert import find_ffmpeg, find_ffprobe, probe_output
from ykv_transform.service import JobItem, convert_job
from ykv_transform.unpack import unpack_ykv


def run_cmd(args: list[str], label: str) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{label} failed: {detail}")


def create_sample_mp4(path: Path, tone: int = 440) -> None:
    run_cmd(
        [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        "create sample mp4",
    )


def wrap_yk_header(content: bytes) -> bytes:
    if content[:2] == b"YK":
        return content
    return b"YK" + b"\x00" * 32 + content


def build_ykv(files_info: list[dict], output_path: Path) -> None:
    payload = bytearray()
    offset = 0
    index_items: list[dict] = []
    for item in files_info:
        raw = wrap_yk_header(item["content"])
        index_items.append(
            {
                "name": item["name"],
                "offset": offset,
                "size": len(raw),
            }
        )
        payload.extend(raw)
        offset += len(raw)

    json_text = quote(json.dumps(index_items, ensure_ascii=False))
    json_bytes = json_text.encode("utf-8")
    footer = str(len(json_bytes)).encode("utf-8").ljust(16, b"\x00")
    output_path.write_bytes(payload + json_bytes + footer)


def assert_probe_ok(mp4_path: Path) -> None:
    probe = probe_output(mp4_path)
    if not probe["has_video"] or not probe["has_audio"]:
        raise RuntimeError(f"missing streams in output: {mp4_path}")


def assert_video_dts_continuity(mp4_path: Path, max_gap: float = 0.08) -> None:
    ffprobe = find_ffprobe()
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_entries",
            "packet=dts_time",
            "-of",
            "csv=p=0",
            str(mp4_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe packets failed: {result.stderr.strip()}")
    dts_list: list[float] = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            dts_list.append(float(raw))
        except ValueError:
            continue
    if len(dts_list) < 3:
        raise RuntimeError("insufficient video packets for dts continuity check")
    for i in range(1, len(dts_list)):
        delta = dts_list[i] - dts_list[i - 1]
        if delta > max_gap:
            raise RuntimeError(
                f"video dts discontinuity detected at packet {i}: "
                f"{dts_list[i-1]:.6f} -> {dts_list[i]:.6f} (gap={delta:.6f}s)"
            )


def preflight_basic_conversion(temp_root: Path) -> None:
    sample = temp_root / "sample_basic.ykv"
    run_cmd([sys.executable, str(PROJECT_ROOT / "scripts/create_test_ykv.py"), str(sample)], "create test ykv")

    out_copy = temp_root / "out_copy.mp4"
    out_karaoke = temp_root / "out_karaoke.mp4"
    copy_result = convert_job(JobItem(sample, out_copy), mode="copy", force=True)
    if not copy_result.success:
        raise RuntimeError(f"copy mode failed: {copy_result.message}")
    karaoke_result = convert_job(JobItem(sample, out_karaoke), mode="karaoke", force=True)
    if not karaoke_result.success:
        raise RuntimeError(f"karaoke mode failed: {karaoke_result.message}")
    assert_probe_ok(out_copy)
    assert_probe_ok(out_karaoke)
    assert_video_dts_continuity(out_karaoke)


def preflight_m3u8_noise_case(temp_root: Path) -> None:
    first = temp_root / "seg1.mp4"
    second = temp_root / "seg2.mp4"
    create_sample_mp4(first, tone=440)
    create_sample_mp4(second, tone=660)
    m3u8 = b"#EXTM3U\n#EXTINF:1.0,\n1.ts\n"

    # Build index with m3u8 noise + out-of-order media entries.
    noisy_ykv = temp_root / "sample_m3u8_noise.ykv"
    build_ykv(
        [
            {"name": "index.m3u8", "content": m3u8},
            {"name": "2.mp4", "content": second.read_bytes()},
            {"name": "1.mp4", "content": first.read_bytes()},
        ],
        noisy_ykv,
    )

    extracted_dir = temp_root / "unpack_out"
    unpack_result = unpack_ykv(noisy_ykv, extracted_dir)
    if len(unpack_result.segments) < 2:
        raise RuntimeError("expected at least 2 media segments after skipping m3u8 noise")
    for seg in unpack_result.segments:
        if seg.suffix.lower() == ".m3u8":
            raise RuntimeError("m3u8 should not be treated as media segment")

    out_karaoke = temp_root / "out_m3u8_noise.mp4"
    result = convert_job(JobItem(noisy_ykv, out_karaoke), mode="karaoke", force=True)
    if not result.success:
        raise RuntimeError(f"m3u8-noise case failed: {result.message}")
    assert_probe_ok(out_karaoke)
    assert_video_dts_continuity(out_karaoke)


def preflight_playlist_order_case(temp_root: Path) -> None:
    first = temp_root / "order_first.mp4"
    second = temp_root / "order_second.mp4"
    create_sample_mp4(first, tone=440)
    create_sample_mp4(second, tone=880)

    # Playlist requires "second -> first", regardless of physical offset order.
    playlist = b"#EXTM3U\n#EXTINF:1.0,\n2.mp4\n#EXTINF:1.0,\n1.mp4\n"
    sample = temp_root / "sample_playlist_order.ykv"
    build_ykv(
        [
            {"name": "1.mp4", "content": first.read_bytes()},
            {"name": "2.mp4", "content": second.read_bytes()},
            {"name": "index.m3u8", "content": playlist},
        ],
        sample,
    )

    unpack_dir = temp_root / "order_unpack"
    unpack_result = unpack_ykv(sample, unpack_dir)
    if len(unpack_result.segments) < 2:
        raise RuntimeError("playlist-order case did not produce expected segments")

    first_extracted = unpack_result.segments[0].read_bytes()
    second_extracted = unpack_result.segments[1].read_bytes()
    if not first_extracted.startswith(second.read_bytes()[:64]):
        raise RuntimeError("playlist order mismatch: first extracted segment is not 2.mp4")
    if not second_extracted.startswith(first.read_bytes()[:64]):
        raise RuntimeError("playlist order mismatch: second extracted segment is not 1.mp4")


def preflight_existing_scripts() -> None:
    run_cmd([sys.executable, str(PROJECT_ROOT / "scripts/verify_service.py")], "verify_service")
    run_cmd([sys.executable, str(PROJECT_ROOT / "scripts/verify_packaging.py")], "verify_packaging")


def main() -> int:
    print("[preflight] starting local preflight...")
    print("[preflight] checking core toolchain...")
    _ = find_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="ykv_preflight_") as temp_dir:
        temp_root = Path(temp_dir)
        print("[preflight] running basic copy/karaoke conversion checks...")
        preflight_basic_conversion(temp_root)
        print("[preflight] running m3u8-noise regression check...")
        preflight_m3u8_noise_case(temp_root)
        print("[preflight] running playlist-order regression check...")
        preflight_playlist_order_case(temp_root)

    print("[preflight] running existing verification scripts...")
    preflight_existing_scripts()
    print("[preflight] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
