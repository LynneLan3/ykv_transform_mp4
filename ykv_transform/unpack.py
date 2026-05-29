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


SKIP_INDEX_EXTENSIONS = {
    "m3u8",
    "txt",
    "json",
    "xml",
    "cfg",
    "ini",
    "db",
}


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


def _basename_lower(name: str) -> str:
    normalized = name.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1].lower()


def _parse_m3u8_sequence(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return []
    sequence: list[str] = []
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        sequence.append(_basename_lower(item))
    return sequence


def unpack_ykv(input_path: Path, temp_dir: Path) -> UnpackResult:
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise UnpackError(f"输入文件不存在: {input_path}")
    file_size = input_path.stat().st_size
    if file_size < 16:
        raise UnpackError("输入文件过小，无法读取 YKV 尾部索引")

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
    if json_size <= 0:
        raise UnpackError("YKV 尾部索引长度无效")
    if json_size > file_size - 16:
        raise UnpackError("YKV 尾部索引长度越界")

    try:
        with input_path.open("rb") as packed_file:
            packed_file.seek(-(16 + json_size), 2)
            json_data = packed_file.read(json_size)
    except OSError as exc:
        raise UnpackError("读取 YKV 尾部索引失败") from exc

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
    media_entries: list[dict] = []
    seen_ranges: set[tuple[int, int]] = set()
    playlist_order: list[str] = []

    try:
        for file_info in files_info:
            filename = file_info.get("name")
            if filename == "dbInfo":
                continue
            name = str(filename or "")
            lowered = name.lower()
            if "." in lowered:
                ext = lowered.rsplit(".", 1)[-1]
                if ext in SKIP_INDEX_EXTENSIONS:
                    if ext == "m3u8":
                        try:
                            with input_path.open("rb") as packed_file:
                                packed_file.seek(file_info["offset"])
                                m3u8_raw = packed_file.read(file_info["size"])
                            playlist_order.extend(_parse_m3u8_sequence(m3u8_raw))
                        except Exception:
                            # Do not fail conversion only because playlist parsing failed.
                            pass
                    # Skip text/index artifacts (e.g. m3u8 playlist) to avoid
                    # feeding non-media files into FFmpeg concat/filter inputs.
                    continue

            try:
                offset = file_info["offset"]
                size = file_info["size"]
            except KeyError as exc:
                raise UnpackError("YKV 分片索引缺少 offset 或 size") from exc

            if not isinstance(offset, int) or not isinstance(size, int):
                raise UnpackError("YKV 分片索引 offset/size 类型无效")
            if offset < 0 or size <= 0:
                raise UnpackError("YKV 分片索引 offset/size 数值无效")
            if offset + size > file_size:
                raise UnpackError("YKV 分片索引越界")

            key = (offset, size)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            media_entries.append({"name": name, "offset": offset, "size": size})

        if playlist_order:
            by_basename: dict[str, list[dict]] = {}
            for entry in media_entries:
                key = _basename_lower(entry["name"])
                by_basename.setdefault(key, []).append(entry)

            ordered: list[dict] = []
            used_ids: set[int] = set()
            for base in playlist_order:
                candidates = by_basename.get(base, [])
                for candidate in candidates:
                    cid = id(candidate)
                    if cid in used_ids:
                        continue
                    ordered.append(candidate)
                    used_ids.add(cid)
                    break

            # Keep unmatched entries as fallback to avoid dropping media data.
            remaining = [entry for entry in media_entries if id(entry) not in used_ids]
            remaining.sort(key=lambda item: item["offset"])
            media_entries = ordered + remaining
        else:
            media_entries.sort(key=lambda item: item["offset"])

        with input_path.open("rb") as packed_file:
            for entry in media_entries:
                packed_file.seek(entry["offset"])
                content = packed_file.read(entry["size"])
                if len(content) != entry["size"]:
                    raise UnpackError("YKV 分片读取不完整")

                if content[:2] == b"YK":
                    if len(content) < 34:
                        raise UnpackError("YKV 分片头部长度异常")
                    content = content[34:]

                segment_index += 1
                name = str(entry["name"] or "")
                if "." in name:
                    segment_ext = name.rsplit(".", 1)[-1]
                else:
                    segment_ext = "mp4"
                output_file = temp_dir / f"{segment_index}.{segment_ext}"
                output_file.write_bytes(content)
                segments.append(output_file)
    except OSError as exc:
        raise UnpackError("读取 YKV 分片失败") from exc

    if not segments:
        raise UnpackError("未从 YKV 文件中提取到任何视频分片")

    return UnpackResult(
        segments=segments,
        segment_ext=segment_ext,
        vip_warning=vip_warning,
    )
