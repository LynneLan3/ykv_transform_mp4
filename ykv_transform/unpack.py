from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


class UnpackError(Exception):
    """Raised when a YKV file cannot be unpacked."""


@dataclass(frozen=True)
class UnpackResult:
    segments: list[Path]
    segment_ext: str
    vip_warning: str | None = None


def _read_last_bytes(input_file: Path, num_bytes: int) -> bytes:
    with input_file.open("rb") as handle:
        handle.seek(-num_bytes, 2)
        return handle.read()


def _check_vip_warning(files_info: list[dict]) -> str | None:
    for file_info in files_info:
        if file_info.get("name") != "dbInfo":
            continue
        try:
            pay_info = json.loads(
                file_info["info"]["configInfo"]["ups"]["data"]["data"]["controller"][
                    "pay_info_ext"
                ]
            )["stage"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
        if pay_info:
            return "检测到 VIP/付费内容标记，转换后可能无法播放。"
    return None


def unpack_ykv(input_path: Path, temp_dir: Path) -> UnpackResult:
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise UnpackError(f"输入文件不存在: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix not in {".ykv", ".kux"}:
        raise UnpackError(f"不支持的文件扩展名: {suffix}")

    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        last_16_bytes = _read_last_bytes(input_path, 16)
        size_info = last_16_bytes.decode("utf-8").strip()
        json_size = int(size_info.split("\x00")[0].strip())
    except (UnicodeDecodeError, ValueError) as exc:
        raise UnpackError("无法解析 YKV 尾部索引长度") from exc

    with input_path.open("rb") as packed_file:
        packed_file.seek(-(16 + json_size), 2)
        json_data = packed_file.read(json_size)

    try:
        decoded_json = unquote(json_data.decode("utf-8"))
        files_info = json.loads(decoded_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnpackError("无法解析 YKV 尾部 JSON 索引") from exc

    if not isinstance(files_info, list):
        raise UnpackError("YKV 索引格式无效")

    vip_warning = _check_vip_warning(files_info)

    segments: list[Path] = []
    segment_ext = ""
    segment_index = 0

    with input_path.open("rb") as packed_file:
        for file_info in files_info:
            filename = file_info.get("name")
            if filename == "dbInfo":
                continue

            try:
                offset = file_info["offset"]
                size = file_info["size"]
            except KeyError as exc:
                raise UnpackError("YKV 分片索引缺少 offset 或 size") from exc

            packed_file.seek(offset)
            content = packed_file.read(size)

            if content[:2] == b"YK":
                content = content[34:]

            segment_index += 1
            segment_ext = filename.rsplit(".", 1)[-1]
            output_file = temp_dir / f"{segment_index}.{segment_ext}"
            output_file.write_bytes(content)
            segments.append(output_file)

    if not segments:
        raise UnpackError("未从 YKV 文件中提取到任何视频分片")

    return UnpackResult(
        segments=segments,
        segment_ext=segment_ext,
        vip_warning=vip_warning,
    )
