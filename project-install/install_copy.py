from __future__ import annotations

import shutil
from pathlib import Path

from install_models import INSTALL_DIRS, INSTALL_FILES
from scripts.shared_tool_targets import MANAGED_ENTRY_MARKER, is_safe_runtime_path


def _guard_runtime_path(path: Path, operation: str) -> None:
    if not is_safe_runtime_path(path):
        raise RuntimeError(
            f"拒绝{operation}：路径 {path} 不在 home 目录下的受管 skill 运行态目录中"
        )


def clean_old_skill(
    name: str,
    target_dir: Path,
    dry_run: bool,
    verbose: bool,
    link_tool_dirs: tuple[Path, ...] = (),
) -> None:
    _guard_runtime_path(target_dir, "清理旧版")
    target = target_dir / name
    if not target.exists() and not target.is_symlink():
        clean_tool_entries(name, link_tool_dirs=link_tool_dirs, dry_run=dry_run, verbose=True)
        return

    label = entry_label(target)
    print(f"  清理旧版（{label}）：{target}")
    clean_tool_entries(name, link_tool_dirs=link_tool_dirs, dry_run=dry_run, verbose=True)

    if dry_run:
        return

    remove_entry(target, verbose=verbose, description="旧版")


def install_skill(
    src: Path,
    target_dir: Path,
    dry_run: bool,
    verbose: bool,
    link_tool_dirs: tuple[Path, ...] = (),
) -> None:
    _guard_runtime_path(target_dir, "安装")
    dest = target_dir / src.name
    print(f"  安装至：{dest}")
    print_install_plan(src)
    print_link_plan(src.name, source_dir=target_dir, link_tool_dirs=link_tool_dirs)
    if dry_run:
        return

    dest.mkdir(parents=True, exist_ok=True)
    copy_install_files(src, dest, verbose=verbose)
    copy_install_dirs(src, dest, verbose=verbose)
    sync_tool_links(src.name, source_dir=target_dir, link_tool_dirs=link_tool_dirs, verbose=verbose)


def entry_label(target: Path) -> str:
    if target.is_symlink():
        return "符号链接"
    if target.is_dir():
        return "目录"
    return "文件"


def remove_entry(target: Path, *, verbose: bool, description: str) -> None:
    if target.is_symlink():
        remove_symlink(target, verbose=verbose, description=description)
        return

    if target.is_dir():
        remove_directory(target, verbose=verbose, description=description)
        return

    remove_file(target, verbose=verbose, description=description)


def remove_symlink(target: Path, *, verbose: bool, description: str) -> None:
    if verbose:
        print(f"  删除{description}符号链接：{target} -> {target.resolve()}")
    target.unlink()


def remove_directory(target: Path, *, verbose: bool, description: str) -> None:
    if verbose:
        print(f"  删除{description}目录：{target}")
    shutil.rmtree(target)


def remove_file(target: Path, *, verbose: bool, description: str) -> None:
    if verbose:
        print(f"  删除{description}文件：{target}")
    target.unlink()


def clean_tool_entries(
    name: str,
    *,
    link_tool_dirs: tuple[Path, ...],
    dry_run: bool,
    verbose: bool,
) -> None:
    for tool_dir in link_tool_dirs:
        entry = tool_dir / name
        if not entry.exists() and not entry.is_symlink():
            continue
        _guard_runtime_path(entry, "清理工具入口")
        if dry_run:
            label = entry_label(entry)
            print(f"  [预览] 将删除关联工具入口（{label}）：{entry}")
            continue
        if verbose:
            print(f"  删除关联工具入口：{entry}")
        remove_entry(entry, verbose=False, description="关联工具入口")


def print_link_plan(name: str, *, source_dir: Path, link_tool_dirs: tuple[Path, ...]) -> None:
    if not link_tool_dirs:
        return

    source = source_dir / name
    for tool_dir in link_tool_dirs:
        link = tool_dir / name
        print(f"    同步链接：{link} -> {source}")


def sync_tool_links(
    name: str,
    *,
    source_dir: Path,
    link_tool_dirs: tuple[Path, ...],
    verbose: bool,
) -> None:
    if not link_tool_dirs:
        return

    source = source_dir / name
    for tool_dir in link_tool_dirs:
        _guard_runtime_path(tool_dir, "创建符号链接")
        link = tool_dir / name

        if not tool_dir.exists():
            tool_dir.mkdir(parents=True, exist_ok=True)

        if link.is_symlink():
            if link.resolve() == source.resolve():
                if verbose:
                    print(f"    保持链接：{link} -> {source}")
                continue
            if verbose:
                print(f"    更新链接：{link} -> {source}")
            link.unlink()
        elif link.exists():
            if verbose:
                label = entry_label(link)
                print(f"    删除同名工具入口（{label}）：{link}")
            remove_entry(link, verbose=False, description="同名工具入口")
        elif verbose:
            print(f"    创建链接：{link} -> {source}")

        try:
            link.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            if verbose:
                print(f"    符号链接不可用，改为复制受管入口：{link} ({exc})")
            copy_managed_tool_entry(source, link)


def copy_managed_tool_entry(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        remove_entry(target, verbose=False, description="同名工具入口")

    shutil.copytree(source, target)
    marker = target / MANAGED_ENTRY_MARKER
    marker.write_text(f"source={source}\n", encoding="utf-8")


def print_install_plan(src: Path) -> None:
    for file_name in sorted(INSTALL_FILES):
        if (src / file_name).is_file():
            print(f"    复制文件：{file_name}")

    for dir_name in sorted(INSTALL_DIRS):
        source_dir = src / dir_name
        if not source_dir.is_dir():
            continue
        installable_files = collect_installable_files(source_dir)
        if installable_files:
            print(f"    复制目录：{dir_name}/ ({len(installable_files)} 个文件)")


def copy_install_files(src: Path, dest: Path, *, verbose: bool) -> None:
    for file_name in sorted(INSTALL_FILES):
        source = src / file_name
        if not source.is_file():
            continue
        shutil.copy2(source, dest / file_name)
        if verbose:
            print(f"    复制：{file_name}")


def copy_install_dirs(src: Path, dest: Path, *, verbose: bool) -> None:
    for dir_name in sorted(INSTALL_DIRS):
        source_dir = src / dir_name
        if not source_dir.is_dir():
            continue

        installable_files = collect_installable_files(source_dir)
        dest_dir = dest / dir_name
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        if not installable_files:
            continue

        for source_file in installable_files:
            target_file = dest_dir / source_file.relative_to(source_dir)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)

        if verbose:
            print(f"    复制：{dir_name}/ ({len(installable_files)} 个文件)")


def collect_installable_files(source_dir: Path) -> list[Path]:
    return sorted(
        path for path in source_dir.rglob("*") if path.is_file() and path.name != ".gitkeep"
    )
