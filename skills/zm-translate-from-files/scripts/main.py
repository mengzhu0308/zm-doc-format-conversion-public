#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from chunk_markdown import (
    build_chunks,
    extract_frontmatter,
    normalize_newlines,
    parse_blocks,
    write_chunks,
)
from resolve_targets import (
    language_suffix,
    resolve_payload,
    validate_output_root,
)
from translation_preferences import Preferences, load_preferences


# resolve_targets.ERROR_STATUSES is the single source of truth for both resolve
# and chunk error codes; chunk-specific subsets are derived from it.


def _emit_json(args: argparse.Namespace, payload: dict[str, object]) -> None:
    """Print payload as JSON; honor --pretty for human-readable output."""
    indent = 2 if getattr(args, "pretty", False) else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


def _resolve_preferences(args: argparse.Namespace) -> tuple[str, Preferences | None, list[str]]:
    """Return (effective_target_language, preferences_or_none, warnings).

    EXTEND.md is always loaded (when --extend is given or via cwd → XDG → home
    lookup) so non-language keys (audience, style, default_output_dir, ...)
    remain available. CLI --to only overrides the language sub-tag.

    Priority for target_language: CLI --to > EXTEND.md target_language > "zh-CN".
    """
    cli_to = (getattr(args, "to", "") or "").strip()
    cwd_str = (getattr(args, "cwd", "") or "").strip()
    cwd = Path(cwd_str).expanduser() if cwd_str else Path.cwd()
    extend_path = (getattr(args, "extend", "") or "").strip()
    extend = Path(extend_path).expanduser() if extend_path else None

    preferences, _, warnings = load_preferences(cwd, extend_path=extend)
    if cli_to:
        return cli_to, preferences, warnings

    target = str(getattr(preferences, "target_language", "") or "zh-CN").strip() or "zh-CN"
    return target, preferences, warnings


def _validate_target_language(value: str) -> str | None:
    """Return an error message if value is invalid, else None."""
    cleaned = (value or "").strip()
    if not cleaned:
        return "Target language cannot be empty."
    if any(ch.isspace() for ch in cleaned):
        return "Target language must not contain whitespace; use '-' to separate sub-tags."
    if len(cleaned) > 64:
        return "Target language is too long (>64 chars)."
    # BCP 47 sub-tags are ASCII letters, digits, and '-'. Reject anything else
    # (slashes, dots, control characters, ...) so the resulting directory name
    # never drifts from the user-supplied language.
    if not re.match(r"^[A-Za-z0-9-]+$", cleaned):
        return "Target language must contain only ASCII letters, digits, and '-'."
    return None


def _read_source_safely(path: Path) -> tuple[str | None, dict[str, object] | None]:
    """Read a UTF-8 text file; return (content_or_None, error_payload_or_None)."""
    if not path.exists():
        return None, {
            "result_status": "error",
            "summary": {"total": 0, "missing_input": 1},
            "error": "Input file does not exist.",
        }
    if path.is_symlink():
        return None, {
            "result_status": "error",
            "summary": {"total": 0, "unsafe_symlink_input": 1},
            "error": "Symlinked input files are not supported.",
        }
    if path.suffix.lower() not in {".md", ".txt"}:
        return None, {
            "result_status": "error",
            "summary": {"total": 0, "unsupported_input": 1},
            "error": "Only .md and .txt files are supported.",
        }
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError as exc:
        return None, {
            "result_status": "error",
            "summary": {"total": 0, "unsupported_encoding": 1},
            "error": (
                "Input file is not valid UTF-8. "
                "Re-encode it (e.g. `iconv -f <src> -t utf-8`) before retrying. "
                f"(codec: {exc.encoding}, position: {exc.start})"
            ),
        }
    except PermissionError:
        return None, {
            "result_status": "error",
            "summary": {"total": 0, "permission_denied": 1},
            "error": "Input file is not readable due to filesystem permissions.",
        }
    except OSError as exc:
        return None, {
            "result_status": "error",
            "summary": {"total": 0, "unsupported_input": 1},
            "error": f"Could not read input file: {exc.strerror or exc}",
        }


def _resolve_output_dir(
    *, args: argparse.Namespace, source_path: Path,
    preferences: Preferences | None = None,
) -> tuple[Path | None, dict[str, object] | None]:
    """Return (output_dir_or_None, error_payload_or_None)."""
    output_dir_arg = (getattr(args, "output_dir", "") or "").strip()
    if output_dir_arg:
        # Validate the user-supplied (un-resolved) path so the ancestor-symlink
        # check still fires when the user pointed through a symlinked parent.
        # Resolving first would follow the symlink and defeat the check.
        unverified = Path(output_dir_arg).expanduser()
        error = validate_output_root(unverified)
        if error:
            key = (
                "unsafe_output_ancestor"
                if error.startswith("An ancestor of the output root")
                else "unsafe_output_path"
            )
            return None, {
                "result_status": "error",
                "summary": {"total": 0, key: 1},
                "error": error,
            }
        output_dir = unverified.resolve(strict=False)
    else:
        # Priority: EXTEND.md default_output_dir > <cwd>/<stem>-chunks/
        if preferences and getattr(preferences, "default_output_dir", None):
            output_dir = Path(str(preferences.default_output_dir)).expanduser().resolve(strict=False)
        else:
            output_dir = Path.cwd() / f"{source_path.stem}-chunks"
        sys.stderr.write(
            f"warning: --output-dir not provided, falling back to {output_dir}\n"
        )
    return output_dir, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Helper CLI for the zm-translate-from-files skill."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prefs_parser = subparsers.add_parser("prefs", help="Load EXTEND.md preferences.")
    prefs_parser.add_argument("--cwd", default="", help="Working directory used to resolve project-local EXTEND.md.")
    prefs_parser.add_argument(
        "--extend",
        default="",
        help="Path to EXTEND.md preference file. When provided, preferences are loaded from this path only.",
    )
    prefs_parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")

    resolve_parser = subparsers.add_parser("resolve", help="Resolve one local file translation target.")
    resolve_parser.add_argument("--path", required=True, help="Local .md or .txt file path to inspect.")
    resolve_parser.add_argument("--to", default="", help="Target language used to derive the output directory suffix. When empty, EXTEND.md (or default) provides the language.")
    resolve_parser.add_argument("--output-dir", default="", help="Optional root directory for generated translation folders.")
    resolve_parser.add_argument(
        "--extend",
        default="",
        help="Path to EXTEND.md preference file. When provided, preferences are loaded from this path only.",
    )
    resolve_parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")

    chunk_parser = subparsers.add_parser("chunk", help="Split a local Markdown or text file into chunks.")
    chunk_parser.add_argument("--path", required=True, help="Local .md or .txt file path to chunk.")
    chunk_parser.add_argument(
        "--max-words",
        type=int,
        default=-1,
        help="Maximum words per chunk. Defaults to chunk_max_words from EXTEND.md (default 5000).",
    )
    chunk_parser.add_argument(
        "--output-dir",
        default="",
        help="Directory that should receive the chunks/ folder. Defaults to <cwd>/<source-stem>-chunks/.",
    )
    chunk_parser.add_argument(
        "--extend",
        default="",
        help="Path to EXTEND.md preference file. When provided, preferences are loaded from this path only.",
    )
    chunk_parser.add_argument(
        "--cwd",
        default="",
        help="Working directory used to resolve the project-local EXTEND.md candidate.",
    )
    chunk_parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    return parser.parse_args()


def run_prefs(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    preferences, loaded_from, warnings = load_preferences(
        cwd,
        extend_path=Path(args.extend) if args.extend else None,
    )
    from translation_preferences import preferences_payload
    payload = preferences_payload(preferences, loaded_from, warnings)
    _emit_json(args, payload)
    return 0


def run_resolve(args: argparse.Namespace) -> int:
    input_path = Path(args.path).expanduser()

    target_language, preferences, warnings = _resolve_preferences(args)
    to_err = _validate_target_language(target_language)
    if to_err:
        result = {
            "input_path": str(input_path.resolve(strict=False)),
            "target_language": target_language,
            "language_suffix": language_suffix(target_language),
            "output_root": None,
            "result_status": "error",
            "mode": "invalid",
            "targets": [],
            "summary": {"total": 0, "invalid_target_language": 1},
            "error": to_err,
            "warnings": list(warnings),
        }
        _emit_json(args, result)
        return 1

    # Priority: CLI --output-dir > EXTEND.md default_output_dir > None (源文件同目录).
    # Pass the unresolved path so resolve_payload's validate_output_root can
    # still see the symlinked parent; resolving here would short-circuit the
    # ancestor-symlink check.
    if args.output_dir:
        raw_output_root = Path(args.output_dir).expanduser()
    elif preferences and getattr(preferences, "default_output_dir", None):
        raw_output_root = Path(str(preferences.default_output_dir)).expanduser()
    else:
        raw_output_root = None
    result, exit_code = resolve_payload(
        input_path=input_path,
        target_language=target_language,
        output_root=raw_output_root,
    )
    if warnings:
        result["warnings"] = list(warnings)

    _emit_json(args, result)
    return exit_code


def run_chunk(args: argparse.Namespace) -> int:
    target_language, preferences, warnings = _resolve_preferences(args)

    # CLI explicit value > EXTEND.md > default 5000
    if args.max_words < 0:
        max_words = int(getattr(preferences, "chunk_max_words", 5000) or 5000) if preferences else 5000
    else:
        max_words = args.max_words
    if max_words <= 0:
        result = {
            "result_status": "error",
            "summary": {"total": 0, "invalid_max_words": 1},
            "error": "--max-words must be a positive integer.",
            "warnings": list(warnings),
        }
        _emit_json(args, result)
        return 1

    source_path = Path(args.path).expanduser()
    content, error = _read_source_safely(source_path)
    if error is not None:
        if warnings:
            error["warnings"] = list(warnings)
        _emit_json(args, error)
        return 1

    output_dir, output_err = _resolve_output_dir(args=args, source_path=source_path, preferences=preferences)
    if output_err is not None or output_dir is None:
        payload_err = output_err or {}
        if warnings:
            payload_err["warnings"] = list(warnings)
        _emit_json(args, payload_err)
        return 1

    try:
        raw_content = normalize_newlines(content or "")
        frontmatter, body = extract_frontmatter(raw_content)
        blocks = parse_blocks(body)
        chunks = build_chunks(blocks, max_words)
        payload = write_chunks(
            source_path=source_path.resolve(),
            output_dir=output_dir,
            frontmatter=frontmatter,
            chunks=chunks,
        )
        payload["max_words"] = max_words
        payload["target_language"] = target_language
        if warnings:
            payload["warnings"] = list(warnings)
        _emit_json(args, payload)
        return 0
    except OSError as exc:
        result = {
            "result_status": "error",
            "summary": {"total": 0, "permission_denied": 1},
            "error": f"Could not write chunks to {output_dir}: {exc.strerror or exc}",
            "warnings": list(warnings),
        }
        _emit_json(args, result)
        return 1


def main() -> None:
    args = parse_args()
    if args.command == "prefs":
        raise SystemExit(run_prefs(args))
    if args.command == "resolve":
        raise SystemExit(run_resolve(args))
    if args.command == "chunk":
        raise SystemExit(run_chunk(args))
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
