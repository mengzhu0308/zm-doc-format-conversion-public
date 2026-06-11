#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


WORD_PATTERN = re.compile(
    r"[A-Za-z0-9_]+|"
    r"[\u3040-\u30ff"  # \u5e73\u5047\u540d + \u7247\u5047\u540d
    r"\u3400-\u4dbf"  # CJK \u6269\u5c55 A
    r"\u4e00-\u9fff"  # CJK \u7edf\u4e00\u6c49\u5b57
    r"\uf900-\ufaff"  # CJK \u517c\u5bb9\u6c49\u5b57\uff08\u5e38\u89c1\u533a\u6bb5\uff09
    r"\uac00-\ud7af]"  # \u97e9\u6587\u8c1a\u6587\u97f3\u8282
)
FENCE_PATTERN = re.compile(r"^(```+|~~~+)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s")
ORDERED_LIST_PATTERN = re.compile(r"^\d+\.\s")
UNORDERED_LIST_PATTERN = re.compile(r"^[-*+]\s")


@dataclass(frozen=True)
class Block:
    text: str
    words: int


def count_words(text: str) -> int:
    return len(WORD_PATTERN.findall(text))


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def extract_frontmatter(content: str) -> tuple[str, str]:
    lines = content.split("\n")
    if not lines or lines[0] != "---":
        return "", content
    for index in range(1, len(lines)):
        if lines[index] in {"---", "..."}:
            frontmatter = "\n".join(lines[: index + 1]).strip()
            body = "\n".join(lines[index + 1 :]).lstrip("\n")
            return frontmatter, body
    return "", content


def flush_paragraph(paragraph: list[str], blocks: list[Block]) -> None:
    if not paragraph:
        return
    text = "\n".join(paragraph).strip()
    if text:
        blocks.append(Block(text=text, words=count_words(text)))
    paragraph.clear()


def parse_blocks(content: str) -> list[Block]:
    lines = content.split("\n")
    blocks: list[Block] = []
    paragraph: list[str] = []
    fence_lines: list[str] = []
    fence_delimiter = ""

    for raw_line in lines:
        line = raw_line.rstrip()
        if fence_delimiter:
            fence_lines.append(line)
            if line.startswith(fence_delimiter):
                text = "\n".join(fence_lines).strip()
                blocks.append(Block(text=text, words=count_words(text)))
                fence_lines = []
                fence_delimiter = ""
            continue

        fence_match = FENCE_PATTERN.match(line.strip())
        if fence_match:
            flush_paragraph(paragraph, blocks)
            fence_delimiter = fence_match.group(1)
            fence_lines = [line]
            continue

        if not line.strip():
            flush_paragraph(paragraph, blocks)
            continue

        if HEADING_PATTERN.match(line):
            flush_paragraph(paragraph, blocks)
            blocks.append(Block(text=line.strip(), words=count_words(line)))
            continue

        if ORDERED_LIST_PATTERN.match(line.strip()) or UNORDERED_LIST_PATTERN.match(line.strip()):
            paragraph.append(line)
            continue

        paragraph.append(line)

    flush_paragraph(paragraph, blocks)
    if fence_lines:
        text = "\n".join(fence_lines).strip()
        blocks.append(Block(text=text, words=count_words(text)))
    return blocks


def split_oversized_block(block: Block, max_words: int) -> list[Block]:
    if block.words <= max_words:
        return [block]

    lines = [line for line in block.text.split("\n") if line]
    pieces: list[Block] = []
    current_lines: list[str] = []
    current_words = 0

    for line in lines:
        line_words = count_words(line)
        if line_words > max_words:
            if current_lines:
                text = "\n".join(current_lines).strip()
                pieces.append(Block(text=text, words=current_words))
                current_lines = []
                current_words = 0
            pieces.extend(split_text_by_words(line, max_words))
            continue

        if current_words + line_words > max_words and current_lines:
            text = "\n".join(current_lines).strip()
            pieces.append(Block(text=text, words=current_words))
            current_lines = [line]
            current_words = line_words
            continue

        current_lines.append(line)
        current_words += line_words

    if current_lines:
        text = "\n".join(current_lines).strip()
        pieces.append(Block(text=text, words=current_words))
    return pieces


def split_text_by_words(text: str, max_words: int) -> list[Block]:
    matches = list(WORD_PATTERN.finditer(text))
    if not matches:
        return [Block(text=text, words=count_words(text))]

    pieces: list[Block] = []
    chunk_start = 0
    current_words = 0

    for match in matches:
        current_words += 1
        if current_words < max_words:
            continue
        piece = text[chunk_start : match.end()].strip()
        if piece:
            pieces.append(Block(text=piece, words=count_words(piece)))
        chunk_start = match.end()
        current_words = 0

    tail = text[chunk_start:].strip()
    if tail:
        pieces.append(Block(text=tail, words=count_words(tail)))
    return pieces


def build_chunks(blocks: list[Block], max_words: int) -> list[list[Block]]:
    chunks: list[list[Block]] = []
    current_chunk: list[Block] = []
    current_words = 0

    normalized_blocks: list[Block] = []
    for block in blocks:
        normalized_blocks.extend(split_oversized_block(block, max_words))

    for block in normalized_blocks:
        if current_words + block.words > max_words and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [block]
            current_words = block.words
            continue
        current_chunk.append(block)
        current_words += block.words

    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def write_chunks(
    *,
    source_path: Path,
    output_dir: Path,
    frontmatter: str,
    chunks: list[list[Block]],
) -> dict[str, object]:
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if frontmatter:
        (chunks_dir / "frontmatter.md").write_text(frontmatter + "\n", encoding="utf-8")

    words_per_chunk: list[int] = []
    for index, chunk in enumerate(chunks, start=1):
        filename = f"chunk-{index:02d}.md"
        text = "\n\n".join(block.text for block in chunk).strip() + "\n"
        (chunks_dir / filename).write_text(text, encoding="utf-8")
        words_per_chunk.append(sum(block.words for block in chunk))

    return {
        "source": str(source_path),
        "chunks_dir": str(chunks_dir),
        "chunks": len(chunks),
        "frontmatter": bool(frontmatter),
        "words_per_chunk": words_per_chunk,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a local Markdown or text file into chunk files for zm-batch-translate-from-files."
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Local .md or .txt file path to chunk.",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=5000,
        help="Maximum words per chunk. Default: 5000.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory that should receive the chunks/ folder. Defaults to the source file directory.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON result.",
    )
    return parser.parse_args()


# noqa: standalone-main  # 单元测试 / 独立调试保留入口；正式调用统一走 scripts/main.py
def main() -> None:
    args = parse_args()
    source_path = Path(args.path).expanduser()
    if not source_path.exists():
        raise SystemExit("Input file does not exist.")
    if source_path.is_symlink():
        raise SystemExit("Symlinked input files are not supported.")
    if source_path.suffix.lower() not in {".md", ".txt"}:
        raise SystemExit("Only .md and .txt files are supported.")
    if args.max_words <= 0:
        raise SystemExit("--max-words must be a positive integer.")

    raw_content = normalize_newlines(source_path.read_text(encoding="utf-8"))
    frontmatter, body = extract_frontmatter(raw_content)
    blocks = parse_blocks(body)
    chunks = build_chunks(blocks, args.max_words)
    output_dir = Path(args.output_dir).expanduser().resolve(strict=False) if args.output_dir else source_path.parent

    payload = write_chunks(
        source_path=source_path.resolve(),
        output_dir=output_dir,
        frontmatter=frontmatter,
        chunks=chunks,
    )
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()
