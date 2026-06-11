from __future__ import annotations

from pathlib import Path

from pack_release_collect import gather_release_entries


CONFIRM_YES = "是"
CONFIRM_NO = "否"


def _insert_tree_path(tree: dict[str, dict], path_text: str) -> None:
    current = tree
    for part in Path(path_text).parts:
        current = current.setdefault(part, {})


def build_manifest_tree_lines(manifest: list[str]) -> list[str]:
    tree: dict[str, dict] = {}
    for path_text in sorted(manifest):
        _insert_tree_path(tree, path_text)

    lines = ["."]

    def walk(node: dict[str, dict], prefix: str) -> None:
        items = sorted(node.items(), key=lambda item: (bool(item[1]), item[0]))
        for index, (name, child) in enumerate(items):
            is_last = index == len(items) - 1
            branch = "└── " if is_last else "├── "
            suffix = "/" if child else ""
            lines.append(f"{prefix}{branch}{name}{suffix}")
            if child:
                walk(child, prefix + ("    " if is_last else "│   "))

    walk(tree, "")
    return lines


def print_manifest_preview(
    *,
    title: str,
    manifest: list[str],
    metadata_lines: list[str] | None = None,
) -> None:
    print(f"\n{title}")
    if metadata_lines:
        for line in metadata_lines:
            print(line)
    print("- 目录结构：")
    for line in build_manifest_tree_lines(manifest):
        print(f"  {line}")


def build_release_manifest(
    project_root: Path,
    *,
    release_scope: str,
    selected_license: str | None,
    exclude_skills: tuple[str, ...] = (),
) -> list[str]:
    entries, _warnings = gather_release_entries(
        project_root,
        release_scope=release_scope,
        selected_license=selected_license,
        exclude_skills=exclude_skills,
    )
    return [archive_name.as_posix() for _source, archive_name in entries]


def print_release_manifest(
    *,
    release_scope: str,
    tag: str,
    manifest: list[str],
    selected_license: str | None,
) -> None:
    metadata_lines = [
        f"- 发布范围：{release_scope}",
        f"- 发布 tag：{tag}",
    ]
    if selected_license is not None:
        metadata_lines.append(f"- 发布 license：{selected_license}")
    metadata_lines.append(f"- 资源数量：{len(manifest)}")
    print_manifest_preview(
        title="发布资源预览：",
        manifest=manifest,
        metadata_lines=metadata_lines,
    )


def write_release_manifest(path: Path, manifest: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(manifest) + ("\n" if manifest else "")
    path.write_text(content, encoding="utf-8")


def _require_yes_no_confirmation(*, prompt: str, cancel_message: str) -> None:
    answer = input(prompt).strip()
    if answer == CONFIRM_YES:
        return
    if answer in {CONFIRM_NO, ""}:
        raise RuntimeError(cancel_message)
    raise RuntimeError(f"仅接受输入 `{CONFIRM_YES}` 或 `{CONFIRM_NO}`，流程已取消。")


def require_release_confirmation(*, release_scope: str) -> None:
    _require_yes_no_confirmation(
        prompt=f"\n确认以上目录结构与资源清单后，是否发布 {release_scope} 版本（是/否）：",
        cancel_message="未收到发布确认，流程已取消。",
    )


def require_public_seed_confirmation() -> None:
    _require_yes_no_confirmation(
        prompt="\n确认以上目录结构与生成计划后，是否生成 public 专用 sibling 目录（是/否）：",
        cancel_message="未收到 public sibling 目录生成确认，流程已取消。",
    )
