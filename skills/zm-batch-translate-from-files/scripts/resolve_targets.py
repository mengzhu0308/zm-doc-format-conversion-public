#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".txt"}
SKIPPED_STATUSES = {
    "duplicate_input",
}


@dataclass(frozen=True)
class Target:
    source_path: str
    source_uri: str | None
    source_kind: str | None
    output_dir: str | None
    backup_output_dir: str | None
    status: str
    reason: str | None


def language_suffix(target_language: str) -> str:
    cleaned = (target_language or "").strip().lower()
    if not cleaned:
        return "translated"
    primary = cleaned.split("-", 1)[0]
    primary = "".join(ch for ch in primary if ch.isalpha())
    return primary or "translated"


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
    base = output_dir.parent / f"{output_dir.name}.backup-{timestamp}"
    candidate = base
    counter = 1
    while candidate.exists() and counter < 1000:
        candidate = output_dir.parent / f"{output_dir.name}.backup-{timestamp}-{counter}"
        counter += 1
    if candidate.exists():
        raise RuntimeError(
            f"Could not allocate a unique backup path for {output_dir} "
            f"after {counter} attempts; please clean up old backups."
        )
    return candidate


def perform_backup(output_dir: Path) -> tuple[Path | None, str | None]:
    """Move an existing output directory to a timestamped backup path.

    Returns a tuple of (backup_path, error_message). When the move succeeds,
    error_message is None. When the input does not exist, is unsafe, or the
    move fails, backup_path is None and error_message contains the reason.
    """
    if not output_dir.exists():
        return None, f"Output directory does not exist: {output_dir}"
    if output_dir.is_symlink():
        return None, "Output directory is a symlink. Remove it and use a real directory before retrying."
    if not output_dir.is_dir():
        return None, "Output path exists but is not a directory."
    try:
        backup = build_backup_output_dir(output_dir)
    except RuntimeError as exc:
        return None, f"Backup naming conflict: {exc}"
    try:
        shutil.move(str(output_dir), str(backup))
    except OSError as exc:
        return None, f"Backup move failed: {exc}"
    return backup, None


def validate_output_dir_unresolved(output_dir: Path | None) -> str | None:
    """Validate a user-provided output directory path BEFORE any resolve().

    Python's ``Path.resolve(strict=False)`` follows symlinks, which would let a
    symlinked path look like a real directory afterwards. This check runs on
    the raw path so symlinks are still detected. It also rejects paths whose
    parent chain contains a symlink, since mkdir/resolve would follow it.
    """
    return validate_path_unresolved(output_dir, kind="Output directory")


def validate_path_unresolved(path: Path | None, *, kind: str) -> str | None:
    """Generic unresolved-path symlink rejector for output / batches paths.

    Centralizes the "path itself is a symlink", "parent chain contains a symlink",
    and "path exists but is not a directory" checks. Used by every subcommand
    that touches a user-provided output or batches directory.
    """
    if path is None:
        return None
    if path.is_symlink():
        return f"{kind} is a symlink. Remove it and use a real directory path before retrying."
    for parent in path.parents:
        if parent == path:
            continue
        if parent.is_symlink():
            return (
                f"{kind} path traverses a symlink at {parent}. "
                f"Use a real directory path before retrying."
            )
    if path.exists() and not path.is_dir():
        return f"{kind} path exists but is not a directory."
    return None


def build_failure(source_path: Path, status: str, reason: str) -> Target:
    return Target(
        source_path=str(source_path),
        source_uri=None,
        source_kind=None,
        output_dir=None,
        backup_output_dir=None,
        status=status,
        reason=reason,
    )


def build_target(
    source_path: Path,
    *,
    output_root: Path | None,
    lang_suffix: str,
) -> Target:
    original = source_path.expanduser()
    if not original.is_absolute():
        return build_failure(
            original,
            "manifest_relative_path",
            "Manifest entries must be absolute paths.",
        )
    if original.is_symlink():
        return build_failure(
            original,
            "unsafe_symlink_input",
            "Symlinked source files are not supported. Use a real file path before retrying.",
        )
    if not original.exists():
        return build_failure(original, "missing_input", "Input path does not exist.")
    if not original.is_file():
        return build_failure(original, "unsupported_input", "Input path is not a supported regular file.")

    resolved = original.resolve()
    kind = source_kind(resolved)
    if kind is None:
        return build_failure(resolved, "unsupported_input", "Only .md and .txt files are supported.")

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


def iter_directory_paths(input_path: Path) -> list[Path]:
    candidates: list[Path] = []
    for candidate in sorted(input_path.iterdir()):
        if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if candidate.is_file() and not candidate.is_symlink():
            candidates.append(candidate)
    return candidates


def read_manifest_paths(manifest_path: Path) -> tuple[list[Path], str | None]:
    if manifest_path.suffix.lower() == ".json":
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [], f"JSON manifest parse failed: {exc}"
        if not isinstance(payload, dict) or not isinstance(payload.get("absolute_paths"), list):
            return [], "JSON manifest must contain a top-level absolute_paths array."
        paths: list[Path] = []
        for index, item in enumerate(payload["absolute_paths"]):
            if not isinstance(item, str):
                return [], (
                    f"JSON manifest absolute_paths[{index}] must be a string, "
                    f"got {type(item).__name__}: {item!r}"
                )
            paths.append(Path(item).expanduser())
        return paths, None

    if manifest_path.suffix.lower() == ".txt":
        paths = []
        for line_no, raw_line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            candidate = Path(line).expanduser()
            if not candidate.is_absolute():
                return [], f"Manifest line {line_no}: not an absolute path: {line}"
            paths.append(candidate)
        return paths, None

    suffix = manifest_path.suffix.lower() or "<no extension>"
    return [], f"Manifest must be a .json or .txt file; got {suffix}: {manifest_path}"


def mark_duplicates(paths: list[Path]) -> list[Target | Path]:
    items: list[Target | Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            items.append(
                build_failure(
                    path,
                    "duplicate_input",
                    "Duplicate input path skipped; the first occurrence keeps its original order.",
                )
            )
            continue
        seen.add(key)
        items.append(path)
    return items


def mark_output_dir_conflicts(targets: list[Target]) -> list[Target]:
    seen: dict[str, list[int]] = {}
    for index, target in enumerate(targets):
        if target.status != "ready" or not target.output_dir:
            continue
        seen.setdefault(target.output_dir, []).append(index)

    if not any(len(indexes) > 1 for indexes in seen.values()):
        return targets

    updated = list(targets)
    for output_dir, indexes in seen.items():
        if len(indexes) < 2:
            continue
        reason = (
            "Multiple inputs resolve to the same output directory. "
            f"Pick a different output root or rename one of the sources before retrying: {output_dir}"
        )
        for index in indexes:
            updated[index] = replace(
                updated[index],
                backup_output_dir=None,
                status="unsafe_output_path",
                reason=reason,
            )
    return updated


def summarize(targets: list[Target]) -> dict[str, int]:
    summary: dict[str, int] = {"total": len(targets), "ready": 0, "failed": 0, "skipped": 0}
    for target in targets:
        if target.status == "ready":
            summary["ready"] += 1
        elif target.status in SKIPPED_STATUSES:
            summary["skipped"] += 1
            summary[target.status] = summary.get(target.status, 0) + 1
        else:
            summary["failed"] += 1
            summary[target.status] = summary.get(target.status, 0) + 1
    return summary


def compute_result_status(summary: dict[str, int]) -> str:
    ready = summary.get("ready", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    if ready and (failed or skipped):
        return "partial"
    if ready:
        return "ok"
    return "error"


def build_payload(
    *,
    mode: str,
    input_label: str,
    paths: list[Path],
    target_language: str,
    output_root: Path | None,
) -> tuple[dict[str, object], int]:
    lang_suffix = language_suffix(target_language)
    items = mark_duplicates(paths)
    targets: list[Target] = []
    for item in items:
        if isinstance(item, Target):
            targets.append(item)
            continue
        targets.append(build_target(item, output_root=output_root, lang_suffix=lang_suffix))
    targets = mark_output_dir_conflicts(targets)

    summary = summarize(targets)
    result_status = compute_result_status(summary)
    payload: dict[str, object] = {
        "input_path": input_label,
        "target_language": target_language,
        "language_suffix": lang_suffix,
        "output_root": str(output_root) if output_root else None,
        "result_status": result_status,
        "mode": mode,
        "targets": [asdict(target) for target in targets],
        "summary": summary,
    }
    return payload, 0 if summary.get("ready", 0) else 1


def resolve_directory_payload(
    *,
    input_path: Path,
    target_language: str,
    output_root: Path | None,
    group_size: int = 10,
    batches_dir: Path | None = None,
) -> tuple[dict[str, object], int]:
    base_payload: dict[str, object] = {
        "input_path": str(input_path.resolve(strict=False)),
        "target_language": target_language,
        "language_suffix": language_suffix(target_language),
        "output_root": str(output_root) if output_root else None,
        "result_status": "error",
        "mode": "directory",
        "targets": [],
        "summary": {"total": 0, "ready": 0, "failed": 1, "skipped": 0},
    }

    output_root_error = validate_output_dir_unresolved(output_root)
    if output_root_error:
        base_payload["error"] = output_root_error
        base_payload["summary"]["unsafe_output_path"] = 1
        return base_payload, 1
    if input_path.is_symlink():
        base_payload["error"] = "Symlinked input directories are not supported."
        base_payload["summary"]["unsafe_symlink_input"] = 1
        return base_payload, 1
    if not input_path.exists():
        base_payload["error"] = "Input directory does not exist."
        base_payload["summary"]["missing_input"] = 1
        return base_payload, 1
    if input_path.is_file():
        base_payload["error"] = "Single-file input is not supported by zm-batch-translate-from-files. Use zm-translate-from-files for one file."
        base_payload["summary"]["file_input_not_supported"] = 1
        return base_payload, 1
    if not input_path.is_dir():
        base_payload["error"] = "Input path is not a directory."
        base_payload["summary"]["unsupported_input"] = 1
        return base_payload, 1

    input_dir = input_path.resolve()
    paths = iter_directory_paths(input_dir)
    if not paths:
        base_payload["error"] = "No .md or .txt files were found in the input directory."
        base_payload["summary"]["manifest_empty"] = 1
        return base_payload, 1
    payload, exit_code = build_payload(
        mode="directory",
        input_label=str(input_dir),
        paths=paths,
        target_language=target_language,
        output_root=output_root,
    )

    # 第一阶段：目录输入且文件数超过 group_size 时，生成分组 JSON + 续跑提示
    if group_size > 0:
        ready_targets = [t for t in payload.get("targets", []) if t.get("status") == "ready"]
        if len(ready_targets) > group_size:
            target_batches_dir = batches_dir if batches_dir is not None else input_dir / "batches"
            batches_dir_error = validate_path_unresolved(target_batches_dir, kind="batches_dir")
            if batches_dir_error:
                payload["result_status"] = "error"
                payload["error"] = (
                    f"{batches_dir_error} (To override, use --batches-dir to point to a real directory.)"
                )
                payload["summary"] = {
                    "total": len(ready_targets),
                    "ready": 0,
                    "failed": len(ready_targets),
                    "skipped": 0,
                    "unsafe_symlink_input": len(ready_targets),
                }
                return payload, 1
            target_batches_dir.mkdir(exist_ok=True, parents=True)
            for old in target_batches_dir.glob("batch_*.json"):
                old.unlink()
            batches_dir = target_batches_dir

            batches = [ready_targets[i:i + group_size] for i in range(0, len(ready_targets), group_size)]
            json_paths = []
            prompts = []
            lang_suffix = language_suffix(target_language)

            for idx, batch in enumerate(batches, 1):
                json_path = batches_dir / f"batch_{idx:03d}.json"
                batch_payload = {
                    "absolute_paths": [t["source_path"] for t in batch],
                    "_batch_meta": {
                        "skill": "zm-batch-translate-from-files",
                        "batch_index": idx,
                        "total_batches": len(batches),
                        "group_size": group_size,
                        "target_language": target_language,
                        "output_root": str(output_root) if output_root else None,
                        "generated_from": str(input_dir),
                        "generated_at": datetime.now().isoformat(),
                    },
                }
                json_path.write_text(json.dumps(batch_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                json_paths.append(json_path)
                # 构建可直接复制粘贴的自然语言续跑提示
                output_hint = f"\n输出根目录：{output_root}" if output_root else ""
                prompt = f"""【批次 {idx} / 共 {len(batches)} 批次】

请使用 zm-batch-translate-from-files skill 批量翻译以下文件：
输入：{json_path}
目标语言：{target_language}
输出规则：每个源文件各自写到 {{stem}}-{lang_suffix}/translation.md{output_hint}"""
                prompts.append(prompt)

            payload["result_status"] = "batch_prepared"
            payload["batch_info"] = {
                "batch_mode": True,
                "batches_dir": str(batches_dir),
                "total_batches": len(batches),
                "group_size": group_size,
                "batch_files": [str(p) for p in json_paths],
            }
            payload["resume_prompts"] = prompts
            return payload, 0

    return payload, exit_code


def resolve_manifest_payload(
    *,
    manifest_path: Path,
    target_language: str,
    output_root: Path | None,
) -> tuple[dict[str, object], int]:
    base_payload: dict[str, object] = {
        "input_path": str(manifest_path.resolve(strict=False)),
        "target_language": target_language,
        "language_suffix": language_suffix(target_language),
        "output_root": str(output_root) if output_root else None,
        "result_status": "error",
        "mode": "manifest",
        "targets": [],
        "summary": {"total": 0, "ready": 0, "failed": 1, "skipped": 0},
    }

    output_root_error = validate_output_dir_unresolved(output_root)
    if output_root_error:
        base_payload["error"] = output_root_error
        base_payload["summary"]["unsafe_output_path"] = 1
        return base_payload, 1
    if manifest_path.is_symlink():
        base_payload["error"] = "Symlinked manifest files are not supported."
        base_payload["summary"]["unsafe_symlink_input"] = 1
        return base_payload, 1
    if not manifest_path.exists():
        base_payload["error"] = "Manifest path does not exist."
        base_payload["summary"]["manifest_missing"] = 1
        return base_payload, 1
    if not manifest_path.is_file():
        base_payload["error"] = "Manifest path is not a file."
        base_payload["summary"]["unsupported_input"] = 1
        return base_payload, 1

    paths, error = read_manifest_paths(manifest_path)
    if error:
        base_payload["error"] = error
        base_payload["summary"]["manifest_parse_failed"] = 1
        return base_payload, 1
    if not paths:
        base_payload["error"] = "Manifest did not contain any input paths."
        base_payload["summary"]["manifest_empty"] = 1
        return base_payload, 1
    return build_payload(
        mode="manifest",
        input_label=str(manifest_path.resolve(strict=False)),
        paths=paths,
        target_language=target_language,
        output_root=output_root,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve batch translation targets for the zm-batch-translate-from-files skill."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--path", help="Local directory path to scan at the current layer.")
    source.add_argument("--manifest", help="JSON or TXT manifest file containing absolute source paths.")
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
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON result.")
    return parser.parse_args()


# noqa: standalone-main  # 单元测试 / 独立调试保留入口；正式调用统一走 scripts/main.py
def main() -> None:
    args = parse_args()
    output_root = Path(args.output_dir).expanduser().resolve(strict=False) if args.output_dir else None
    if args.path:
        result, exit_code = resolve_directory_payload(
            input_path=Path(args.path).expanduser(),
            target_language=args.to,
            output_root=output_root,
        )
    else:
        result, exit_code = resolve_manifest_payload(
            manifest_path=Path(args.manifest).expanduser(),
            target_language=args.to,
            output_root=output_root,
        )
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
