#!/usr/bin/env python3
"""Headless verification for service layer and packaging metadata."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def check_files() -> None:
    required = [
        PROJECT_ROOT / "gui_main.py",
        PROJECT_ROOT / "ykv_transform" / "gui_app.py",
        PROJECT_ROOT / "ykv_transform" / "service.py",
        PROJECT_ROOT / "ykv_transform" / "resources.py",
        PROJECT_ROOT / "packaging" / "ykv_transform.spec",
        PROJECT_ROOT / "packaging" / "installer.iss",
        PROJECT_ROOT / "packaging" / "build_windows.ps1",
        PROJECT_ROOT / ".github" / "workflows" / "build-windows.yml",
    ]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Missing files:\n" + "\n".join(str(path) for path in missing))


def check_service() -> None:
    from ykv_transform.service import JobItem, collect_jobs, convert_job

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        sample = temp / "sample.ykv"
        output = temp / "verify_gui_logic.mp4"
        create_script = PROJECT_ROOT / "scripts" / "create_test_ykv.py"
        # Build a disposable sample to avoid relying on host-specific /tmp state.
        subprocess.run([sys.executable, str(create_script), str(sample)], check=True)

        jobs = collect_jobs([sample])
        assert len(jobs) == 1
        result = convert_job(
            JobItem(jobs[0].input_path, output),
            mode="karaoke",
            force=True,
        )
        if not result.success:
            raise SystemExit(result.message)


def check_gui_import() -> None:
    try:
        from ykv_transform.gui_app import MainWindow, run_gui  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print("PySide6 not installed; skip GUI import check.")
            return
        raise


def main() -> int:
    check_files()
    check_service()
    check_gui_import()
    print("verify_windows_package: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
