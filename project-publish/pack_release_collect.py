from __future__ import annotations

import fnmatch
from pathlib import Path

from pack_release_models import INSTALL_DIRS, INSTALL_FILES, TMP_SUFFIXES
from pack_release_support import (
    read_private_release_filters,
    read_public_release_skills,
)

def cleanup_temp_files(project_root: Path) -> list[str]:
    detected: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(TMP_SUFFIXES) or ".tmp." in name or name.endswith(".tmp"):
            detected.append(str(path.relative_to(project_root)))
    return detected


def should_exclude(path: Path) -> bool:
    if "__pycache__" in path.parts or path.suffix == ".pyc":
        return True
    name = path.name
    return name.endswith(TMP_SUFFIXES) or name.endswith(".tmp") or ".tmp." in name


def collect_skill_dirs(project_root: Path) -> list[Path]:
    return sorted(entry for entry in (project_root / "skills").iterdir() if entry.is_dir() and (entry / "SKILL.md").is_file())


def gather_release_entries(
    project_root: Path,
    *,
    release_scope: str = "private",
    selected_license: str | None = None,
    exclude_skills: tuple[str, ...] = (),
) -> tuple[list[tuple[Path, Path]], list[str]]:
    entries: list[tuple[Path, Path]] = []
    warnings: list[str] = []
    _ = release_scope
    _ = selected_license
    _ = exclude_skills
    collect_private_project_snapshot(project_root, entries)
    if not entries:
        raise FileNotFoundError("未收集到任何可发布内容")
    return entries, warnings


def collect_root_readme(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    readme = project_root / "README.md"
    if not readme.is_file():
        raise FileNotFoundError("缺少根级 README.md")
    entries.append((readme, Path("README.md")))


def collect_root_version(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    version_file = project_root / "VERSION.yaml"
    if not version_file.is_file():
        raise FileNotFoundError("缺少根级 VERSION.yaml")
    entries.append((version_file, Path("VERSION.yaml")))


def collect_project_install(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    collect_project_dir(project_root, "project-install", entries)


def collect_project_uninstall(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    collect_project_dir(project_root, "project-uninstall", entries)


def collect_project_dir(project_root: Path, dir_name: str, entries: list[tuple[Path, Path]]) -> None:
    project_dir = project_root / dir_name
    if not project_dir.is_dir():
        raise FileNotFoundError(f"缺少 {dir_name}/ 目录")
    for source in sorted(project_dir.rglob("*")):
        if source.is_file() and not should_exclude(source):
            entries.append((source, source.relative_to(project_root)))


def collect_skill_install_state(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    for skill_dir in collect_skill_dirs(project_root):
        collect_skill_files(project_root, skill_dir, entries)
        collect_skill_dirs_files(project_root, skill_dir, entries)


def collect_private_project_snapshot(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    exclude_paths, exclude_globs = read_private_release_filters(project_root)
    for source in sorted(project_root.rglob("*")):
        if not source.is_file():
            continue
        relative_path = source.relative_to(project_root)
        if should_exclude(source):
            continue
        if is_excluded_from_private_snapshot(relative_path, exclude_paths, exclude_globs):
            continue
        entries.append((source, relative_path))


def collect_public_project_files(project_root: Path, entries: list[tuple[Path, Path]]) -> None:
    collect_root_readme(project_root, entries)
    collect_root_version(project_root, entries)
    collect_project_install(project_root, entries)
    collect_project_uninstall(project_root, entries)


def collect_public_skill_install_state(
    project_root: Path,
    entries: list[tuple[Path, Path]],
    *,
    exclude_skills: tuple[str, ...] = (),
) -> None:
    available = {skill_dir.name: skill_dir for skill_dir in collect_skill_dirs(project_root)}
    for skill_name in read_public_release_skills(project_root, extra_exclude_skills=exclude_skills):
        skill_dir = available.get(skill_name)
        if skill_dir is None:
            raise FileNotFoundError(f"release.yaml 引用了不存在的 skill：{skill_name}")
        collect_skill_files(project_root, skill_dir, entries)
        collect_skill_dirs_files(project_root, skill_dir, entries)


def collect_skill_files(project_root: Path, skill_dir: Path, entries: list[tuple[Path, Path]]) -> None:
    for file_name in sorted(INSTALL_FILES):
        source = skill_dir / file_name
        if source.is_file():
            entries.append((source, source.relative_to(project_root)))


def collect_skill_dirs_files(project_root: Path, skill_dir: Path, entries: list[tuple[Path, Path]]) -> None:
    for dir_name in sorted(INSTALL_DIRS):
        source_dir = skill_dir / dir_name
        if not source_dir.is_dir():
            continue
        for source in sorted(source_dir.rglob("*")):
            if source.is_file() and not should_exclude(source):
                entries.append((source, source.relative_to(project_root)))


def gather_public_repo_seed_entries(
    project_root: Path,
    *,
    exclude_skills: tuple[str, ...] = (),
) -> list[tuple[Path, Path]]:
    entries: list[tuple[Path, Path]] = []
    collect_public_project_files(project_root, entries)
    collect_public_skill_install_state(project_root, entries, exclude_skills=exclude_skills)
    collect_project_dir(project_root, "project-publish", entries)
    if not entries:
        raise FileNotFoundError("未收集到任何 public 仓库种子内容")
    return entries


def is_excluded_from_private_snapshot(relative_path: Path, exclude_paths: list[str], exclude_globs: list[str]) -> bool:
    relative_text = relative_path.as_posix()
    for excluded in exclude_paths:
        normalized = excluded.strip().strip("/")
        if not normalized:
            continue
        if relative_text == normalized or relative_text.startswith(f"{normalized}/"):
            return True
    for pattern in exclude_globs:
        if fnmatch.fnmatch(relative_text, pattern) or fnmatch.fnmatch(relative_path.name, pattern):
            return True
    return False
