#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


# Resolve the skill name from the directory layout (skills/<skill-name>/scripts/..)
# so that renaming the skill folder does not require touching this constant.
SKILL_NAME = Path(__file__).resolve().parent.parent.name
DEFAULT_TARGET_LANGUAGE = "zh-CN"
DEFAULT_MODE = "normal"
DEFAULT_AUDIENCE = "general"
DEFAULT_STYLE = "natural"
DEFAULT_CHUNK_THRESHOLD = 4000
DEFAULT_CHUNK_MAX_WORDS = 5000
ALLOWED_MODES = {"quick", "normal", "refined"}
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


def preference_candidates(cwd: Path) -> list[Path]:
    xdg_root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return [
        cwd / ".zm-skills" / SKILL_NAME / "EXTEND.md",
        xdg_root / "zm-skills" / SKILL_NAME / "EXTEND.md",
        Path.home() / ".zm-skills" / SKILL_NAME / "EXTEND.md",
    ]


def strip_outer_quotes(raw_value: str) -> str:
    """Strip the outermost matched quote pair from a scalar.

    Only handles a single outer pair of `"` or `'`; does not touch inline
    backticks. Inline-code stripping lives in ``parse_extend_file.strip_inline_code``.
    """
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


# Backwards-compatible alias for callers that imported the old name.
def parse_scalar(raw_value: str) -> str:  # pragma: no cover - thin wrapper
    return strip_outer_quotes(raw_value)


def parse_glossary_files(raw_value: str, *, base_dir: Path, warnings: list[str]) -> tuple[str, ...]:
    items = [item.strip() for item in raw_value.split(",") if item.strip()]
    resolved: list[str] = []
    for item in items:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve(strict=False)
        if not candidate.exists():
            warnings.append(f"glossary_files entry does not exist: {candidate}")
            continue
        if not candidate.is_file():
            warnings.append(f"glossary_files entry is not a regular file: {candidate}")
            continue
        resolved.append(str(candidate))
    return tuple(resolved)


def parse_extend_file(path: Path) -> tuple[dict[str, object], list[str]]:
    parsed: dict[str, object] = {}
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        warnings.append(
            f"EXTEND.md is not valid UTF-8 (codec: {exc.encoding}, "
            f"position: {exc.start}). Re-encode it (e.g. `iconv -f <src> -t utf-8`) "
            "before retrying; falling back to safe defaults."
        )
        return parsed, warnings
    fence_delimiter = ""
    inline_code_re = re.compile(r"`([^`\n]+)`")

    def strip_inline_code(value: str) -> str:
        return inline_code_re.sub(lambda m: m.group(1).strip(), value)

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            delimiter = line[:3]
            if not fence_delimiter:
                fence_delimiter = delimiter
            elif fence_delimiter == delimiter:
                fence_delimiter = ""
            continue
        if fence_delimiter:
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if ":" not in line:
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = strip_inline_code(raw_value.strip())
        if not key:
            continue
        if key not in SUPPORTED_KEYS:
            warnings.append(f"Ignored unsupported key: {key}")
            continue

        if key in INT_KEYS:
            try:
                parsed[key] = int(strip_outer_quotes(value))
            except ValueError:
                warnings.append(f"Ignored invalid integer for {key}: {value}")
            continue

        if key == "glossary_files":
            parsed[key] = parse_glossary_files(value, base_dir=path.parent, warnings=warnings)
            continue

        parsed[key] = strip_outer_quotes(value)

    return parsed, warnings


def normalize_preferences(values: dict[str, object], *, warnings: list[str]) -> Preferences:
    # Local import to avoid an import cycle when resolve_targets imports
    # translation_preferences indirectly.
    from resolve_targets import normalize_target_language

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

    raw_target_language = str(values.get("target_language") or DEFAULT_TARGET_LANGUAGE).strip()
    target_language = normalize_target_language(raw_target_language) or DEFAULT_TARGET_LANGUAGE
    audience = str(values.get("audience") or DEFAULT_AUDIENCE).strip()
    style = str(values.get("style") or DEFAULT_STYLE).strip()

    default_output_dir = values.get("default_output_dir")
    output_dir_text: str | None = None
    if default_output_dir:
        candidate = Path(str(default_output_dir)).expanduser()
        output_dir_text = str(candidate.resolve(strict=False))

    glossary_files = values.get("glossary_files") or ()
    if not isinstance(glossary_files, tuple):
        glossary_files = tuple()

    return Preferences(
        target_language=target_language or DEFAULT_TARGET_LANGUAGE,
        default_mode=mode,
        audience=audience or DEFAULT_AUDIENCE,
        style=style or DEFAULT_STYLE,
        chunk_threshold=chunk_threshold,
        chunk_max_words=chunk_max_words,
        default_output_dir=output_dir_text,
        glossary_files=glossary_files,
    )


def load_preferences(
    cwd: Path | None = None,
    *,
    extend_path: Path | None = None,
) -> tuple[Preferences, Path | None, list[str]]:
    if extend_path is not None:
        extend_path = extend_path.resolve(strict=False)
        if extend_path.is_file():
            parsed, warnings = parse_extend_file(extend_path)
            return normalize_preferences(parsed, warnings=warnings), extend_path, warnings
        return Preferences(), None, [f"EXTEND.md not found: {extend_path}"]
    search_root = cwd or Path.cwd()
    for candidate in preference_candidates(search_root):
        if candidate.is_file():
            parsed, warnings = parse_extend_file(candidate)
            return normalize_preferences(parsed, warnings=warnings), candidate, warnings
    return Preferences(), None, []


def preferences_payload(preferences: Preferences, loaded_from: Path | None, warnings: Iterable[str]) -> dict[str, object]:
    return {
        "skill": SKILL_NAME,
        "loaded_from": str(loaded_from) if loaded_from else None,
        "preferences": asdict(preferences),
        "warnings": list(warnings),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load EXTEND.md preferences for the zm-translate-from-files skill."
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


def main() -> None:
    args = parse_args()
    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    preferences, loaded_from, warnings = load_preferences(cwd)
    payload = preferences_payload(preferences, loaded_from, warnings)
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()