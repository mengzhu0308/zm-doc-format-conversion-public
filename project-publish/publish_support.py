from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pack_release_collect import gather_release_entries
from publish_models import CommandResult, GitHubRepoContext, LocalPublishRepoCandidate, TMP_SUFFIXES


SEARCH_SKIP_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
}

PUBLIC_PUBLISH_WARNING = "危险：公开发布会暴露整个仓库内容、tag、Release 与 Assets"
PUBLIC_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PUBLIC_LICENSE_CHOICES = ("MIT", "Apache-2.0", "GPL-3.0")


def validate_github_repo_name(repo_name: str, *, label: str) -> str:
    normalized = repo_name.strip()
    if not normalized:
        raise RuntimeError(f"{label}不能为空。")
    if not PUBLIC_REPO_NAME_RE.fullmatch(normalized):
        raise RuntimeError(f"{label}只允许字母、数字、点、下划线和中划线，且必须以字母或数字开头。")
    return normalized


def is_publish_project_root(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / ".git").exists()
        and (path / "skills").is_dir()
        and (path / "project-publish").is_dir()
        and (path / "VERSION.yaml").is_file()
    )


def find_project_root(preferred_path: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if preferred_path is not None:
        preferred = Path(preferred_path).expanduser().resolve()
        candidates.extend([preferred, *preferred.parents])
    candidates.extend(
        [
            Path(__file__).resolve().parent.parent,
            Path.cwd(),
            *Path.cwd().resolve().parents,
        ]
    )
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "skills").is_dir() and (candidate / "VERSION.yaml").is_file():
            return candidate
    print("错误：未找到项目根目录（需要同时存在 skills/ 与 VERSION.yaml）。", file=sys.stderr)
    raise SystemExit(2)


def find_git_repo_root(preferred_path: str | Path | None = None, *, default_path: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if preferred_path is not None:
        preferred = Path(preferred_path).expanduser().resolve()
        candidates.extend([preferred, *preferred.parents])
    if default_path is not None:
        default = Path(default_path).expanduser().resolve()
        candidates.extend([default, *default.parents])
    candidates.extend([Path.cwd(), *Path.cwd().resolve().parents])
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / ".git").exists():
            return candidate
    print("错误：未找到 Git 仓库根目录（需要存在 .git）。", file=sys.stderr)
    raise SystemExit(2)


def expected_target_repo_name(project_name: str, repo_visibility: str) -> str:
    return validate_github_repo_name(project_name, label="目标 GitHub 仓库名")


def infer_default_target_repo_root(source_project_root: Path, project_name: str, repo_visibility: str | None) -> Path:
    if repo_visibility is None:
        return source_project_root
    _ = expected_target_repo_name(project_name, repo_visibility)
    return source_project_root


def print_public_publish_warning(action: str) -> None:
    print(f"{PUBLIC_PUBLISH_WARNING}；当前操作：{action}；结果会进入公开仓库。", file=sys.stderr)


def ensure_public_publish_project_root(project_root: Path, *, require_git: bool) -> None:
    required_dirs = [
        project_root / "skills",
        project_root / "project-install",
        project_root / "project-publish",
    ]
    missing_dirs = [path.relative_to(project_root).as_posix() for path in required_dirs if not path.is_dir()]
    if missing_dirs:
        raise RuntimeError(
            "当前目录不是可执行公开发布的源码项目根目录，缺少："
            f"{', '.join(missing_dirs)}。"
        )
    if not (project_root / "VERSION.yaml").is_file():
        raise RuntimeError("当前目录缺少 VERSION.yaml，无法执行公开发布。")
    if require_git and not (project_root / ".git").exists():
        raise RuntimeError("当前目录不是 Git 工作树，无法执行正式公开发布。")


def validate_public_repo_name(repo_name: str, source_root: Path) -> Path:
    normalized = validate_github_repo_name(repo_name, label="public 仓库目录名")
    if normalized == source_root.name:
        raise RuntimeError("默认目标必须是合法的 sibling public 工作目录，目录名不能与当前 private 源码仓库目录名相同。")
    return source_root.parent / normalized


def copy_seed_entries(target_root: Path, entries: list[tuple[Path, Path]], *, verbose: bool) -> None:
    for source, archive_name in entries:
        destination = target_root / archive_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if verbose:
            print(f"已复制：{archive_name.as_posix()}")


def build_seed_manifest(entries: list[tuple[Path, Path]]) -> list[str]:
    return [archive_name.as_posix() for _source, archive_name in entries]


def ensure_target_worktree_accepts_entries(target_root: Path, entries: list[tuple[Path, Path]]) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    expected_files = {archive_name for _source, archive_name in entries}
    expected_dirs = {Path()}
    for archive_name in expected_files:
        current = Path()
        for part in archive_name.parts[:-1]:
            current /= part
            expected_dirs.add(current)

    file_conflicts: list[str] = []
    dir_conflicts: list[str] = []
    unexpected_files: list[str] = []
    for current in sorted(target_root.rglob("*")):
        relative_path = current.relative_to(target_root)
        if not relative_path.parts or relative_path.parts[0] == ".git":
            continue
        if relative_path in expected_files and current.is_dir():
            file_conflicts.append(relative_path.as_posix())
            continue
        if relative_path in expected_dirs and current.exists() and not current.is_dir():
            dir_conflicts.append(relative_path.as_posix())
            continue
        if current.is_file() and relative_path not in expected_files:
            unexpected_files.append(relative_path.as_posix())

    if file_conflicts or dir_conflicts or unexpected_files:
        problems: list[str] = []
        if file_conflicts:
            problems.append(f"目标目录中的同名目录阻塞文件写入：{', '.join(file_conflicts)}")
        if dir_conflicts:
            problems.append(f"目标目录中的同名文件阻塞目录写入：{', '.join(dir_conflicts)}")
        if unexpected_files:
            problems.append(f"目标目录存在白名单外文件：{', '.join(unexpected_files)}")
        raise RuntimeError("project-publish 已禁止自动删除目标目录内容；" + "；".join(problems))


def discover_local_search_roots() -> tuple[Path, ...]:
    roots: list[Path] = [Path.home()]
    cwd = Path.cwd().resolve()
    parts = cwd.parts
    if len(parts) >= 3 and parts[1] == "media":
        roots.append(Path("/") / parts[1] / parts[2])
    elif len(parts) >= 2 and parts[1] in {"mnt", "Volumes"}:
        roots.append(Path("/") / parts[1])
    unique: list[Path] = []
    for root in roots:
        if root.exists() and root not in unique:
            unique.append(root)
    return tuple(unique)


def discover_local_publish_roots(search_roots: tuple[Path, ...] | None = None) -> list[Path]:
    roots = search_roots or discover_local_search_roots()
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for current_root, dirnames, _filenames in os.walk(root):
            current = Path(current_root)
            dirnames[:] = [name for name in dirnames if name not in SEARCH_SKIP_DIRS]
            if is_publish_project_root(current):
                resolved = current.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(resolved)
                dirnames[:] = []
    return sorted(found)


def parse_simple_yaml_mapping(text: str) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    current_section: str | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not raw_line.startswith(" "):
            if not stripped.endswith(":"):
                raise ValueError(f"第 {lineno} 行不是合法的顶层映射")
            current_section = stripped[:-1].strip()
            if not current_section:
                raise ValueError(f"第 {lineno} 行缺少顶层键名")
            data.setdefault(current_section, {})
            continue

        if current_section is None:
            raise ValueError(f"第 {lineno} 行在顶层键之前出现缩进字段")
        if not raw_line.startswith("  ") or raw_line.startswith("   "):
            raise ValueError(f"第 {lineno} 行使用了不支持的缩进层级")
        if ":" not in stripped:
            raise ValueError(f"第 {lineno} 行缺少冒号")

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip("'\"")
        if not key:
            raise ValueError(f"第 {lineno} 行缺少字段名")
        data[current_section][key] = value
    return data


def read_project_info(project_root: Path) -> dict[str, str]:
    parsed = parse_simple_yaml_mapping((project_root / "VERSION.yaml").read_text(encoding="utf-8"))
    info = parsed.get("project_info", {})
    if not info.get("name") or not info.get("version"):
        raise ValueError("VERSION.yaml 缺少 project_info.name 或 project_info.version")
    return info


def sanitize_tag(tag: str) -> str:
    return tag.replace("/", "-")


def cleanup_temp_files(project_root: Path, *, verbose: bool) -> list[str]:
    detected: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(TMP_SUFFIXES) or ".tmp." in name or name.endswith(".tmp"):
            detected.append(str(path.relative_to(project_root)))
    if verbose and detected:
        print(f"检测到临时文件 {len(detected)} 个；project-publish 不会自动删除，请按需改用 project-clean-cache。")
    return detected


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"未找到命令：{name}")


def write_command_snapshot(commands_dir: Path, name: str, stdout: str, stderr: str) -> None:
    (commands_dir / f"{name}.stdout.txt").write_text(stdout, encoding="utf-8")
    (commands_dir / f"{name}.stderr.txt").write_text(stderr, encoding="utf-8")


def run_command(args: list[str], *, cwd: Path, commands_dir: Path, snapshot_name: str) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    result = CommandResult(completed.returncode, completed.stdout.strip(), completed.stderr.strip())
    write_command_snapshot(commands_dir, snapshot_name, result.stdout, result.stderr)
    return result


def run_plain_command(args: list[str], *, cwd: Path) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return CommandResult(completed.returncode, completed.stdout.strip(), completed.stderr.strip())


def require_success(result: CommandResult, fallback_message: str) -> str:
    if result.code != 0 or not result.stdout:
        raise RuntimeError(result.stderr or fallback_message)
    return result.stdout.strip()


def load_viewer_login(run_project_command) -> str:
    viewer = run_project_command(["gh", "api", "user", "--jq", ".login"], "preflight_gh_viewer")
    return require_success(viewer, "无法识别当前 gh 登录账号。")


def run_or_preview(
    args: list[str],
    *,
    cwd: Path,
    commands_dir: Path,
    snapshot_name: str,
    dry_run: bool,
) -> CommandResult:
    if dry_run:
        stdout = "DRY RUN: " + " ".join(args)
        write_command_snapshot(commands_dir, snapshot_name, stdout, "")
        return CommandResult(0, stdout, "")
    return run_command(args, cwd=cwd, commands_dir=commands_dir, snapshot_name=snapshot_name)


def parse_github_remote_url(remote_url: str) -> tuple[str, str] | None:
    normalized = remote_url.strip()
    patterns = [
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match:
            return match.group("owner"), match.group("repo")
    return None


def parse_name_with_owner(name_with_owner: str) -> tuple[str, str]:
    if "/" not in name_with_owner:
        raise RuntimeError(f"非法 GitHub 仓库标识：`{name_with_owner}`。")
    owner_login, repo_name = name_with_owner.split("/", 1)
    return owner_login.strip(), repo_name.strip()


def is_missing_repo_message(message: str) -> bool:
    normalized = message.lower()
    return (
        "could not resolve to a repository" in normalized
        or "not found" in normalized
        or ("目标 github 仓库 `" in normalized and "不存在" in normalized)
    )


def build_repo_missing_message(name_with_owner: str, repo_visibility: str | None) -> str:
    if repo_visibility in {"private", "public"}:
        return f"目标 GitHub 仓库 `{name_with_owner}` 不存在，且当前流程未启用自动创建 `{repo_visibility}` 仓库。"
    return f"目标 GitHub 仓库 `{name_with_owner}` 不存在。"


def load_publish_repo_context(run_project_command) -> GitHubRepoContext:
    remote = run_project_command(["git", "remote", "get-url", "origin"], "preflight_git_remote")
    if remote.code != 0 or not remote.stdout:
        raise RuntimeError(remote.stderr or "无法读取 origin 远端地址。")

    parsed_remote = parse_github_remote_url(remote.stdout)
    if parsed_remote is None:
        raise RuntimeError("当前 origin 不是受支持的 GitHub 仓库地址，project-publish 仅支持发布你的 GitHub 仓库。")
    owner_login, repo_name = parsed_remote
    name_with_owner = f"{owner_login}/{repo_name}"

    return load_named_repo_context(name_with_owner, run_project_command, remote_url=remote.stdout.strip())


def load_named_repo_context(
    name_with_owner: str,
    run_project_command,
    *,
    remote_url: str | None = None,
    viewer_login: str | None = None,
) -> GitHubRepoContext:
    resolved_viewer_login = viewer_login or load_viewer_login(run_project_command)

    repo_view = run_project_command(
        ["gh", "repo", "view", name_with_owner, "--json", "nameWithOwner,visibility,owner"],
        "preflight_gh_repo_view",
    )
    if repo_view.code != 0 or not repo_view.stdout:
        stderr = repo_view.stderr or ""
        if is_missing_repo_message(stderr):
            raise RuntimeError(build_repo_missing_message(name_with_owner, None))
        raise RuntimeError(stderr or f"无法读取 GitHub 仓库 `{name_with_owner}` 的元信息。")

    try:
        repo_data = json.loads(repo_view.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 GitHub 仓库信息：{exc}") from exc

    actual_owner = str(repo_data.get("owner", {}).get("login", "")).strip()
    actual_name = str(repo_data.get("nameWithOwner", "")).strip() or name_with_owner
    actual_visibility = str(repo_data.get("visibility", "")).strip().lower()
    return GitHubRepoContext(
        viewer_login=resolved_viewer_login,
        owner_login=actual_owner,
        name_with_owner=actual_name,
        visibility=actual_visibility,
        remote_url=remote_url or f"https://github.com/{name_with_owner}.git",
    )


def ensure_missing_named_repo_created(
    *,
    name_with_owner: str,
    repo_visibility: str,
    run_project_command,
    viewer_login: str,
    dry_run: bool,
    remote_url: str | None = None,
) -> GitHubRepoContext:
    owner_login, repo_name = parse_name_with_owner(name_with_owner)
    if owner_login != viewer_login:
        raise RuntimeError(
            f"当前仓库 `{name_with_owner}` 不属于已登录账号 `{viewer_login}`，project-publish 仅允许发布你自己的 GitHub 仓库。"
        )
    validate_github_repo_name(repo_name, label=f"{repo_visibility} GitHub 仓库名")
    if not dry_run:
        create = run_project_command(
            ["gh", "repo", "create", name_with_owner, f"--{repo_visibility}", "--confirm"],
            "preflight_gh_repo_create",
        )
        if create.code != 0:
            raise RuntimeError(create.stderr or f"自动创建 `{repo_visibility}` GitHub 仓库失败。")
    return GitHubRepoContext(
        viewer_login=viewer_login,
        owner_login=owner_login,
        name_with_owner=name_with_owner,
        visibility=repo_visibility,
        remote_url=remote_url or build_github_repo_https_url(owner_login, repo_name),
    )


def ensure_publish_repo_context(
    repo_visibility: str | None,
    run_project_command,
    *,
    expected_repo_name: str | None = None,
    create_if_missing: bool = False,
    dry_run: bool = False,
) -> GitHubRepoContext:
    if repo_visibility is None:
        raise RuntimeError("请先确认当前 GitHub 仓库是 public 还是 private，并通过 `--repo-visibility` 明确传入。")
    remote = run_project_command(["git", "remote", "get-url", "origin"], "preflight_git_remote")
    if remote.code != 0 or not remote.stdout:
        raise RuntimeError(remote.stderr or "无法读取 origin 远端地址。")
    parsed_remote = parse_github_remote_url(remote.stdout)
    if parsed_remote is None:
        raise RuntimeError("当前 origin 不是受支持的 GitHub 仓库地址，project-publish 仅支持发布你的 GitHub 仓库。")
    owner_login, remote_repo_name = parsed_remote
    name_with_owner = f"{owner_login}/{remote_repo_name}"
    viewer_login = load_viewer_login(run_project_command)
    if expected_repo_name is not None:
        expected_repo_name = expected_target_repo_name(expected_repo_name, repo_visibility)
        if remote_repo_name != expected_repo_name:
            raise RuntimeError(
                "当前 origin 指向的 GitHub 仓库名与默认目标仓库名不一致："
                f"origin 为 `{remote_repo_name}`，默认应为 `{expected_repo_name}`。"
                "请修正 origin，或显式传入目标仓库路径后重试。"
            )
    try:
        context = load_named_repo_context(
            name_with_owner,
            run_project_command,
            remote_url=remote.stdout.strip(),
            viewer_login=viewer_login,
        )
    except RuntimeError as exc:
        if not create_if_missing or not is_missing_repo_message(str(exc)):
            raise
        context = ensure_missing_named_repo_created(
            name_with_owner=name_with_owner,
            repo_visibility=repo_visibility,
            run_project_command=run_project_command,
            viewer_login=viewer_login,
            dry_run=dry_run,
            remote_url=remote.stdout.strip(),
        )
    if context.owner_login != context.viewer_login:
        raise RuntimeError(
            f"当前仓库 `{context.name_with_owner}` 不属于已登录账号 `{context.viewer_login}`，project-publish 仅允许发布你自己的 GitHub 仓库。"
        )
    if context.visibility not in {"public", "private"}:
        raise RuntimeError(
            f"当前仓库 `{context.name_with_owner}` 的可见性为 `{context.visibility or 'unknown'}`，目前只支持确认 `public` 或 `private`。"
        )
    if context.visibility != repo_visibility:
        raise RuntimeError(
            f"你确认的是 `{repo_visibility}`，但 GitHub 仓库 `{context.name_with_owner}` 实际是 `{context.visibility}`，请重新确认后再执行发布。"
        )
    return context


def ensure_named_publish_repo_context(
    repo_visibility: str | None,
    name_with_owner: str,
    run_project_command,
    *,
    create_if_missing: bool = False,
    dry_run: bool = False,
) -> GitHubRepoContext:
    if repo_visibility is None:
        raise RuntimeError("请先确认当前 GitHub 仓库是 public 还是 private，并通过 `--repo-visibility` 明确传入。")
    viewer_login = load_viewer_login(run_project_command)
    try:
        context = load_named_repo_context(
            name_with_owner,
            run_project_command,
            viewer_login=viewer_login,
        )
    except RuntimeError as exc:
        if not create_if_missing or not is_missing_repo_message(str(exc)):
            raise
        context = ensure_missing_named_repo_created(
            name_with_owner=name_with_owner,
            repo_visibility=repo_visibility,
            run_project_command=run_project_command,
            viewer_login=viewer_login,
            dry_run=dry_run,
        )
    if context.owner_login != context.viewer_login:
        raise RuntimeError(
            f"当前仓库 `{context.name_with_owner}` 不属于已登录账号 `{context.viewer_login}`，project-publish 仅允许发布你自己的 GitHub 仓库。"
        )
    if context.visibility not in {"public", "private"}:
        raise RuntimeError(
            f"当前仓库 `{context.name_with_owner}` 的可见性为 `{context.visibility or 'unknown'}`，目前只支持确认 `public` 或 `private`。"
        )
    if context.visibility != repo_visibility:
        raise RuntimeError(
            f"你确认的是 `{repo_visibility}`，但 GitHub 仓库 `{context.name_with_owner}` 实际是 `{context.visibility}`，请重新确认后再执行发布。"
        )
    return context


def ensure_target_repo_name_matches_convention(
    project_name: str,
    repo_visibility: str | None,
    repo_context: GitHubRepoContext,
) -> None:
    if repo_visibility is None:
        return
    expected_name = expected_target_repo_name(project_name, repo_visibility)
    actual_name = repo_context.name_with_owner.split("/", 1)[-1]
    if actual_name != expected_name:
        raise RuntimeError(
            "目标 GitHub 仓库命名不符合默认约定："
            f"`{repo_visibility}` 仓库应命名为 `{expected_name}`，"
            f"当前实际为 `{actual_name}`。"
        )


def validate_release_inputs(
    repo_visibility: str | None,
    release_scope: str | None,
    selected_license: str | None,
    *,
    pack_only: bool,
) -> None:
    resolved_scope = release_scope or "private"
    if pack_only and resolved_scope == "private":
        return
    if not pack_only and repo_visibility is None:
        raise RuntimeError("请先确认目标 GitHub 仓库是 public 还是 private，并通过 `--repo-visibility` 明确传入。")
    if resolved_scope == "public" and selected_license not in PUBLIC_LICENSE_CHOICES:
        choices = " / ".join(PUBLIC_LICENSE_CHOICES)
        raise RuntimeError(f"公开发布前必须先确认发布 license；当前只支持：{choices}。")


def discover_local_publish_candidates(search_roots: tuple[Path, ...] | None = None) -> list[LocalPublishRepoCandidate]:
    candidates: list[LocalPublishRepoCandidate] = []
    for project_root in discover_local_publish_roots(search_roots):
        try:
            context = load_publish_repo_context(
                lambda args, _snapshot_name: run_plain_command(args, cwd=project_root)
            )
        except RuntimeError:
            continue
        if context.owner_login != context.viewer_login:
            continue
        if context.visibility not in {"public", "private"}:
            continue
        candidates.append(
            LocalPublishRepoCandidate(
                project_root=project_root,
                name_with_owner=context.name_with_owner,
                visibility=context.visibility,
                remote_url=context.remote_url,
            )
        )
    return sorted(candidates, key=lambda item: (item.name_with_owner, str(item.project_root)))


def build_github_repo_https_url(owner_login: str, repo_name: str) -> str:
    return f"https://github.com/{owner_login}/{repo_name}.git"


def ensure_public_target_git_repo(
    *,
    source_project_root: Path,
    target_project_root: Path,
    commands_dir: Path,
    expected_repo_name: str,
    release_scope: str,
    verbose: bool,
) -> str:
    entries, _warnings = gather_release_entries(source_project_root, release_scope=release_scope)
    ensure_target_worktree_accepts_entries(target_project_root, entries)
    viewer_login = load_viewer_login(lambda args, snapshot_name: run_command(args, cwd=source_project_root, commands_dir=commands_dir, snapshot_name=snapshot_name))
    remote_url = build_github_repo_https_url(viewer_login, expected_repo_name)
    if not (target_project_root / ".git").exists():
        init_result = run_command(
            ["git", "init", "-b", "main"],
            cwd=target_project_root,
            commands_dir=commands_dir,
            snapshot_name="prepare_public_git_init",
        )
        if init_result.code != 0:
            fallback_init = run_command(
                ["git", "init"],
                cwd=target_project_root,
                commands_dir=commands_dir,
                snapshot_name="prepare_public_git_init_fallback",
            )
            if fallback_init.code != 0:
                raise RuntimeError(fallback_init.stderr or "初始化 public 本地 Git 仓库失败。")
            rename_branch = run_command(
                ["git", "branch", "-M", "main"],
                cwd=target_project_root,
                commands_dir=commands_dir,
                snapshot_name="prepare_public_git_branch_main",
            )
            if rename_branch.code != 0:
                raise RuntimeError(rename_branch.stderr or "初始化 public 本地 Git 仓库后切换到 `main` 失败。")

    copy_seed_entries(target_project_root, entries, verbose=verbose)

    origin = run_command(
        ["git", "remote", "get-url", "origin"],
        cwd=target_project_root,
        commands_dir=commands_dir,
        snapshot_name="prepare_public_git_remote_get",
    )
    if origin.code != 0 or not origin.stdout:
        add_origin = run_command(
            ["git", "remote", "add", "origin", remote_url],
            cwd=target_project_root,
            commands_dir=commands_dir,
            snapshot_name="prepare_public_git_remote_add",
        )
        if add_origin.code != 0:
            raise RuntimeError(add_origin.stderr or "为 public 本地仓库配置 origin 失败。")
        return remote_url

    parsed_remote = parse_github_remote_url(origin.stdout)
    if parsed_remote is None:
        raise RuntimeError("public 本地仓库的 origin 不是受支持的 GitHub 仓库地址。")
    actual_owner, actual_repo = parsed_remote
    if actual_owner != viewer_login or actual_repo != expected_repo_name:
        raise RuntimeError(
            "public 本地仓库的 origin 与预期目标仓库不一致："
            f"期望 `{viewer_login}/{expected_repo_name}`，实际为 `{actual_owner}/{actual_repo}`。"
        )
    return origin.stdout.strip()


def ensure_private_target_git_repo(
    *,
    source_project_root: Path,
    target_project_root: Path,
    verbose: bool,
) -> None:
    entries, _warnings = gather_release_entries(source_project_root, release_scope="private")
    ensure_target_worktree_accepts_entries(target_project_root, entries)
    copy_seed_entries(target_project_root, entries, verbose=verbose)
