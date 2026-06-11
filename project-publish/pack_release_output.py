from __future__ import annotations

import json
from pathlib import Path

from pack_release_support import log


def build_result(
    *,
    tag: str,
    package_name: str,
    package_path: Path,
    package_status: str,
    release_scope: str,
    selected_license: str | None,
    uploaded: bool,
    release_url: str,
    dry_run: bool,
    included_entries: list[Path],
    error: str | None,
) -> dict[str, object]:
    return {
        "tag": tag,
        "package_name": package_name,
        "package_path": str(package_path),
        "package_status": package_status,
        "release_scope": release_scope,
        "selected_license": selected_license,
        "uploaded": uploaded,
        "release_url": release_url,
        "asset_name": package_name,
        "dry_run": dry_run,
        "included_entries": [entry.as_posix() for entry in included_entries],
        "error": error,
    }


def write_result(result_path: Path, result: dict[str, object]) -> None:
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def maybe_log_candidates(
    project_root: Path,
    tag: str,
    entries: list[tuple[Path, Path]],
    *,
    verbose: bool,
    release_scope: str,
    selected_license: str | None,
) -> None:
    if not verbose:
        return
    log(f"项目根目录：{project_root}")
    log(f"发布 tag：{tag}")
    log(f"发布范围：{release_scope}")
    if selected_license is not None:
        log(f"发布 license：{selected_license}")
    log(f"候选条目数：{len(entries)}")
