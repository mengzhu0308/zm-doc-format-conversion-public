#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


SKILL_NAME = "zm-batch-translate-from-files"
DEFAULT_TARGET_LANGUAGE = "zh-CN"
DEFAULT_MODE = "normal"
DEFAULT_AUDIENCE = "general"
DEFAULT_STYLE = "natural"
DEFAULT_CHUNK_THRESHOLD = 4000
DEFAULT_CHUNK_MAX_WORDS = 5000
ALLOWED_MODES = {"quick", "normal", "refined"}
ALLOWED_AUDIENCES = {"general", "technical", "academic", "children", "business"}
ALLOWED_STYLES = {"natural", "formal", "casual", "literary"}
INT_KEYS = {"chunk_threshold", "chunk_max_words"}
SUPPORTED_KEYS = {
    "target_language",
    "default_mode",
    "audience",
    "style",
    "chunk_threshold",
    "chunk_max_words",
    "default_output_dir",
    "glossary_files",
    "batches_dir",
}


@dataclass(frozen=True)
class Preferences:
    target_language: str = DEFAULT_TARGET_LANGUAGE
    default_mode: str = DEFAULT_MODE
    audience: str = DEFAULT_AUDIENCE
    style: str = DEFAULT_STYLE
    chunk_threshold: int = DEFAULT_CHUNK_THRESHOLD
    chunk_max_words: int = DEFAULT_CHUNK_MAX_WORDS
    default_output_dir: str | None = None
    glossary_files: tuple[str, ...] = field(default_factory=tuple)
    batches_dir: str | None = None


def preference_candidates(cwd: Path) -> list[Path]:
    xdg_root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return [
        cwd / ".zm-skills" / SKILL_NAME / "EXTEND.md",
        xdg_root / "zm-skills" / SKILL_NAME / "EXTEND.md",
        Path.home() / ".zm-skills" / SKILL_NAME / "EXTEND.md",
    ]


def parse_scalar(raw_value: str) -> str | int:
    value = raw_value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1].strip()
    return value


def parse_glossary_files(
    raw_value: str,
    *,
    base_dir: Path,
    line_no: int = 0,
) -> tuple[tuple[str, ...], list[str]]:
    items = [item.strip() for item in raw_value.split(",") if item.strip()]
    seen: set[str] = set()
    resolved: list[str] = []
    warnings: list[str] = []
    line_prefix = f"Line {line_no}: " if line_no else ""
    for item in items:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve(strict=False)
        key = str(candidate)
        if key in seen:
            warnings.append(f"{line_prefix}Duplicate glossary entry skipped: {candidate}")
            continue
        if not candidate.is_file():
            warnings.append(f"{line_prefix}Glossary file not found: {candidate}")
            seen.add(key)
            continue
        seen.add(key)
        resolved.append(key)
    return tuple(resolved), warnings


FENCE_RE = re.compile(r"^(\`{3,}|~{3,})")


def parse_extend_file(path: Path) -> tuple[dict[str, object], list[str]]:
    parsed: dict[str, object] = {}
    warnings: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    fence_delimiter = ""

    for line_no, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        fence_match = FENCE_RE.match(stripped)
        if fence_match:
            delimiter = fence_match.group(1)
            if not fence_delimiter:
                fence_delimiter = delimiter
            elif delimiter[0] == fence_delimiter[0]:
                fence_delimiter = ""
            continue
        if fence_delimiter:
            continue
        line = stripped
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if ":" not in line:
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if key not in SUPPORTED_KEYS:
            warnings.append(f"Line {line_no}: ignored unsupported key: {key}")
            continue

        if key in INT_KEYS:
            try:
                parsed[key] = int(parse_scalar(value))
            except ValueError:
                warnings.append(f"Line {line_no}: ignored invalid integer for {key}: {value}")
            continue

        if key == "glossary_files":
            glossary, glossary_warnings = parse_glossary_files(value, base_dir=path.parent, line_no=line_no)
            parsed[key] = glossary
            warnings.extend(glossary_warnings)
            continue

        parsed[key] = parse_scalar(value)

    return parsed, warnings


def normalize_preferences(values: dict[str, object], *, warnings: list[str]) -> Preferences:
    mode = str(values.get("default_mode") or DEFAULT_MODE).strip().lower()
    if mode not in ALLOWED_MODES:
        warnings.append(f"Unsupported default_mode '{mode}', fallback to {DEFAULT_MODE}.")
        mode = DEFAULT_MODE

    chunk_threshold = values.get("chunk_threshold", DEFAULT_CHUNK_THRESHOLD)
    chunk_max_words = values.get("chunk_max_words", DEFAULT_CHUNK_MAX_WORDS)

    if not isinstance(chunk_threshold, int) or chunk_threshold <= 0:
        warnings.append("chunk_threshold must be a positive integer. Fallback applied.")
        chunk_threshold = DEFAULT_CHUNK_THRESHOLD
    if not isinstance(chunk_max_words, int) or chunk_max_words <= 0:
        warnings.append("chunk_max_words must be a positive integer. Fallback applied.")
        chunk_max_words = DEFAULT_CHUNK_MAX_WORDS

    target_language = str(values.get("target_language") or DEFAULT_TARGET_LANGUAGE).strip()
    audience = str(values.get("audience") or DEFAULT_AUDIENCE).strip().lower()
    style = str(values.get("style") or DEFAULT_STYLE).strip().lower()
    if audience and audience not in ALLOWED_AUDIENCES:
        warnings.append(f"Unsupported audience '{audience}', fallback to {DEFAULT_AUDIENCE}.")
        audience = DEFAULT_AUDIENCE
    if style and style not in ALLOWED_STYLES:
        warnings.append(f"Unsupported style '{style}', fallback to {DEFAULT_STYLE}.")
        style = DEFAULT_STYLE

    default_output_dir = values.get("default_output_dir")
    output_dir_text: str | None = None
    if default_output_dir:
        candidate = Path(str(default_output_dir)).expanduser()
        output_dir_text = str(candidate.resolve(strict=False))

    glossary_files = values.get("glossary_files") or ()
    if not isinstance(glossary_files, tuple):
        glossary_files = tuple()

    batches_dir = values.get("batches_dir")
    batches_dir_text: str | None = None
    if batches_dir:
        candidate = Path(str(batches_dir)).expanduser()
        batches_dir_text = str(candidate.resolve(strict=False))

    return Preferences(
        target_language=target_language or DEFAULT_TARGET_LANGUAGE,
        default_mode=mode,
        audience=audience or DEFAULT_AUDIENCE,
        style=style or DEFAULT_STYLE,
        chunk_threshold=chunk_threshold,
        chunk_max_words=chunk_max_words,
        default_output_dir=output_dir_text,
        glossary_files=glossary_files,
        batches_dir=batches_dir_text,
    )


def load_preferences(
    cwd: Path | None = None,
    *,
    extend_path: Path | None = None,
) -> tuple[Preferences, Path | None, list[str]]:
    if extend_path is not None and str(extend_path):
        extend_path = extend_path.resolve(strict=False)
        if extend_path.is_file():
            parsed, warnings = parse_extend_file(extend_path)
            return normalize_preferences(parsed, warnings=warnings), extend_path, warnings
        return Preferences(), None, [f"EXTEND.md not found: {extend_path}"]
    search_root = cwd or Path.cwd()
    searched = preference_candidates(search_root)
    for candidate in searched:
        if candidate.is_file():
            parsed, warnings = parse_extend_file(candidate)
            return normalize_preferences(parsed, warnings=warnings), candidate, warnings
    warnings = [f"No EXTEND.md found; searched: {', '.join(str(p) for p in searched)}"]
    return Preferences(), None, warnings


def preferences_payload(preferences: Preferences, loaded_from: Path | None, warnings: Iterable[str]) -> dict[str, object]:
    return {
        "skill": SKILL_NAME,
        "loaded_from": str(loaded_from) if loaded_from else None,
        "preferences": asdict(preferences),
        "warnings": list(warnings),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load EXTEND.md preferences for the zm-batch-translate-from-files skill."
    )
    parser.add_argument(
        "--cwd",
        default="",
        help="Working directory used to resolve the project-local EXTEND.md candidate.",
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
    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    preferences, loaded_from, warnings = load_preferences(cwd)
    payload = preferences_payload(preferences, loaded_from, warnings)
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()
