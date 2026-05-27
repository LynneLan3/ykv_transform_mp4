from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ykv_transform.service import (
    EXIT_OK,
    EXIT_UNPACK,
    EXIT_USAGE,
    JobItem,
    collect_jobs_from_directory,
    convert_job,
    default_output_path,
)


def _print_result(result) -> int:
    if result.vip_warning:
        print(f"警告: {result.vip_warning}", file=sys.stderr)
    if result.success:
        print(result.message)
        if result.compatibility_hint:
            print(f"提示: {result.compatibility_hint}", file=sys.stderr)
        return EXIT_OK

    print(result.message, file=sys.stderr)
    return result.exit_code


def _batch_convert(
    input_dir: Path,
    output_dir: Path,
    mode: str,
    ffmpeg_path: str | None,
    keep_temp: bool,
    force: bool,
) -> int:
    if not input_dir.is_dir():
        print(f"输入目录不存在: {input_dir}", file=sys.stderr)
        return EXIT_USAGE

    jobs = collect_jobs_from_directory(input_dir, output_dir)
    if not jobs:
        print(f"未找到 YKV/KUX 文件: {input_dir}", file=sys.stderr)
        return EXIT_USAGE

    output_dir.mkdir(parents=True, exist_ok=True)
    worst_exit = EXIT_OK
    for job in jobs:
        print(f"处理: {job.input_path}")
        result = convert_job(
            job,
            mode=mode,
            ffmpeg_path=ffmpeg_path,
            keep_temp=keep_temp,
            force=force,
        )
        exit_code = _print_result(result)
        if exit_code != EXIT_OK:
            worst_exit = max(worst_exit, exit_code)

    return worst_exit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ykv-transform",
        description="将优酷 YKV/KUX 容器转换为 MP4",
    )
    parser.add_argument(
        "--ffmpeg",
        help="ffmpeg 可执行文件路径（默认优先使用内置或 PATH）",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser("convert", help="转换单个文件")
    convert_parser.add_argument("input", type=Path, help="输入 .ykv/.kux 文件")
    convert_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出 .mp4 文件（默认同目录同名）",
    )
    convert_parser.add_argument(
        "--mode",
        choices=["copy", "karaoke"],
        default="copy",
        help="copy=无损封装，karaoke=H.264+AAC 重编码",
    )
    convert_parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留解包后的中间分片",
    )
    convert_parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的输出文件",
    )

    batch_parser = subparsers.add_parser("batch", help="批量转换目录")
    batch_parser.add_argument("input_dir", type=Path, help="输入目录")
    batch_parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="输出目录",
    )
    batch_parser.add_argument(
        "--mode",
        choices=["copy", "karaoke"],
        default="copy",
        help="copy=无损封装，karaoke=H.264+AAC 重编码",
    )
    batch_parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留解包后的中间分片",
    )
    batch_parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的输出文件",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "convert":
        input_path = args.input.resolve()
        output_path = (
            args.output.resolve()
            if args.output
            else default_output_path(input_path)
        )
        result = convert_job(
            JobItem(input_path=input_path, output_path=output_path),
            mode=args.mode,
            ffmpeg_path=args.ffmpeg,
            keep_temp=args.keep_temp,
            force=args.force,
        )
        return _print_result(result)

    if args.command == "batch":
        return _batch_convert(
            args.input_dir.resolve(),
            args.output_dir.resolve(),
            mode=args.mode,
            ffmpeg_path=args.ffmpeg,
            keep_temp=args.keep_temp,
            force=args.force,
        )

    parser.print_help()
    return EXIT_USAGE
