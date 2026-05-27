#!/usr/bin/env python3
"""Verify packaging artifacts and core conversion flow without a GUI runtime."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = [
    "gui_main.py",
    "ykv_transform/gui_app.py",
    "ykv_transform/service.py",
    "ykv_transform/resources.py",
    "packaging/ykv_transform.spec",
    "packaging/installer.iss",
    "packaging/build_windows.ps1",
    ".github/workflows/build-windows.yml",
]


def check_files() -> None:
    missing = [rel for rel in REQUIRED_FILES if not (PROJECT_ROOT / rel).is_file()]
    if missing:
        raise SystemExit(f"Missing required files: {', '.join(missing)}")
    print("packaging files: ok")


def check_cli_flow() -> None:
    sample = Path("/tmp/sample.ykv")
    if not sample.is_file():
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts/create_test_ykv.py"), str(sample)],
            check=True,
        )

    output = Path("/tmp/verify_out.mp4")
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "convert",
            str(sample),
            "-o",
            str(output),
            "--mode",
            "karaoke",
            "--force",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout or "CLI convert failed")
    if not output.is_file():
        raise SystemExit("CLI output mp4 not created")
    print("cli convert: ok")


def check_service_api() -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from ykv_transform.service import collect_jobs

    jobs = collect_jobs([Path("/tmp/sample.ykv")])
    if not jobs:
        raise SystemExit("collect_jobs returned empty list")
    if jobs[0].output_path.suffix != ".mp4":
        raise SystemExit("collect_jobs output path invalid")
    print("service api: ok")


def check_gui_import() -> None:
    try:
        from ykv_transform.gui_app import MainWindow, run_gui  # noqa: F401
    except ModuleNotFoundError as exc:
        print(f"gui import skipped: {exc}")
        return
    print("gui import: ok")


def main() -> int:
    check_files()
    check_cli_flow()
    check_service_api()
    check_gui_import()
    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
