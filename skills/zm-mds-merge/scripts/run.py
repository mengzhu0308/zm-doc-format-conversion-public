#!/usr/bin/env python3
"""
zm-mds-merge: 将多个 Markdown 文件按标题结构智能合并为一个统一文件。
支持多文件、目录、清单文件三种输入模式，以及 PDF/图片页面对齐。
纯标准库实现，无外部依赖。
"""

import argparse
import errno
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# 正则模式
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
# HTML 标题识别：<h1>–<h6>...</h1>–</h6>，只匹配简单形式
HTML_HEADING_PATTERN = re.compile(r"^\s*<h([1-6])>(.+?)</h[1-6]>\s*$", re.IGNORECASE | re.MULTILINE)
FENCE_PATTERN = re.compile(r"^(?:`{3,}|~{3,})")
PAGE_NUMBER_PATTERN = re.compile(r"[-_](\d+)")

SUPPORTED_SUFFIXES = (".md", ".markdown")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# 失败状态码集中定义，便于 SKILL.md 失败口径表与 main() 退出码统一引用
STATUS_SUCCESS = "success"
STATUS_MISSING_INPUT = "missing_input"
STATUS_EMPTY_INPUT_DIR = "empty_input_dir"
STATUS_UNSUPPORTED_FORMAT = "unsupported_format"
STATUS_MANIFEST_PARSE_FAILED = "manifest_parse_failed"
STATUS_MANIFEST_EMPTY = "manifest_empty"
STATUS_OUTPUT_DIR_CREATE_FAILED = "output_dir_create_failed"
STATUS_SAVE_FAILED = "save_failed"
STATUS_ALIGN_SOURCE_NOT_FOUND = "align_source_not_found"
STATUS_ALIGN_SOURCE_INVALID_TYPE = "align_source_invalid_type"
STATUS_READ_FAILED = "read_failed"
STATUS_SYMLINK_NOT_SUPPORTED = "symlink_not_supported"
STATUS_MANIFEST_NOT_FOUND = "manifest_not_found"
STATUS_PARTIAL = "partial"

# 失败状态码 → 退出码；任何失败统一返回 1
FAILURE_STATUSES = frozenset(
    {
        STATUS_MISSING_INPUT,
        STATUS_EMPTY_INPUT_DIR,
        STATUS_UNSUPPORTED_FORMAT,
        STATUS_MANIFEST_PARSE_FAILED,
        STATUS_MANIFEST_EMPTY,
        STATUS_OUTPUT_DIR_CREATE_FAILED,
        STATUS_SAVE_FAILED,
        STATUS_ALIGN_SOURCE_NOT_FOUND,
        STATUS_ALIGN_SOURCE_INVALID_TYPE,
        STATUS_READ_FAILED,
        STATUS_SYMLINK_NOT_SUPPORTED,
        STATUS_MANIFEST_NOT_FOUND,
    }
)


class Result(NamedTuple):
    status: str
    message: str | None
    output_file: str | None
    details: dict


def normalize_newlines(text: str) -> str:
    """统一换行符为 \n，去除 BOM。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")


def iter_block_segments(text: str):
    """
    将 Markdown 文本切分为 (kind, segment) 段，kind ∈ {"prose", "code"}。
    用于在解析标题时隔离围栏代码块。
    """
    segments = []
    current = []
    current_kind = "prose"
    fence_pattern = None  # 当前围栏的起始正则（按字符数匹配收尾）
    in_code = False

    for line in text.split("\n"):
        if not in_code:
            m = FENCE_PATTERN.match(line)
            if m:
                # 开启围栏：先 flush prose，再切到 code
                if current:
                    segments.append((current_kind, "\n".join(current)))
                    current = []
                fence_char = m.group(0)[0]
                fence_len = len(m.group(0))
                fence_pattern = re.compile(rf"^{fence_char}{{{fence_len},}}\s*$")
                current_kind = "code"
                in_code = True
                current.append(line)
            else:
                current.append(line)
        else:
            current.append(line)
            if fence_pattern is not None and fence_pattern.match(line):
                # 围栏收尾
                segments.append((current_kind, "\n".join(current)))
                current = []
                current_kind = "prose"
                in_code = False
                fence_pattern = None

    if current:
        segments.append((current_kind, "\n".join(current)))

    return segments


def parse_headings(text: str) -> list[tuple[int, str, int]]:
    """解析文本中的所有标题，跳过围栏代码块。
    支持 Markdown # 标题 与 HTML <h1>–<h6> 标题。返回 [(level, title, position), ...]。

    注意：当前实现不识别 Markdown 引用块（\"> " 开头）内的标题，因为
    HEADING_PATTERN 的 ^ 锚点要求 # 在行首。若未来放宽此约束，需同步考虑
    引用块隔离，避免引用块内的 # 被误统计为标题。"""
    headings = []
    for kind, segment in iter_block_segments(text):
        if kind == "code":
            continue
        for match in HEADING_PATTERN.finditer(segment):
            level = len(match.group(1))
            title = match.group(2).strip()
            headings.append((level, title, match.start()))
        for match in HTML_HEADING_PATTERN.finditer(segment):
            level = int(match.group(1))
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if title:
                headings.append((level, title, match.start()))
    headings.sort(key=lambda h: h[2])
    return headings


def find_first_heading_line(text: str) -> tuple[int, str] | None:
    """返回第一段非围栏代码块中第一个 H1 标题 (level, title)；不存在则 None。
    支持 Markdown # 与 HTML <h1> 两种语法。

    注意：当前实现不识别 Markdown 引用块（\"> " 开头）内的标题，因为
    line.strip() 不会去 除 "> " 前缀。若未来修改 stripped 处理逻辑，需同步
    考虑引用块隔离，避免引用块内的 # 被误识别为标题。"""
    for kind, segment in iter_block_segments(text):
        if kind == "code":
            continue
        for line in segment.split("\n"):
            if not line.strip():
                continue
            # 严格匹配行首的标题标记（与 parse_headings 的 HEADING_PATTERN 行为一致）
            m = re.match(r"^(#{1,6})\s+(.+)$", line)
            if m:
                return len(m.group(1)), m.group(2).strip()
            m = re.match(r"^<h([1-6])>(.+?)</h[1-6]>$", line, re.IGNORECASE)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if title:
                    return int(m.group(1)), title
            return None
    return None


def normalize_heading_levels(body: str, doc_title: str) -> str:
    """
    规范化标题层级：
    1. 若正文以一级标题开头，保留其作为文档标题，不再额外插入文件名。
    2. 若正文不以一级标题开头，在正文开头插入 `# {doc_title}\n\n` 作为一级标题。
    3. 计算正文中的最小标题层级（含新增的一级标题），跳过围栏代码块。
    4. 若最小标题 > 1，将所有标题统一提升，使最小标题变为二级（##）。
    """
    body = normalize_newlines(body)
    body = body.strip()

    first_heading = find_first_heading_line(body)

    # 若无 H1，插入文件名作为一级标题
    if first_heading is None or first_heading[0] != 1:
        body = f"# {doc_title}\n\n{body}"

    headings = parse_headings(body)
    if headings:
        min_level = min(h[0] for h in headings)
        if min_level > 1:
            shift = min_level - 2  # 使最小变为 2
            if shift > 0:

                def shift_heading(match):
                    hashes = match.group(1)
                    new_hashes = "#" * max(1, len(hashes) - shift)
                    return f"{new_hashes} {match.group(2)}"

                # 按段落处理，避免误改围栏代码块
                rebuilt = []
                for kind, segment in iter_block_segments(body):
                    if kind == "code":
                        rebuilt.append(segment)
                    else:
                        rebuilt.append(HEADING_PATTERN.sub(shift_heading, segment))
                body = "\n".join(rebuilt)

    return body


def deduplicate_adjacent_headings(parts: list[str]) -> list[str]:
    """
    消除相邻部分之间的重复标题。
    若部分 A 的最后一个标题与部分 B 的第一个标题文本和层级均相同，则删除 B 中的重复标题。
    标题匹配跳过围栏代码块。
    """
    if len(parts) <= 1:
        return parts

    result = [parts[0]]

    for i in range(1, len(parts)):
        prev_part = result[-1]
        curr_part = parts[i]

        prev_headings = parse_headings(prev_part)
        curr_headings = parse_headings(curr_part)

        if prev_headings and curr_headings:
            last_prev = prev_headings[-1]
            first_curr = curr_headings[0]

            if last_prev[1] == first_curr[1] and last_prev[0] == first_curr[0]:
                target_level, target_title = first_curr[0], first_curr[1]
                # 跳过围栏代码块按行删除首个匹配标题
                rebuilt = []
                in_code = False
                fence_pattern = None
                removed = False
                for line in curr_part.split("\n"):
                    if not in_code:
                        m = FENCE_PATTERN.match(line)
                        if m:
                            in_code = True
                            fence_char = m.group(0)[0]
                            fence_len = len(m.group(0))
                            fence_pattern = re.compile(rf"^{fence_char}{{{fence_len},}}\s*$")
                            rebuilt.append(line)
                            continue
                    else:
                        if fence_pattern is not None and fence_pattern.match(line):
                            in_code = False
                            fence_pattern = None
                            rebuilt.append(line)
                            continue
                    if not in_code and not removed:
                        h_match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
                        if h_match and h_match.group(1) == "#" * target_level and h_match.group(2).strip() == target_title:
                            removed = True
                            continue
                    rebuilt.append(line)
                curr_part = "\n".join(rebuilt).strip()

        result.append(curr_part)

    return result


def clean_body(body: str) -> str:
    """清理正文格式：统一换行、去除多余空行、确保标题前后有空行；跳过围栏代码块。"""
    body = normalize_newlines(body)
    body = body.strip()

    # 段落级重建：围栏代码块整体保留，prose 段落内做格式整理
    rebuilt = []
    for kind, segment in iter_block_segments(body):
        if kind == "code":
            rebuilt.append(segment)
            continue
        seg = segment
        # 将连续 3 个以上空行压缩为 2 个
        seg = re.sub(r"\n{4,}", "\n\n\n", seg)
        # 确保标题前后有空行
        def fix_heading_spacing(match):
            hashes = match.group(1)
            title = match.group(2)
            return f"\n\n{hashes} {title}\n\n"

        seg = HEADING_PATTERN.sub(fix_heading_spacing, seg)
        # 再次清理多余空行
        seg = re.sub(r"\n{3,}", "\n\n", seg)
        rebuilt.append(seg)
    body = "\n".join(rebuilt)
    body = body.strip()

    return body


def _normalize_value(raw: str) -> str:
    """归一化 frontmatter 值：去除单/双引号包裹的空值、其他情况保持原样。"""
    s = raw.strip()
    if s in ('""', "''"):
        return ""
    return s


def _parse_yaml_simple(fm_text: str) -> dict:
    """
    简单 YAML 解析：仅支持本 skill 实际遇到的子集
      - 标量 key: value
      - 多行块标量 key: | / key: >（保留为字符串）
      - 内联列表 key: [a, b, c]
      - 列表（- 开头）
      - 一层嵌套（缩进的子键）
    无匹配字段时跳过。解析失败回退到原标量解析逻辑。
    """
    fm: dict = {}
    lines = fm_text.split("\n")

    def _scalar(v: str) -> object:
        v = v.rstrip()
        # 双引号包裹 → 反转义
        if len(v) >= 2 and v.startswith("\"") and v.endswith("\""):
            inner = v[1:-1]
            inner = (
                inner.replace("\\\\", "\\")
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
            )
            return _normalize_value(inner)
        return _normalize_value(v)

    def _parse_scalar(v: str):
        v = v.strip()
        # 内联列表
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            if not inner:
                return []
            return [_scalar(item) for item in inner.split(",")]
        return _scalar(v)

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1

        if not stripped or stripped.startswith("#"):
            continue

        # 顶层 key: value（key 不以 - 或 空格开头）
        if stripped.startswith("- "):
            # 顶层 list 形式暂不支持；跳过
            continue
        m = re.match(r"^([A-Za-z_][\w\-]*):\s*(.*)$", stripped)
        if not m:
            continue
        key = m.group(1)
        value_part = m.group(2).rstrip()

        # 块标量
        if value_part in ("|", ">", "|-", "|-", ">-", "|+"):
            block_lines = []
            block_indent = None
            while i < len(lines):
                bl = lines[i]
                if not bl.strip():
                    # 空行：保留为段落分隔（在块标量内），但若紧跟缩进为 0 的下一行则收尾
                    block_lines.append("")
                    i += 1
                    continue
                indent = len(bl) - len(bl.lstrip(" \t"))
                if block_indent is None:
                    if indent == 0:
                        break
                    block_indent = indent
                if indent < block_indent:
                    break
                block_lines.append(bl[block_indent:])
                i += 1
            # 去除尾部空行（YAML 块标量 fold 后不保留尾部空行）
            while block_lines and not block_lines[-1]:
                block_lines.pop()
            fm[key] = "\n".join(block_lines) if value_part.startswith(">") else "\n".join(block_lines)
            continue

        # 子结构（嵌套对象或列表）：冒号后为空、且下一行缩进
        if value_part == "":
            child: dict = {}
            child_list: list | None = None
            child_indent = None
            while i < len(lines):
                cl = lines[i]
                if not cl.strip():
                    i += 1
                    continue
                indent = len(cl) - len(cl.lstrip(" \t"))
                if child_indent is None:
                    if indent == 0:
                        break
                    child_indent = indent
                if indent < child_indent:
                    break
                cs = cl.strip()
                if cs.startswith("- "):
                    if child_list is None:
                        child_list = []
                    child_list.append(_parse_scalar(cs[2:]))
                    i += 1
                    continue
                cm = re.match(r"^([A-Za-z_][\w\-]*):\s*(.*)$", cs)
                if cm:
                    if child_list is not None:
                        # 列表后面跟对象键，结束当前子对象解析
                        break
                    child[cm.group(1)] = _parse_scalar(cm.group(2))
                    i += 1
                    continue
                break
            if child_list is not None:
                fm[key] = child_list
            elif child:
                fm[key] = child
            else:
                fm[key] = None
            continue

        # 标量 / 内联列表
        fm[key] = _parse_scalar(value_part)

    return fm


def extract_frontmatter(content: str) -> tuple[dict, str]:
    """提取 YAML frontmatter，返回 (frontmatter_dict, body)。无 frontmatter 时返回 ({}, content)。
    支持单行标量、多行块（| / >）、内联列表（[a, b]）、列表（- ）与一层嵌套对象。
    """
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end() :]

    try:
        fm = _parse_yaml_simple(fm_text)
    except Exception:
        fm = {}

    return fm, body


def _is_empty_value(value) -> bool:
    """判断 frontmatter 值是否视为“空”：None、空串、仅引号 `""` 或 `''`、仅空白。"""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return value.strip() in ("", '""', "''")


def merge_frontmatters(
    frontmatters: list[dict], strategy: str = "first-wins"
) -> dict | None:
    """
    合并多个文件的 frontmatter。
    strategy:
      - first-wins：首文件为准，后续文件同 key 不覆盖（默认值）
      - first-non-empty：首文件该 key 为空时允许后续非空值覆盖
    全部为空时返回 None。
    """
    if not any(frontmatters):
        return None

    merged: dict = {}
    for fm in frontmatters:
        for key, value in fm.items():
            if key not in merged:
                merged[key] = value
                continue
            if strategy == "first-non-empty" and _is_empty_value(merged.get(key)):
                if not _is_empty_value(value):
                    merged[key] = value
    return merged


def _yaml_quote(value: str) -> str:
    """对字符串做 YAML 双引号包裹与必要转义。"""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _yaml_render_value(value, indent: int = 0) -> str:
    """将非空 value 渲染为 YAML 文本,可被 _parse_yaml_simple 还原。
    - 字符串:双引号包裹 + 转义
    - list:多行块,每项 `  - ...`(缩进 indent*2)
    - dict:多行块,每个 k: v
    """
    pad = "  " * indent
    if isinstance(value, str):
        return _yaml_quote(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            if isinstance(item, (list, dict)):
                sub = _yaml_render_value(item, indent + 1)
                lines.append(f"{pad}-")
                for sub_line in sub.split("\n"):
                    lines.append(f"{pad}  {sub_line}")
            else:
                lines.append(f"{pad}- {_yaml_render_value(item, indent + 1)}")
        return "\n".join(lines)
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            if isinstance(v, (list, dict)):
                lines.append(f"{pad}{k}:")
                sub = _yaml_render_value(v, indent + 1)
                for sub_line in sub.split("\n"):
                    lines.append(f"{pad}  {sub_line}")
            else:
                lines.append(f"{pad}{k}: {_yaml_render_value(v, indent + 1)}")
        return "\n".join(lines)
    return _yaml_quote(str(value))


def format_frontmatter(fm: dict) -> str:
    """将 frontmatter dict 格式化为 YAML frontmatter 字符串。

    字符串值用双引号包裹并按 YAML 规则转义;list/dict 渲染为块格式,
    整体可被 _parse_yaml_simple 往返解析。
    """
    if not fm:
        return ""
    lines = ["---"]
    for key, value in fm.items():
        if value is None:
            lines.append(f"{key}: \"\"")
            continue
        if isinstance(value, (list, dict)):
            # list/dict:第一行只写 key:,后续行写块内容
            lines.append(f"{key}:")
            sub = _yaml_render_value(value, indent=0)
            for sub_line in sub.split("\n"):
                lines.append(f"  {sub_line}")
        else:
            lines.append(f"{key}: {_yaml_render_value(value)}")
    lines.append("---\n")
    return "\n".join(lines)


def extract_page_number(filename: str) -> int:
    """从文件名提取数字序号。如 image-3.md -> 3，page-01.md -> 1，ch2.md -> 2。失败返回 0。"""
    match = PAGE_NUMBER_PATTERN.search(filename)
    if match:
        return int(match.group(1))
    # 尝试匹配文件名中任意位置的连续数字
    match = re.search(r"(\d+)", filename)
    if match:
        return int(match.group(1))
    return 0


def resolve_align_source(
    align_source: str | None, align_mode: str
) -> tuple[str | None, dict[str, Path], str | None, str | None]:
    """
    解析对齐源路径。
    返回 (source_type, image_map, status, message)。
    source_type: "pdf" | "image_dir" | None
    image_map: {stem: Path}
    status: 对齐失败时的显式状态码（None 表示成功或未启用）
    message: 错误或说明信息
    """
    if not align_source:
        return None, {}, None, None

    raw_path = Path(align_source)
    if raw_path.is_symlink():
        return None, {}, STATUS_SYMLINK_NOT_SUPPORTED, f"对齐源符号链接不被支持: {align_source}"

    # 相对路径必须相对源 markdown 文件所在目录统一解析；此处先解析为绝对
    path = raw_path.resolve()
    if not path.exists():
        return None, {}, STATUS_ALIGN_SOURCE_NOT_FOUND, f"对齐源路径不存在: {align_source}"

    if path.is_file() and path.suffix.lower() == ".pdf":
        # PDF 作为对齐源时仅支持 marker 模式；image-ref / both 显式拒绝
        if align_mode in ("image-ref", "both"):
            return (
                None,
                {},
                STATUS_ALIGN_SOURCE_INVALID_TYPE,
                "PDF 作为对齐源时仅支持 --align-mode marker；如需图片引用请改用图片目录",
            )
        return "pdf", {}, None, None

    if path.is_dir():
        image_map = {}
        for f in path.iterdir():
            if f.suffix.lower() in IMAGE_SUFFIXES and f.is_file():
                image_map[f.stem] = f
        return "image_dir", image_map, None, None

    return None, {}, STATUS_ALIGN_SOURCE_INVALID_TYPE, f"对齐源必须是 PDF 文件或图片目录: {align_source}"


def build_alignment_marker(page: int, total: int, image_path: str | None, mode: str) -> str:
    """生成对齐标记字符串。使用 Markdown 引用块格式，不影响标题层级。"""
    lines = []
    if mode in ("marker", "both"):
        lines.append(f"> **第 {page} 页 / 共 {total} 页**")
    if mode in ("image-ref", "both") and image_path:
        if lines:
            lines.append(">")
        lines.append(f"> ![第 {page} 页]({image_path})")
    if lines:
        return "\n".join(lines) + "\n\n"
    return ""


def _normalize_each(bodies, filenames, prefix_markers=None):
    """对每个 body 归一化标题层级+清理;可选在每个 body 前置 marker。
    prefix_markers 与 bodies 等长,元素为前缀字符串（或 ""）。"""
    prefix_markers = prefix_markers or [""] * len(bodies)
    normalized = []
    for body, filename, marker in zip(bodies, filenames, prefix_markers):
        doc_title = Path(filename).stem
        body = normalize_heading_levels(body, doc_title)
        body = clean_body(body)
        normalized.append(marker + body)
    return normalized


def merge_bodies(bodies: list[str], filenames: list[str], separator: str) -> str:
    """合并多个文件正文，处理标题层级和去重。"""
    normalized = _normalize_each(bodies, filenames)
    deduplicated = deduplicate_adjacent_headings(normalized)
    merged = separator.join(deduplicated)
    merged = clean_body(merged)
    return merged


def merge_bodies_with_markers(
    bodies: list[str],
    filenames: list[str],
    separator: str,
    align_markers: list[str],
) -> str:
    """先做标题层级归一化，再按需在每个段前插入对齐标记，最后拼接。"""
    normalized = _normalize_each(bodies, filenames, prefix_markers=align_markers)
    deduplicated = deduplicate_adjacent_headings(normalized)
    merged = separator.join(deduplicated)
    merged = clean_body(merged)
    return merged


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """先写临时文件再 os.replace 原子替换，避免半截文件。
    跨设备（EXDEV）时回退到 shutil.move，不再保证原子性但保证成功。"""
    import shutil

    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        tmp_path.write_text(content, encoding=encoding)
        os.replace(tmp_path, path)
    except OSError as e:
        # 跨设备链接时回退到 shutil.move（不保证原子性，但保证最终一致性）
        if getattr(e, "errno", None) == errno.EXDEV:
            shutil.move(str(tmp_path), str(path))
            return
        # 清理可能残留的临时文件
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def process_files(
    file_paths: list[Path],
    output_path: Path,
    separator: str,
    no_frontmatter: bool,
    align_source: str | None,
    align_mode: str,
    frontmatter_strategy: str,
    continue_on_error: str = "off",
) -> Result:
    """处理文件合并的核心逻辑。

    continue_on_error:
      - "off":  任何读取失败立即返回 read_failed（默认）
      - "skip": 跳过失败文件继续合并；成功/失败清单写入 details
    """
    frontmatters = []
    bodies = []
    filenames = []
    succeeded_paths: list[Path] = []
    failed_paths: list[tuple[Path, str]] = []

    for fp in file_paths:
        try:
            content = fp.read_text(encoding="utf-8")
        except Exception as e:
            err = f"{e}"
            if continue_on_error == "skip":
                failed_paths.append((fp, err))
                continue
            return Result(
                status=STATUS_READ_FAILED,
                message=f"读取文件失败: {fp} - {err}",
                output_file=None,
                details={"file": str(fp), "succeeded": [str(p) for p in succeeded_paths]},
            )

        fm, body = extract_frontmatter(content)
        frontmatters.append(fm)
        bodies.append(body)
        filenames.append(fp.name)
        succeeded_paths.append(fp)

    if not succeeded_paths:
        return Result(
            status=STATUS_READ_FAILED,
            message="所有源文件均读取失败",
            output_file=None,
            details={"failed": [{"file": str(p), "error": e} for p, e in failed_paths]},
        )

    # skip 模式下用成功子集替换 file_paths（影响后续页码、对齐等）
    if continue_on_error == "skip" and failed_paths:
        file_paths = succeeded_paths

    merged_fm = None
    if not no_frontmatter:
        merged_fm = merge_frontmatters(frontmatters, strategy=frontmatter_strategy)

    # 处理对齐源
    source_type, image_map, align_status, align_message = resolve_align_source(align_source, align_mode)
    if align_status:
        return Result(
            status=align_status,
            message=align_message,
            output_file=None,
            details={},
        )

    # 提取页码;fallback 时显式记录供 details 审计
    page_numbers = []
    page_number_sources: list[str] = []  # "filename" 或 "position-fallback"
    for idx, fp in enumerate(file_paths):
        page = extract_page_number(fp.name)
        if page == 0:
            page = idx + 1
            page_number_sources.append("position-fallback")
        else:
            page_number_sources.append("filename")
        page_numbers.append(page)
    total_pages = max(page_numbers) if page_numbers else len(file_paths)

    # 为每个 body 添加对齐标记（仅在指定了 align_source 时）
    align_markers = []
    for i, (body, fp) in enumerate(zip(bodies, file_paths)):
        if align_source:
            page = page_numbers[i]
            image_path = None
            if source_type == "image_dir":
                img = image_map.get(fp.stem)
                if img:
                    image_path = str(img)
            align_markers.append(
                build_alignment_marker(page, total_pages, image_path, align_mode)
            )
        else:
            align_markers.append("")

    merged_body = merge_bodies_with_markers(bodies, filenames, separator, align_markers)

    final_content = ""
    if merged_fm:
        final_content += format_frontmatter(merged_fm)
    final_content += merged_body + "\n"

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return Result(
            status=STATUS_OUTPUT_DIR_CREATE_FAILED,
            message=f"无法创建输出目录: {e}",
            output_file=None,
            details={"output": str(output_path)},
        )

    try:
        _atomic_write_text(output_path, final_content, encoding="utf-8")
    except Exception as e:
        return Result(
            status=STATUS_SAVE_FAILED,
            message=f"保存输出文件失败: {e}",
            output_file=None,
            details={"output": str(output_path)},
        )

    heading_stats = {}
    for level, _, _ in parse_headings(merged_body):
        heading_stats[f"h{level}"] = heading_stats.get(f"h{level}", 0) + 1

    details: dict = {
        "source_files": [str(f) for f in file_paths],
        "output_file": str(output_path),
        "frontmatter_keys": list(merged_fm.keys()) if merged_fm else [],
        "heading_stats": heading_stats,
        "total_chars": len(final_content),
        "align_source": align_source,
        "align_mode": align_mode if align_source else None,
        "frontmatter_strategy": frontmatter_strategy,
    }
    if align_source:
        details["page_numbers"] = page_numbers
        details["page_number_sources"] = page_number_sources
        fallback_pages = [page_numbers[i] for i, s in enumerate(page_number_sources) if s == "position-fallback"]
        if fallback_pages:
            details["page_number_fallback"] = fallback_pages
    # skip 模式下把成功/失败清单同时写进 details,供调用方审计
    if continue_on_error == "skip" and (succeeded_paths or failed_paths):
        details["succeeded"] = [str(p) for p in succeeded_paths]
        details["failed"] = [{"file": str(p), "error": e} for p, e in failed_paths]
    # skip 模式下若存在失败文件,状态码置为 partial(退出码 2)
    if continue_on_error == "skip" and failed_paths:
        return Result(
            status=STATUS_PARTIAL,
            message=f"部分合并完成: {len(succeeded_paths)} 成功, {len(failed_paths)} 失败 → {output_path}",
            output_file=str(output_path),
            details=details,
        )
    return Result(
        status=STATUS_SUCCESS,
        message=f"成功合并 {len(file_paths)} 个文件到 {output_path}",
        output_file=str(output_path),
        details=details,
    )


def parse_manifest_json(path: Path) -> tuple[list[Path], str | None, str | None]:
    """解析 JSON 清单文件。相对路径相对 manifest 文件所在目录解析。
    返回 (paths, status, err)：status 非空时表示已确定状态码。
    """
    if path.is_symlink():
        return [], STATUS_SYMLINK_NOT_SUPPORTED, f"清单符号链接不被支持: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [], STATUS_MANIFEST_PARSE_FAILED, f"JSON 解析失败: {e}"
    except Exception as e:
        return [], STATUS_MANIFEST_PARSE_FAILED, f"读取清单文件失败: {e}"

    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return [], STATUS_MANIFEST_PARSE_FAILED, "清单文件缺少 'paths' 数组或类型不正确"

    manifest_dir = path.parent
    result = []
    for p_str in paths:
        if isinstance(p_str, str):
            p = Path(p_str)
            if not p.is_absolute():
                p = (manifest_dir / p).resolve()
            if p.exists() and p.suffix.lower() in SUPPORTED_SUFFIXES:
                result.append(p)

    return result, None, None


def parse_manifest_txt(path: Path) -> tuple[list[Path], str | None, str | None]:
    """解析 TXT 清单文件。相对路径相对 manifest 文件所在目录解析;CRLF 行尾自动 rstrip。
    返回 (paths, status, err)：status 非空时表示已确定状态码。
    """
    if path.is_symlink():
        return [], STATUS_SYMLINK_NOT_SUPPORTED, f"清单符号链接不被支持: {path}"
    try:
        manifest_dir = path.parent
        result = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n").strip()
                if not line or line.startswith("#"):
                    continue
                p = Path(line)
                if not p.is_absolute():
                    p = (manifest_dir / p).resolve()
                if p.exists() and p.suffix.lower() in SUPPORTED_SUFFIXES:
                    result.append(p)
        return result, None, None
    except Exception as e:
        return [], STATUS_MANIFEST_PARSE_FAILED, f"读取清单文件失败: {e}"


def resolve_input(args) -> tuple[list[Path], str | None, str | None, str | None]:
    """
    解析输入，返回 (文件列表, status, message, input_type)。
    优先级: --paths > --manifest > --path
    status: 失败时的显式状态码；成功时为 None
    message: 错误或说明信息
    input_type: "files" | "manifest" | "directory" | None
    """
    if args.paths:
        files = []
        for p_str in args.paths:
            p = Path(p_str)
            if not p.exists():
                return [], STATUS_MISSING_INPUT, f"文件不存在: {p_str}", None
            if p.is_symlink():
                return [], STATUS_SYMLINK_NOT_SUPPORTED, f"符号链接不被支持: {p_str}", None
            if p.suffix.lower() not in SUPPORTED_SUFFIXES:
                return [], STATUS_UNSUPPORTED_FORMAT, f"不支持的文件格式: {p_str}", None
            files.append(p)
        return files, None, None, "files"

    if args.manifest:
        p = Path(args.manifest)
        if not p.exists():
            return [], STATUS_MANIFEST_NOT_FOUND, f"清单文件不存在: {args.manifest}", None
        ext = p.suffix.lower()
        if ext == ".json":
            files, status, err = parse_manifest_json(p)
        elif ext == ".txt":
            files, status, err = parse_manifest_txt(p)
        else:
            return [], STATUS_UNSUPPORTED_FORMAT, f"不支持的清单格式: {ext}", None

        if status:
            # 解析层返回的 status 已显式携带语义，直接透传
            return [], status, err, None
        if err:
            return [], STATUS_MANIFEST_PARSE_FAILED, err, None
        if not files:
            return [], STATUS_MANIFEST_EMPTY, "清单文件中无有效的 .md 文件路径", None
        return files, None, None, "manifest"

    if args.path:
        p = Path(args.path)
        if not p.exists():
            return [], STATUS_MISSING_INPUT, f"目录不存在: {args.path}", None
        if not p.is_dir():
            return [], STATUS_MISSING_INPUT, f"路径不是目录: {args.path}", None

        files = sorted(
            [
                f
                for f in p.iterdir()
                if f.suffix.lower() in SUPPORTED_SUFFIXES and f.is_file()
            ]
        )
        if not files:
            return [], STATUS_EMPTY_INPUT_DIR, f"目录中没有 .md 文件: {args.path}", None
        return files, None, None, "directory"

    return [], STATUS_MISSING_INPUT, "必须指定 --paths、--path 或 --manifest 之一", None


def _sanitize_dirname_for_filename(name: str) -> str:
    """将目录名清洗为可作为文件名片段的字符串:去除路径分隔符与不可打印字符,合并连续空白。"""
    import string

    keep = set(string.ascii_letters + string.digits + "_-.()[]+")
    cleaned = "".join(c if c in keep else "_" for c in name)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "dir"


def determine_output_path(args, input_type: str, source_files: list[Path]) -> Path:
    """确定输出文件路径。"""
    if args.output:
        return Path(args.output)

    if args.output_dir:
        return Path(args.output_dir) / "merged.md"

    if input_type == "files":
        return source_files[0].parent / "merged.md"
    elif input_type == "directory":
        dir_path = Path(args.path)
        safe_name = _sanitize_dirname_for_filename(dir_path.name)
        return dir_path.parent / f"{safe_name}_merged.md"
    elif input_type == "manifest":
        manifest_path = Path(args.manifest)
        return manifest_path.parent / f"{manifest_path.stem}_merged.md"

    return Path("merged.md")


def run(args) -> dict:
    """核心运行函数，返回结构化结果字典。"""
    source_files, input_status, input_message, input_type = resolve_input(args)
    if input_status:
        # 状态码由 resolve_input 显式透传，run() 不再依赖 error 字符串
        return {
            "result_status": input_status,
            "summary": input_message,
            "targets": [],
        }

    if not source_files:
        return {
            "result_status": STATUS_EMPTY_INPUT_DIR,
            "summary": "未找到有效的源文件",
            "targets": [],
        }

    output_path = determine_output_path(args, input_type or "files", source_files)

    result = process_files(
        source_files,
        output_path,
        args.separator,
        args.no_frontmatter,
        args.align_source,
        args.align_mode,
        args.frontmatter_strategy,
        getattr(args, "continue_on_error", "off"),
    )

    targets = [
        {
            "source": str(f),
            "status": result.status,
        }
        for f in source_files
    ]

    return {
        "result_status": result.status,
        "summary": result.message,
        "output_file": result.output_file,
        "targets": targets,
        "details": result.details,
    }


def _read_skill_version() -> str:
    """从 VERSION.yaml 简单正则读取 skill_info.version,纯标准库实现。"""
    try:
        here = Path(__file__).resolve().parent.parent
        text = (here / "VERSION.yaml").read_text(encoding="utf-8")
    except Exception:
        return "unknown"
    m = re.search(r"^\s*version:\s*([\w.+-]+)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    return "unknown"


def main():
    parser = argparse.ArgumentParser(
        prog="zm-mds-merge",
        description="将多个 Markdown 文件按标题结构智能合并为一个统一文件。支持 PDF/图片页面对齐。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""\
示例:
  %(prog)s --paths ch1.md ch2.md ch3.md
  %(prog)s --path docs/ --output result.md
  %(prog)s --manifest files.txt --separator "\\n\\n***\\n\\n"
  %(prog)s --path ocr_results/ --align-source pdf_images/ --align-mode both

退出码:
  0  合并成功
  1  合并失败（详见 result_status）
""",
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--paths", nargs="+", help="一个或多个 .md 文件路径")
    input_group.add_argument("--path", help="包含 .md 文件的目录路径")
    input_group.add_argument(
        "--manifest",
        help="清单文件路径（.json 含 'paths' 数组，或 .txt 每行一个路径）",
    )

    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--output", help="输出文件完整路径")
    output_group.add_argument("--output-dir", help="输出目录（与 --output 互斥）")

    parser.add_argument(
        "--separator",
        default="\n\n---\n\n",
        help="文件间分隔符（默认: \\n\\n---\\n\\n）",
    )
    parser.add_argument(
        "--no-frontmatter",
        action="store_true",
        help="禁止在输出中生成合并后的 frontmatter",
    )
    parser.add_argument(
        "--frontmatter-strategy",
        choices=["first-wins", "first-non-empty"],
        default="first-wins",
        help=(
            "frontmatter 冲突解决策略：\n"
            "  first-wins      首文件为准（默认）\n"
            "  first-non-empty 首文件该 key 为空时由后续非空值覆盖"
        ),
    )

    parser.add_argument(
        "--align-source",
        help="对齐源路径（PDF 文件或图片目录），用于在合并结果中插入页码标记和/或原始图片引用",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"zm-mds-merge {_read_skill_version()}",
    )

    parser.add_argument(
        "--align-mode",
        choices=["marker", "image-ref", "both"],
        default="both",
        help=(
            "对齐模式：\n"
            "  marker     仅页码标记\n"
            "  image-ref  仅图片引用（仅在 --align-source 为图片目录时生效）\n"
            "  both       两者（PDF 对齐源时不支持，会返回错误）"
        ),
    )

    parser.add_argument(
        "--continue-on-error",
        choices=["off", "skip"],
        default="off",
        help=(
            "源文件读取失败时的行为:\n"
            "  off   立即返回 read_failed（默认）\n"
            "  skip  跳过失败文件继续合并；成功/失败清单写入 details"
        ),
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细处理信息")

    args = parser.parse_args()

    result = run(args)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 50}")
        print("zm-mds-merge 合并结果")
        print(f"{'=' * 50}")
        print(f"状态: {result['result_status']}")
        print(f"汇总: {result['summary']}")
        if result.get("output_file"):
            print(f"输出: {result['output_file']}")
        if args.verbose and result.get("details"):
            print("\n详情:")
            for key, value in result["details"].items():
                print(f"  {key}: {value}")
        if result.get("targets") and len(result["targets"]) > 1:
            success = sum(
                1 for t in result["targets"] if t["status"] == "success"
            )
            print(f"\n总计: {len(result['targets'])} 文件, 成功 {success} 个")

    if result["result_status"] in FAILURE_STATUSES:
        sys.exit(1)
    elif result["result_status"] == STATUS_PARTIAL:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
