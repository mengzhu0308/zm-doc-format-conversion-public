#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chunk_markdown import (
    build_chunks,
    extract_frontmatter,
    normalize_newlines,
    parse_blocks,
    write_chunks,
)
from resolve_targets import (
    perform_backup,
    resolve_directory_payload,
    resolve_manifest_payload,
    validate_output_dir_unresolved,
)
from translation_preferences import load_preferences, preferences_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Helper CLI for the zm-batch-translate-from-files skill."
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

    resolve_parser = subparsers.add_parser("resolve", help="Resolve batch translation targets.")
    source = resolve_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--path", help="Local directory path to scan at the current layer.")
    source.add_argument("--manifest", help="JSON or TXT manifest containing absolute source paths.")
    resolve_parser.add_argument("--to", default="zh-CN", help="Target language used to derive the output directory suffix.")
    resolve_parser.add_argument("--output-dir", default="", help="Optional root directory for generated translation folders.")
    resolve_parser.add_argument(
        "--batches-dir",
        default="",
        help="Directory that should receive batch_*.json files when directory input exceeds --group-size. Defaults to <input-dir>/batches; can also be set via EXTEND.md batches_dir.",
    )
    resolve_parser.add_argument(
        "--extend",
        default="",
        help="Path to EXTEND.md preference file. When provided, preferences are loaded from this path only.",
    )
    resolve_parser.add_argument(
        "--group-size",
        type=int,
        default=10,
        help="Directory input: max files per batch JSON (default 10); set to 0 to disable grouping.",
    )
    resolve_parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")

    chunk_parser = subparsers.add_parser("chunk", help="Split a local Markdown or text file into chunks.")
    chunk_parser.add_argument("--path", required=True, help="Local .md or .txt file path to chunk.")
    chunk_parser.add_argument("--max-words", type=int, default=5000, help="Maximum words per chunk. Default: 5000.")
    chunk_parser.add_argument("--output-dir", default="", help="Directory that should receive the chunks/ folder.")
    chunk_parser.add_argument(
        "--extend",
        default="",
        help="Path to EXTEND.md preference file. When provided, preferences override --max-words and --output-dir defaults.",
    )
    chunk_parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")

    backup_parser = subparsers.add_parser(
        "backup",
        help="Move an existing output directory to a timestamped backup path before rewriting it.",
    )
    backup_parser.add_argument(
        "--output-dir",
        required=True,
        help="Existing output directory (e.g. <stem>-<lang>) to move to a backup path.",
    )
    backup_parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    return parser.parse_args()


def run_prefs(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).expanduser() if args.cwd else Path.cwd()
    extend_path = Path(args.extend).expanduser() if args.extend else None
    preferences, loaded_from, warnings = load_preferences(
        cwd,
        extend_path=extend_path,
    )
    payload = preferences_payload(preferences, loaded_from, warnings)
    payload["mode"] = "prefs"
    summary = {"total": 1, "ready": 1 if loaded_from else 0, "failed": 0 if loaded_from else 1, "skipped": 0}
    if warnings:
        summary["warnings"] = len(warnings)
    if not loaded_from and warnings:
        summary["missing_input"] = 1
    payload["summary"] = summary
    explicit_failure = bool(extend_path) and bool(warnings)
    if loaded_from:
        payload["result_status"] = "ok"
    elif explicit_failure:
        payload["result_status"] = "error"
    else:
        # Default preferences returned when no EXTEND.md is found; not a hard failure.
        payload["result_status"] = "ok"
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0 if payload["result_status"] == "ok" else 1


def run_resolve(args: argparse.Namespace) -> int:
    raw_output_root = Path(args.output_dir).expanduser() if args.output_dir else None
    unresolved_error = validate_output_dir_unresolved(raw_output_root)
    if unresolved_error:
        payload = {
            "input_path": None,
            "target_language": args.to,
            "language_suffix": None,
            "output_root": None,
            "result_status": "error",
            "mode": "directory" if args.path else "manifest",
            "targets": [],
            "summary": {"total": 0, "ready": 0, "failed": 1, "skipped": 0, "unsafe_output_path": 1},
            "error": unresolved_error,
        }
        indent = 2 if args.pretty else None
        print(json.dumps(payload, ensure_ascii=False, indent=indent))
        return 1
    output_root = raw_output_root.resolve(strict=False) if raw_output_root else None
    extend_path = Path(args.extend) if args.extend else None
    if extend_path:
        prefs, _, _ = load_preferences(Path.cwd(), extend_path=extend_path)
    else:
        prefs = None
    cli_batches_dir = Path(args.batches_dir).expanduser() if args.batches_dir else None
    batches_dir: Path | None = cli_batches_dir
    if batches_dir is None and prefs is not None and prefs.batches_dir:
        batches_dir = Path(prefs.batches_dir).expanduser()

    if args.path:
        result, exit_code = resolve_directory_payload(
            input_path=Path(args.path).expanduser(),
            target_language=args.to,
            output_root=output_root,
            group_size=getattr(args, "group_size", 10),
            batches_dir=batches_dir,
        )
    else:
        result, exit_code = resolve_manifest_payload(
            manifest_path=Path(args.manifest).expanduser(),
            target_language=args.to,
            output_root=output_root,
        )

    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    return exit_code


def _chunk_error(source_path: Path, status_key: str, error: str, pretty: bool, warnings: list[str] | None = None) -> int:
    payload = {
        "source": str(source_path),
        "chunks_dir": None,
        "chunks": 0,
        "frontmatter": False,
        "words_per_chunk": [],
        "result_status": "error",
        "summary": {"total": 0, "ready": 0, "failed": 1, "skipped": 0, status_key: 1},
        "error": error,
        "warnings": list(warnings) if warnings else [],
    }
    indent = 2 if pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 1


def run_chunk(args: argparse.Namespace) -> int:
    source_path = Path(args.path).expanduser()
    if not source_path.exists():
        return _chunk_error(source_path, "missing_input", "Input file does not exist.", args.pretty)
    if source_path.is_symlink():
        return _chunk_error(source_path, "unsafe_symlink_input", "Symlinked input files are not supported.", args.pretty)
    if source_path.suffix.lower() not in {".md", ".txt"}:
        return _chunk_error(source_path, "unsupported_input", "Only .md and .txt files are supported.", args.pretty)
    if args.max_words <= 0:
        return _chunk_error(source_path, "invalid_args", "--max-words must be a positive integer.", args.pretty)

    max_words = args.max_words
    warnings: list[str] = []
    if args.extend:
        preferences, _, warnings = load_preferences(
            Path.cwd(),
            extend_path=Path(args.extend),
        )
        max_words = preferences.chunk_max_words
    if max_words <= 0:
        return _chunk_error(source_path, "invalid_args", "--max-words must be a positive integer.", args.pretty, warnings)

    if args.output_dir:
        raw_output_dir = Path(args.output_dir).expanduser()
        unresolved_error = validate_output_dir_unresolved(raw_output_dir)
        if unresolved_error:
            payload = {
                "source": str(source_path),
                "chunks_dir": None,
                "chunks": 0,
                "frontmatter": False,
                "words_per_chunk": [],
                "result_status": "error",
                "summary": {"total": 0, "ready": 0, "failed": 1, "skipped": 0, "unsafe_output_path": 1},
                "error": unresolved_error,
            }
            indent = 2 if args.pretty else None
            print(json.dumps(payload, ensure_ascii=False, indent=indent))
            return 1
        output_dir = raw_output_dir.resolve(strict=False)
    else:
        output_dir = source_path.parent

    raw_content = normalize_newlines(source_path.read_text(encoding="utf-8"))
    frontmatter, body = extract_frontmatter(raw_content)
    blocks = parse_blocks(body)
    chunks = build_chunks(blocks, max_words)

    payload = write_chunks(
        source_path=source_path.resolve(),
        output_dir=output_dir,
        frontmatter=frontmatter,
        chunks=chunks,
    )
    payload["warnings"] = warnings
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


def run_backup(args: argparse.Namespace) -> int:
    raw_output_dir = Path(args.output_dir).expanduser()
    unresolved_error = validate_output_dir_unresolved(raw_output_dir)
    if unresolved_error:
        payload = {
            "output_dir": str(raw_output_dir),
            "backup_output_dir": None,
            "result_status": "error",
            "summary": {"total": 0, "ready": 0, "failed": 1, "skipped": 0, "unsafe_output_path": 1},
            "error": unresolved_error,
        }
        indent = 2 if args.pretty else None
        print(json.dumps(payload, ensure_ascii=False, indent=indent))
        return 1
    output_dir = raw_output_dir.resolve(strict=False)
    backup_path, error = perform_backup(output_dir)
    summary = {"total": 0, "ready": 0, "failed": 0 if backup_path else 1, "skipped": 0}
    if not backup_path and error:
        if "symlink" in error:
            summary["unsafe_output_path"] = 1
        elif "does not exist" in error:
            summary["missing_input"] = 1
    payload = {
        "output_dir": str(output_dir),
        "backup_output_dir": str(backup_path) if backup_path else None,
        "result_status": "ok" if backup_path else "error",
        "summary": summary,
        "error": error,
    }
    indent = 2 if args.pretty else None
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0 if backup_path else 1


def main() -> None:
    args = parse_args()
    if args.command == "prefs":
        raise SystemExit(run_prefs(args))
    if args.command == "resolve":
        raise SystemExit(run_resolve(args))
    if args.command == "chunk":
        raise SystemExit(run_chunk(args))
    if args.command == "backup":
        raise SystemExit(run_backup(args))
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
