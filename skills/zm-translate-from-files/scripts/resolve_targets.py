#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Target:
    source_path: str
    source_uri: str | None
    source_kind: str | None
    output_dir: str | None
    backup_output_dir: str | None
    status: str
    reason: str | None


SUPPORTED_SUFFIXES = {".md", ".txt"}
# Single source of truth for any non-OK status emitted by either resolve or chunk.
# main.py imports this instead of redeclaring a partial subset.
ERROR_STATUSES = {
    "missing_input",
    "directory_input_not_supported",
    "unsupported_input",
    "unsafe_symlink_input",
    "unsafe_output_path",
    "unsafe_output_ancestor",
    "invalid_target_language",
    "unsupported_encoding",
    "permission_denied",
    "invalid_max_words",
}


# Match BCP 47 primary language subtag: 2-3 alpha letters (no digits).
_BCP47_PRIMARY_RE = re.compile(r"^[A-Za-z]{2,3}$")
# Match a BCP 47 subtag: 2-8 alphanumerics, must not start or end with hyphen.
_BCP47_SUBTAG_RE = re.compile(r"^[A-Za-z0-9]{2,8}$")
# Match a BCP 47 private-use subtag (x-...) or singleton-followed-by value.
_BCP47_SINGLETON_RE = re.compile(r"^[A-Za-z0-9]$")


def normalize_target_language(value: str) -> str:
    """Normalize a target_language value into canonical BCP 47 form.

    - Lowercases, splits on '-', '_' or whitespace, trims each subtag.
    - Title-cases the primary subtag and any 4-letter script subtag (Hans, Hant).
    - Uppercases region subtags that are 2 letters or 3 digits.
    - Drops empty segments and unknown singletons to avoid leaking whitespace or
      stray characters into output directory names.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    raw_parts = re.split(r"[-_\s]+", cleaned)
    parts = [p for p in raw_parts if p]
    if not parts:
        return ""
    primary = parts[0]
    if not _BCP47_PRIMARY_RE.match(primary):
        return cleaned  # leave as-is; _validate_target_language should reject it
    normalized = [primary.lower()]

    i = 1
    while i < len(parts):
        sub = parts[i]
        if not sub:
            i += 1
            continue
        # Single-character singleton introduces the next subtag (extension etc.).
        if len(sub) == 1 and _BCP47_SINGLETON_RE.match(sub):
            if i + 1 < len(parts) and _BCP47_SUBTAG_RE.match(parts[i + 1]):
                normalized.append(sub.lower())
                normalized.append(parts[i + 1].lower())
                i += 2
                continue
            i += 1
            continue
        if len(sub) == 4 and sub.isalpha():
            normalized.append(sub.capitalize())  # script subtag (Hans, Hant, Latn)
        elif len(sub) == 2 and sub.isalpha():
            normalized.append(sub.upper())  # region subtag (CN, US, TW)
        elif len(sub) == 3 and sub.isdigit():
            normalized.append(sub)  # UN M.49 region subtag
        elif _BCP47_SUBTAG_RE.match(sub):
            normalized.append(sub.lower())
        else:
            # Unknown shape: keep lower-cased to avoid silent drops.
            normalized.append(sub.lower())
        i += 1
    return "-".join(normalized)


def language_suffix(target_language: str) -> str:
    """Return a filesystem-safe suffix derived from a BCP 47 target language.

    Preserves every BCP 47 sub-tag (joined by '_' instead of '-') so that
    ``zh-Hans-CN`` and ``zh-Hant-TW`` produce different output directories.
    Only alphanumerics and '_' are kept; the result is truncated to 32 chars.
    """
    normalized = normalize_target_language(target_language)
    if not normalized:
        return "translated"
    candidate = normalized.replace("-", "_")
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate:
        return "translated"
    return candidate[:32] or "translated"


def source_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix == ".txt":
        return "text"
    return None


def build_output_dir(*, source_path: Path, output_root: Path | None, lang_suffix: str) -> Path:
    name = f"{source_path.stem}-{lang_suffix}"
    if output_root is None:
        return source_path.parent / name
    return output_root / name


def build_backup_output_dir(output_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return output_dir.parent / f"{output_dir.name}.backup-{timestamp}"


def _ancestor_is_symlink(path: Path) -> bool:
    """Walk upward from ``path`` and return True if any existing ancestor is a symlink."""
    current = path
    for parent in (current, *current.parents):
        if parent == current or parent.exists():
            if parent.is_symlink():
                return True
        if parent == parent.parent:
            break
    return False


def validate_output_root(output_root: Path | None) -> str | None:
    if output_root is None:
        return None
    if output_root.exists() and output_root.is_symlink():
        return "Output root directory is a symlink. Use a real directory path before retrying."
    if output_root.exists() and not output_root.is_dir():
        return "Output root path exists but is not a directory."
    if _ancestor_is_symlink(output_root):
        return (
            "An ancestor of the output root is a symlink. "
            "Use a real directory path before retrying to avoid writing outside the intended location."
        )
    return None


def build_target(
    source_path: Path,
    *,
    output_root: Path | None,
    lang_suffix: str,
) -> Target:
    original = source_path.expanduser()
    if original.is_symlink():
        return Target(
            source_path=str(original),
            source_uri=None,
            source_kind=None,
            output_dir=None,
            backup_output_dir=None,
            status="unsafe_symlink_input",
            reason="Symlinked source files are not supported. Use a real file path before retrying.",
        )

    resolved = original.resolve()
    kind = source_kind(resolved)
    if kind is None:
        return Target(
            source_path=str(resolved),
            source_uri=None,
            source_kind=None,
            output_dir=None,
            backup_output_dir=None,
            status="unsupported_input",
            reason="Only .md and .txt files are supported.",
        )

    output_dir = build_output_dir(source_path=resolved, output_root=output_root, lang_suffix=lang_suffix)
    if output_dir.exists() and output_dir.is_symlink():
        return Target(
            source_path=str(resolved),
            source_uri=resolved.as_uri(),
            source_kind=kind,
            output_dir=str(output_dir),
            backup_output_dir=None,
            status="unsafe_output_path",
            reason="Target output directory is a symlink. Remove it and use a real directory before retrying.",
        )
    if output_dir.exists() and not output_dir.is_dir():
        return Target(
            source_path=str(resolved),
            source_uri=resolved.as_uri(),
            source_kind=kind,
            output_dir=str(output_dir),
            backup_output_dir=None,
            status="unsafe_output_path",
            reason="Target output path exists but is not a directory.",
        )

    backup_output_dir = build_backup_output_dir(output_dir) if output_dir.exists() else None
    return Target(
        source_path=str(resolved),
        source_uri=resolved.as_uri(),
        source_kind=kind,
        output_dir=str(output_dir),
        backup_output_dir=str(backup_output_dir) if backup_output_dir else None,
        status="ready",
        reason=None,
    )


def summarize(targets: list[Target]) -> dict[str, int]:
    summary: dict[str, int] = {"total": len(targets)}
    for target in targets:
        summary[target.status] = summary.get(target.status, 0) + 1
    return summary


def compute_result_status(summary: dict[str, int]) -> str:
    if any(summary.get(status, 0) > 0 for status in ERROR_STATUSES):
        return "error"
    return "ok"


def resolve_payload(
    *,
    input_path: Path,
    target_language: str,
    output_root: Path | None,
) -> tuple[dict[str, object], int]:
    normalized_language = normalize_target_language(target_language) or "translated"
    lang_suffix = language_suffix(target_language)
    result: dict[str, object] = {
        "input_path": str(input_path.resolve(strict=False)),
        "target_language": normalized_language,
        "language_suffix": lang_suffix,
        "output_root": str(output_root) if output_root else None,
        "result_status": "error",
        "mode": "missing",
        "targets": [],
        "summary": {"total": 0, "missing_input": 1},
    }

    output_root_error = validate_output_root(output_root)
    exit_code = 0
    if output_root_error:
        result["error"] = output_root_error
        key = (
            "unsafe_output_ancestor"
            if output_root_error.startswith("An ancestor of the output root")
            else "unsafe_output_path"
        )
        result["summary"] = {"total": 0, key: 1}
        exit_code = 1
    elif input_path.is_symlink():
        result["error"] = "Symlinked input files are not supported."
        result["summary"] = {"total": 0, "unsafe_symlink_input": 1}
        exit_code = 1
    elif not input_path.exists():
        result["error"] = "Input path does not exist."
        exit_code = 1
    elif input_path.is_file():
        targets = [build_target(input_path, output_root=output_root, lang_suffix=lang_suffix)]
        summary = summarize(targets)
        result["mode"] = "file"
        result["targets"] = [asdict(target) for target in targets]
        result["summary"] = summary
        result["result_status"] = compute_result_status(summary)
        if any(target.status in ERROR_STATUSES for target in targets):
            exit_code = 1
    elif input_path.is_dir():
        result["error"] = "Directory input is not supported by zm-translate-from-files. Use zm-batch-translate-from-files for directory or manifest batch translation."
        result["mode"] = "directory"
        result["summary"] = {"total": 0, "directory_input_not_supported": 1}
        result["result_status"] = "error"
        exit_code = 1
    else:
        result["error"] = "Input path is not a supported regular file."
        result["summary"] = {"total": 0, "unsupported_input": 1}
        exit_code = 1
    return result, exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve one local translation target for the zm-translate-from-files skill."
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Local .md or .txt file path to inspect.",
    )
    parser.add_argument(
        "--to",
        default="zh-CN",
        help="Target language used to derive the output directory suffix. Default: zh-CN.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional root directory for generated translation folders.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON result.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.path).expanduser()
    output_root = Path(args.output_dir).expanduser().resolve(strict=False) if args.output_dir else None
    result, exit_code = resolve_payload(
        input_path=input_path,
        target_language=args.to,
        output_root=output_root,
    )

    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()