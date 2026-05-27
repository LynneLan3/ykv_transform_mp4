#!/usr/bin/env python3
"""Verify conversion service without GUI dependencies."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ykv_transform.service import JobItem, collect_jobs, convert_job


def main() -> int:
    create_script = PROJECT_ROOT / "scripts" / "create_test_ykv.py"
    with tempfile.TemporaryDirectory() as temp_dir:
        sample = Path(temp_dir) / "sample.ykv"
        output = Path(temp_dir) / "sample.mp4"

        subprocess.run([sys.executable, str(create_script), str(sample)], check=True)

        jobs = collect_jobs([sample])
        if len(jobs) != 1:
            print(f"expected 1 job, got {len(jobs)}")
            return 1

        result = convert_job(
            JobItem(input_path=sample, output_path=output),
            mode="karaoke",
            force=True,
        )
        if not result.success or not output.is_file():
            print(result.message)
            return 1

        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_streams", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            print(probe.stderr)
            return 1

        if "codec_type=video" not in probe.stdout or "codec_type=audio" not in probe.stdout:
            print("output missing video or audio stream")
            return 1

    print("service verification ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
