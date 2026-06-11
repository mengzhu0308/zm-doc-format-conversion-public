#!/usr/bin/env python3
"""
zm-word2image 共享内部工具
被 run.py 与 pdf2image_run.py 复用，不暴露 CLI。
"""

import argparse
import re
import unicodedata
import uuid
from pathlib import Path
from typing import NamedTuple


class Result(NamedTuple):
    status: str
    message: str
    files_created: list[str]
    details: dict


# 路径分隔符 + Windows 保留名字符，统一替换为下划线
_PATH_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]+')

# Windows 设备保留名（case-insensitive，COM1-9 / LPT1-9）
_WINDOWS_RESERVED_STEMS = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})

# --output-dir 白名单：拒绝把图片写入这些敏感系统目录（A-2 P1-1）
# 改为前缀匹配（A-3 P0-1），所以仅保留根路径，不带末尾斜杠。
FORBIDDEN_OUTPUT_DIRS = frozenset({
    "/etc", "/var", "/usr", "/boot", "/proc", "/sys",
    "/root", "/run", "/dev", "/sbin", "/bin", "/lib", "/lib64",
})


def safe_filename(name: str) -> str:
    """
    把字符串转换为对当前平台安全的文件/目录名。
    规则：
    1. 把路径分隔符与 Windows 非法字符替换为下划线
    2. 过滤掉 Unicode 控制/格式/代理/私用/未分配类别
    3. 保留 ASCII 下划线 `_` 和连字符 `-`
    4. 去掉首尾空白
    5. 全部被剥除后回退到 word_<rand8> 占位名
    6. Windows 设备保留名（CON / COM1-9 / LPT1-9 等）加 `_w` 后缀，避免在 Windows 上被拒绝创建
    """
    replaced = _PATH_UNSAFE_RE.sub("_", name)
    cleaned = "".join(
        c for c in replaced
        if unicodedata.category(c)[0] != "C" or c in ("_", "-")
    ).rstrip()
    if not cleaned:
        return f"word_{uuid.uuid4().hex[:8]}"
    # Windows 保留名保护：仅在 stem 命中保留名集合时加 _w 后缀，保留扩展名。
    p = Path(cleaned)
    if p.stem.upper() in _WINDOWS_RESERVED_STEMS:
        return f"{p.stem}_w{p.suffix}"
    return cleaned


def validate_output_dir(value: str) -> str:
    """
    --output-dir 白名单校验：拒绝把图片写入系统敏感目录（A-2 P1-1 + A-3 P0-1）。

    规则：
    1. 相对路径无论存不存在都 resolve 到绝对路径（A-3 P1-2）
    2. 绝对路径直接 resolve 一次去除 `..` / `.` 噪音
    3. 命中 forbidden 前缀（含 equal）时抛 ArgumentTypeError；返回原值供下游使用
    """
    p = Path(value).expanduser()
    # 不论是否绝对、不论是否存在，都 resolve() 一次得到真实绝对路径
    try:
        resolved = p.resolve()
    except OSError:
        # resolve 失败（极端情况：权限、字符集）按原值字符串判 forbidden 前缀
        resolved_str = str(p)
        if any(resolved_str == root or resolved_str.startswith(root + "/") for root in FORBIDDEN_OUTPUT_DIRS):
            raise argparse.ArgumentTypeError(
                f"--output-dir 不允许写入系统敏感目录 {value}；"
                f"请改用 ~/、./、/tmp/ 等用户级或临时目录"
            )
        return value
    resolved_str = str(resolved)
    for root in FORBIDDEN_OUTPUT_DIRS:
        if resolved_str == root or resolved_str.startswith(root + "/"):
            raise argparse.ArgumentTypeError(
                f"--output-dir 不允许写入系统敏感目录 {resolved_str}；"
                f"请改用 ~/、./、/tmp/ 等用户级或临时目录"
            )
    return value


def read_skill_version() -> str:
    """从 VERSION.yaml 读取 skill_info.version；解析失败时回退 'unknown'。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        return _read_skill_version_fallback()
    try:
        version_yaml = Path(__file__).resolve().parent.parent / "VERSION.yaml"
        with open(version_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return str(data.get("skill_info", {}).get("version", "unknown"))
    except Exception:
        return "unknown"


def _read_skill_version_fallback() -> str:
    """无 PyYAML 时用简易解析（不依赖外部库），仅识别顶层 skill_info.version。"""
    try:
        version_yaml = Path(__file__).resolve().parent.parent / "VERSION.yaml"
        text = version_yaml.read_text(encoding="utf-8")
    except Exception:
        return "unknown"
    in_skill_info = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("skill_info"):
            in_skill_info = True
            continue
        if in_skill_info and stripped and not line.startswith(" "):
            in_skill_info = False
        if in_skill_info and stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return "unknown"

