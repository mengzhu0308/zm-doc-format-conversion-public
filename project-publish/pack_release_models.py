from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

INSTALL_FILES = {"SKILL.md", "VERSION.yaml"}
INSTALL_DIRS = {"scripts", "references", "assets"}
TMP_SUFFIXES = (".tmp", ".swp", ".swo")


@dataclass(frozen=True)
class PackOptions:
    tag: str
    upload: bool
    dry_run: bool
    verbose: bool
    release_scope: str | None
    selected_license: str | None
    target_repo: str | None
    exclude_skills: tuple[str, ...]
    confirmed: bool


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ReleasePackage:
    package_name: str
    package_path: Path
    cache_dir: Path
    result_path: Path
