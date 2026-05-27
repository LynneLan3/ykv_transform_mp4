from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_EXTENSIONS = {".ykv", ".kux"}


def runtime_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return PROJECT_ROOT


def bundled_ffmpeg() -> Path | None:
    candidate = runtime_base() / "resources" / "ffmpeg" / "win64" / "ffmpeg.exe"
    if candidate.is_file():
        return candidate
    return None


def bundled_ffprobe() -> Path | None:
    candidate = runtime_base() / "resources" / "ffmpeg" / "win64" / "ffprobe.exe"
    if candidate.is_file():
        return candidate
    return None
