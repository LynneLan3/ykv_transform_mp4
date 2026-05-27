#!/usr/bin/env python3
"""Create a synthetic YKV container for local verification."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


def create_sample_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
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
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        check=True,
    )


def wrap_yk_header(content: bytes) -> bytes:
    if content[:2] == b"YK":
        return content
    header = b"YK" + b"\x00" * 32
    return header + content


def build_ykv(segments: list[bytes], output_path: Path) -> None:
    files_info: list[dict] = []
    offset = 0
    payload = bytearray()

    for index, segment in enumerate(segments, start=1):
        wrapped = wrap_yk_header(segment)
        files_info.append(
            {
                "name": f"{index}.mp4",
                "offset": offset,
                "size": len(wrapped),
            }
        )
        payload.extend(wrapped)
        offset += len(wrapped)

    json_text = quote(json.dumps(files_info, ensure_ascii=False))
    json_bytes = json_text.encode("utf-8")
    size_footer = str(len(json_bytes)).encode("utf-8").ljust(16, b"\x00")

    output_path.write_bytes(payload + json_bytes + size_footer)


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample.ykv")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        first = temp / "1.mp4"
        second = temp / "2.mp4"
        create_sample_mp4(first)
        create_sample_mp4(second)
        build_ykv([first.read_bytes(), second.read_bytes()], output)

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
