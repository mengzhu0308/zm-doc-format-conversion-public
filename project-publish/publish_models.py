from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TMP_SUFFIXES = (".tmp", ".swp", ".swo")


@dataclass(frozen=True)
class PublishOptions:
    tag: str | None
    repo_visibility: str | None
    repo_path: str | None
    target_repo_path: str | None
    release_scope: str | None
    list_local_repos: bool
    selected_license: str | None
    exclude_skills: tuple[str, ...]
    dry_run: bool
    verbose: bool
    skip_upload: bool
    pack_only: bool


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


@dataclass
class StepOutcome:
    name: str
    status: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True)
class PublishSession:
    project_root: Path
    tag: str
    project_info: dict[str, str]
    session_dir: Path
    commands_dir: Path


@dataclass(frozen=True)
class GitHubRepoContext:
    viewer_login: str
    owner_login: str
    name_with_owner: str
    visibility: str
    remote_url: str


@dataclass(frozen=True)
class LocalPublishRepoCandidate:
    project_root: Path
    name_with_owner: str
    visibility: str
    remote_url: str
