from __future__ import annotations

import shutil
from pathlib import Path

from scripts.shared_tool_targets import MANAGED_ENTRY_MARKER, is_safe_runtime_path


def _guard_runtime_path(path: Path, operation: str) -> None:
    if not is_safe_runtime_path(path):
        raise RuntimeError(
            f"拒绝{operation}：路径 {path} 不在 home 目录下的受管 skill 运行态目录中"
        )


def uninstall_skill(
    name: str,
    target_dir: Path,
    dry_run: bool,
    verbose: bool,
    link_tool_dirs: tuple[Path, ...] = (),
) -> None:
    _guard_runtime_path(target_dir, "卸载")
    target = target_dir / name
    target_exists = target.exists() or target.is_symlink()

    if target_exists:
        label = "符号链接" if target.is_symlink() else "目录"
        print(f"  删除已安装项（{label}）：{target}")
    else:
        print(f"  已跳过：{name} 未安装于 {target}")

    remove_tool_links(name, link_tool_dirs=link_tool_dirs, dry_run=dry_run, verbose=True)

    if dry_run:
        return

    # 先删除符号链接，再删除 SSOT 中的实际目录
    if target.is_symlink():
        remove_symlink(target)
    elif target.is_dir():
        remove_directory(target)
    elif verbose:
        print(f"  已跳过：{target} 未安装")


def remove_symlink(target: Path) -> None:
    print(f"  删除符号链接：{target} -> {target.resolve()}")
    target.unlink()


def remove_directory(target: Path) -> None:
    print(f"  删除已安装目录：{target}")
    shutil.rmtree(target)


def remove_tool_links(name: str, *, link_tool_dirs: tuple[Path, ...], dry_run: bool, verbose: bool) -> None:
    for tool_dir in link_tool_dirs:
        link = tool_dir / name
        if not link.is_symlink() and not is_managed_copy_entry(link):
            continue
        _guard_runtime_path(link, "删除工具入口")
        if dry_run:
            label = "符号链接" if link.is_symlink() else "受管副本"
            print(f"  [预览] 将删除关联{label}：{link}")
            continue
        if verbose:
            label = "符号链接" if link.is_symlink() else "受管副本"
            print(f"  删除关联{label}：{link}")
        remove_tool_entry(link)


def is_managed_copy_entry(path: Path) -> bool:
    return path.is_dir() and (path / MANAGED_ENTRY_MARKER).is_file()


def remove_tool_entry(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
