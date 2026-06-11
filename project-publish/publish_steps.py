from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from publish_models import PublishOptions, PublishSession, StepOutcome


CommandRunner = Callable[[list[str]], object]


def record_skipped_prepare_steps() -> list[StepOutcome]:
    return [
        StepOutcome("step1_commit_push", "skipped", "pack-only 模式跳过提交与 push", {}),
        StepOutcome("step2_create_tag", "skipped", "pack-only 模式跳过 tag 创建", {}),
        StepOutcome("step3_create_release", "skipped", "pack-only 模式跳过 Release 创建", {}),
    ]


def ensure_pack_script(project_root: Path, release_scope: str) -> Path:
    _ = release_scope
    script = project_root / "project-publish" / "pack_release.py"
    if not script.is_file():
        raise RuntimeError(f"缺少 {script.relative_to(project_root).as_posix()}")
    return script


def ensure_git_repo(run_project_command: Callable[[list[str], str], object]) -> None:
    result = run_project_command(["git", "rev-parse", "--is-inside-work-tree"], "preflight_git_repo")
    if result.code != 0 or result.stdout != "true":
        raise RuntimeError("当前目录不是 Git 工作树，无法执行发布流程。")


def ensure_gh_auth(run_project_command: Callable[[list[str], str], object]) -> None:
    result = run_project_command(["gh", "auth", "status"], "preflight_gh_auth")
    if result.code != 0:
        raise RuntimeError(result.stderr or "gh CLI 未完成认证，请先执行 gh auth login。")


def _parse_version_string(version: str) -> tuple[int, int, int] | None:
    """从形如 '2.40.1' / '2.40.1 (2023-12-13)' 的字符串中解析主版本号元组。"""
    cleaned = version.strip().split()[0] if version.strip() else ""
    if not cleaned:
        return None
    parts = cleaned.split(".")
    if len(parts) < 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) >= 3 else 0
    except ValueError:
        return None
    if major < 0 or minor < 0 or patch < 0:
        return None
    return (major, minor, patch)


def _parse_gh_version(stdout: str) -> tuple[int, int, int] | None:
    """从 `gh --version` 的输出中解析 gh CLI 版本号。

    期望第一行形如：`gh version 2.40.1 (2023-12-13)`。
    """
    if not stdout:
        return None
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("gh version"):
            continue
        tokens = stripped.split()
        if len(tokens) < 3:
            return None
        return _parse_version_string(tokens[2])
    return None


def ensure_gh_version(
    run_project_command: Callable[[list[str], str], object],
    min_version: tuple[int, int, int] = (2, 0, 0),
) -> tuple[int, int, int]:
    """要求 gh CLI 版本不低于 min_version，避免旧版缺少 --generate-notes 等能力。

    返回当前 gh CLI 的实际版本号。
    """
    result = run_project_command(["gh", "--version"], "preflight_gh_version")
    if result.code != 0:
        raise RuntimeError(result.stderr or "无法读取 gh CLI 版本信息。")
    parsed = _parse_gh_version(result.stdout)
    if parsed is None:
        raise RuntimeError(
            f"无法解析 gh CLI 版本：{result.stdout.strip() or '(空输出)'}；请升级到 >= "
            f"{min_version[0]}.{min_version[1]}.{min_version[2]} 后重试。"
        )
    if parsed < min_version:
        actual = ".".join(str(part) for part in parsed)
        required = ".".join(str(part) for part in min_version)
        raise RuntimeError(
            f"当前 gh CLI 版本为 {actual}，发布流程要求 >= {required}；请升级 gh 后重试。"
        )
    return parsed


def ensure_branch_name(
    run_project_command: Callable[[list[str], str], object],
    *,
    snapshot_name: str = "step1_branch_name",
    fallback_snapshot_name: str = "step1_branch_name_fallback",
    detached_message: str = "当前处于 detached HEAD，无法执行默认发布提交。",
) -> str:
    result = run_project_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], snapshot_name)
    branch = result.stdout.strip() if result.code == 0 else ""
    if not branch or branch == "HEAD":
        fallback = run_project_command(["git", "symbolic-ref", "--short", "HEAD"], fallback_snapshot_name)
        if fallback.code == 0 and fallback.stdout.strip():
            branch = fallback.stdout.strip()
    if not branch or branch == "HEAD":
        raise RuntimeError(detached_message)
    return branch


def ensure_release_branch(branch: str, *, repo_label: str = "当前仓库") -> None:
    if branch != "main":
        if repo_label == "当前仓库":
            raise RuntimeError(f"发布流程只允许从本地 `main` 分支执行，当前分支为 `{branch}`。")
        raise RuntimeError(f"{repo_label}当前分支必须为 `main`，当前分支为 `{branch}`。")


def ensure_main_branch(
    run_project_command: Callable[[list[str], str], object],
    *,
    repo_label: str = "当前仓库",
    snapshot_name: str = "preflight_branch_name",
    fallback_snapshot_name: str = "preflight_branch_name_fallback",
) -> str:
    detached_message = "当前处于 detached HEAD，发布流程只允许在本地 `main` 分支执行。"
    if repo_label != "当前仓库":
        detached_message = f"{repo_label}当前处于 detached HEAD，发布流程只允许在本地 `main` 分支执行。"
    branch = ensure_branch_name(
        run_project_command,
        snapshot_name=snapshot_name,
        fallback_snapshot_name=fallback_snapshot_name,
        detached_message=detached_message,
    )
    ensure_release_branch(branch, repo_label=repo_label)
    return branch


def resolve_upstream_branch(run_project_command: Callable[[list[str], str], object]) -> str | None:
    result = run_project_command(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        "step1_git_upstream",
    )
    if result.code != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def fetch_upstream(
    upstream: str,
    run_project_command: Callable[[list[str], str], object],
) -> None:
    remote = upstream.split("/", 1)[0] if "/" in upstream else "origin"
    result = run_project_command(["git", "fetch", remote], "step1_git_fetch")
    if result.code != 0:
        raise RuntimeError(result.stderr or f"无法抓取上游 `{upstream}` 的最新状态。")


def read_ahead_behind(
    run_project_command: Callable[[list[str], str], object],
) -> tuple[int, int]:
    result = run_project_command(
        ["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"],
        "step1_git_ahead_behind",
    )
    if result.code != 0:
        raise RuntimeError(result.stderr or "无法读取当前分支与上游的同步状态。")
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        raise RuntimeError("无法解析当前分支与上游的 ahead/behind 统计。")
    try:
        ahead, behind = (int(parts[0]), int(parts[1]))
    except ValueError as exc:
        raise RuntimeError("无法解析当前分支与上游的 ahead/behind 统计。") from exc
    return ahead, behind


def sync_branch_with_upstream(
    *,
    branch: str,
    has_uncommitted_changes: bool,
    repo_label: str,
    run_project_command: Callable[[list[str], str], object],
    run_or_preview: Callable[[list[str], str], object],
) -> tuple[str, str | None]:
    upstream = resolve_upstream_branch(run_project_command)
    if upstream is None:
        if has_uncommitted_changes:
            return "commit_and_push", None
        raise RuntimeError(
            f"{repo_label}当前分支 `{branch}` 未设置上游，无法确认是否已同步到最新远端提交。"
        )
    if upstream != "origin/main":
        raise RuntimeError(
            f"{repo_label}发布时要求本地 `main` 对齐 `origin/main`，当前上游为 `{upstream}`。"
        )

    fetch_upstream(upstream, run_project_command)
    ahead, behind = read_ahead_behind(run_project_command)
    if ahead > 0 and behind > 0:
        raise RuntimeError(
            f"{repo_label}当前分支 `{branch}` 与上游 `{upstream}` 已分叉，请先手动同步后再发布。"
        )
    if behind > 0 and has_uncommitted_changes:
        raise RuntimeError(
            f"{repo_label}当前分支 `{branch}` 落后于上游 `{upstream}`，且存在未提交改动，无法安全快进。"
        )
    if behind > 0:
        fast_forward = run_or_preview(["git", "merge", "--ff-only", "@{u}"], "step1_git_fast_forward")
        if fast_forward.code != 0:
            raise RuntimeError(fast_forward.stderr or f"无法将 `{branch}` 快进到 `{upstream}`。")
        return "fast_forward", upstream
    if ahead > 0 and not has_uncommitted_changes:
        push = run_or_preview(["git", "push"], "step1_git_push")
        if push.code != 0:
            raise RuntimeError(push.stderr or "git push 执行失败。")
        return "push_only", upstream
    return "commit_and_push" if has_uncommitted_changes else "noop", upstream


def step_commit_and_push(
    *,
    session: PublishSession,
    run_project_command: Callable[[list[str], str], object],
    run_or_preview: Callable[[list[str], str], object],
    repo_label: str = "当前仓库",
    commit_message: str | None = None,
    dry_run: bool = False,
) -> StepOutcome:
    branch = ensure_main_branch(
        run_project_command,
        repo_label=repo_label,
        snapshot_name="step1_branch_name",
        fallback_snapshot_name="step1_branch_name_fallback",
    )
    upstream = resolve_upstream_branch(run_project_command)

    details: dict[str, object] = {
        "branch": branch,
        "upstream": upstream,
        "sync_action": "noop",
    }

    # 自动 bump 检查：若最新 tag 存在于旧 commit 且有新 commit，自动递增 VERSION.yaml
    bumped, new_tag = bump_version_if_needed(
        session=session,
        run_project_command=run_project_command,
        run_or_preview=run_or_preview,
        upstream=upstream,
        branch=branch,
        dry_run=dry_run,
    )
    if bumped and new_tag:
        details["bumped_tag"] = new_tag
        details["sync_action"] = "bumped"
        return StepOutcome(
            name="step1_commit_push",
            status="success",
            message=f"已自动检测到新 commit，VERSION.yaml 已递增至 {new_tag} 并推送",
            details=details,
        )

    status = run_project_command(["git", "status", "--porcelain"], "step1_status")
    if status.code != 0:
        raise RuntimeError(status.stderr or "无法读取 Git 状态。")
    has_uncommitted_changes = bool(status.stdout.strip())
    sync_action, upstream = sync_branch_with_upstream(
        branch=branch,
        has_uncommitted_changes=has_uncommitted_changes,
        repo_label=repo_label,
        run_project_command=run_project_command,
        run_or_preview=run_or_preview,
    )
    details["upstream"] = upstream
    details["sync_action"] = sync_action

    if not has_uncommitted_changes:
        if sync_action == "fast_forward":
            return StepOutcome(
                name="step1_commit_push",
                status="success",
                message=f"已将{repo_label}快进到上游最新提交",
                details=details,
            )
        if sync_action == "push_only":
            return StepOutcome(
                name="step1_commit_push",
                status="success",
                message=f"已将{repo_label}当前分支的最新提交推送到远端",
                details=details,
            )
        return StepOutcome(
            name="step1_commit_push",
            status="skipped",
            message=f"{repo_label}无待提交改动，且当前分支已同步到上游最新提交，跳过提交与 push",
            details=details,
        )

    resolved_commit_message = commit_message or f"chore(release): publish {session.tag}"
    run_or_preview(["git", "add", "-A"], "step1_git_add")
    commit = run_or_preview(["git", "commit", "-m", resolved_commit_message], "step1_git_commit")
    if commit.code != 0:
        raise RuntimeError(commit.stderr or "git commit 执行失败。")

    push_args = ["git", "push"] if upstream is not None else ["git", "push", "-u", "origin", branch]
    push = run_or_preview(push_args, "step1_git_push")
    if push.code != 0:
        raise RuntimeError(push.stderr or "git push 执行失败。")

    details["commit_message"] = resolved_commit_message
    return StepOutcome(
        name="step1_commit_push",
        status="success",
        message=f"已完成{repo_label}提交并推送",
        details=details,
    )


def get_latest_tag(
    run_project_command: Callable[[list[str], str], object],
) -> tuple[str, str] | None:
    """返回最新 tag 的名称和对应 commit hash，若无 tag 则返回 None。"""
    result = run_project_command(
        ["git", "describe", "--tags", "--abbrev=0"],
        "step1_latest_tag",
    )
    if result.code != 0:
        return None
    tag_name = result.stdout.strip()
    if not tag_name:
        return None
    commit_result = run_project_command(
        ["git", "rev-parse", tag_name],
        "step1_latest_tag_commit",
    )
    if commit_result.code != 0:
        return None
    return tag_name, commit_result.stdout.strip()


def is_head_commits_since_tag(
    run_project_command: Callable[[list[str], str], object],
    tag_commit: str,
) -> bool:
    """检查 HEAD 是否已超前于指定 tag commit（即是否有新 commit）。"""
    result = run_project_command(
        ["git", "rev-list", "--count", f"{tag_commit}..HEAD"],
        "step1_commits_since_tag",
    )
    if result.code != 0:
        return False
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def _parse_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _format_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def bump_version_file(version_path: Path) -> str:
    """将 VERSION.yaml 中的 patch 版本加 1，写回文件，返回新的版本字符串。"""
    text = version_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines = []
    bumped = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("version:") and not bumped:
            key, val = stripped.split(":", 1)
            old_ver = val.strip().strip("'\"")
            major, minor, patch = _parse_version(old_ver)
            new_ver = _format_version(major, minor, patch + 1)
            new_lines.append(f"{line[:len(line) - len(line.lstrip())]}version: {new_ver}")
            bumped = True
        else:
            new_lines.append(line)
    if not bumped:
        raise RuntimeError("VERSION.yaml 中未找到 version 字段，无法自动递增。")
    version_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return _format_version(major, minor, patch + 1)


def predict_release_tag(
    session: PublishSession,
    run_project_command: Callable[[list[str], str], object],
) -> str:
    tag_info = get_latest_tag(run_project_command)
    if tag_info is None:
        return session.tag

    tag_name, tag_commit = tag_info
    from publish_support import read_project_info

    info = read_project_info(session.project_root)
    current_ver = info.get("version", "")
    if current_ver != tag_name.lstrip("v"):
        return session.tag
    if not is_head_commits_since_tag(run_project_command, tag_commit):
        return session.tag
    major, minor, patch = _parse_version(current_ver)
    return f"v{_format_version(major, minor, patch + 1)}"


def bump_version_if_needed(
    session: PublishSession,
    run_project_command: Callable[[list[str], str], object],
    run_or_preview: Callable[[list[str], str], object],
    upstream: str | None,
    branch: str,
    dry_run: bool = False,
) -> tuple[bool, str | None]:
    """
    检查是否需要自动 bump VERSION.yaml。
    若最新 tag 存在于不同 commit 且有新 commit，则 bump。
    返回 (是否执行了 bump, 新的 tag 名称)。若未 bump，新 tag 为 None。
    """
    tag_info = get_latest_tag(run_project_command)
    if tag_info is None:
        return False, None
    tag_name, tag_commit = tag_info
    # 检查当前 VERSION.yaml 的版本
    version_path = session.project_root / "VERSION.yaml"
    from publish_support import read_project_info
    info = read_project_info(session.project_root)
    current_ver = info.get("version", "")
    # 从 tag 名提取版本（如 v0.2.5 → 0.2.5）
    ver_from_tag = tag_name.lstrip("v")
    # 若 VERSION.yaml 版本与 tag 版本一致，说明还未 bump
    if current_ver != ver_from_tag:
        # 已 bump 过，不需要再次 bump
        return False, None
    if not is_head_commits_since_tag(run_project_command, tag_commit):
        return False, None
    if dry_run:
        major, minor, patch = _parse_version(current_ver)
        return False, f"v{_format_version(major, minor, patch + 1)}"
    # 需要 bump：先暂存所有改动（包括用户未提交的内容），再更新 VERSION.yaml，一起 commit
    new_ver = bump_version_file(version_path)
    new_tag = f"v{new_ver}"
    commit_msg = f"chore(release): bump to {new_ver} for release"
    # git add -A 暂存所有改动，确保用户的内容变更和 VERSION bump 在同一 commit
    add_result = run_or_preview(["git", "add", "-A"], "step1_bump_add")
    if add_result.code != 0:
        raise RuntimeError(add_result.stderr or "git add -A 失败。")
    commit_result = run_or_preview(["git", "commit", "-m", commit_msg], "step1_bump_commit")
    if commit_result.code != 0:
        raise RuntimeError(commit_result.stderr or "git commit 失败。")
    push_args = ["git", "push"] if upstream is not None else ["git", "push", "-u", "origin", branch]
    push_result = run_or_preview(push_args, "step1_bump_push")
    if push_result.code != 0:
        raise RuntimeError(push_result.stderr or "git push 失败。")
    return True, new_tag


def tag_exists(session: PublishSession, run_project_command: Callable[[list[str], str], object]) -> bool:
    result = run_project_command(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{session.tag}"],
        "step2_tag_exists",
    )
    return result.code == 0


def step_create_tag(
    *,
    session: PublishSession,
    options: PublishOptions,
    run_target_command: Callable[[list[str], str], object],
    run_target_or_preview: Callable[[list[str], str], object],
) -> StepOutcome:
    if tag_exists(session, run_target_command):
        # 检查现有 tag 是否已指向当前 HEAD
        verify_tag = run_target_command(["git", "rev-parse", session.tag], "step2_git_verify_tag")
        verify_head = run_target_command(["git", "rev-parse", "HEAD"], "step2_git_verify_head")
        tag_commit = verify_tag.stdout.strip() if verify_tag.code == 0 else ""
        head_commit = verify_head.stdout.strip() if verify_head.code == 0 else ""
        if tag_commit == head_commit and tag_commit:
            # tag 已指向 HEAD，说明是幂等重复执行，跳过
            return StepOutcome(
                "step2_create_tag",
                "success",
                f"tag `{session.tag}` 已存在且已指向当前 HEAD，跳过创建",
                {"tag": session.tag, "skipped": True},
            )
        # tag 存在但指向旧 commit，移动到当前 HEAD
        if not options.dry_run:
            run_target_command(["git", "tag", "-f", session.tag], "step2_git_tag_move")
            run_target_or_preview(["git", "push", "origin", session.tag, "--force"], "step2_git_push_tag_move")
            return StepOutcome(
                "step2_create_tag",
                "success",
                f"tag `{session.tag}` 已存在但指向旧 commit，已强制移动至当前 HEAD",
                {"tag": session.tag, "moved": True},
            )

    create_tag = run_target_or_preview(["git", "tag", session.tag], "step2_git_tag")
    if create_tag.code != 0:
        raise RuntimeError(create_tag.stderr or "创建 tag 失败。")

    push_tag = run_target_or_preview(["git", "push", "origin", session.tag], "step2_git_push_tag")
    if push_tag.code != 0:
        raise RuntimeError(push_tag.stderr or "推送 tag 失败。")

    if not options.dry_run:
        verify_tag = run_target_command(["git", "rev-parse", session.tag], "step2_git_verify_tag")
        if verify_tag.code != 0:
            raise RuntimeError(verify_tag.stderr or "tag 校验失败。")
        verify_head = run_target_command(["git", "rev-parse", "HEAD"], "step2_git_verify_head")
        if verify_head.code != 0:
            raise RuntimeError(verify_head.stderr or "无法读取当前 HEAD，无法确认 tag 指向。")
        tag_commit = verify_tag.stdout.strip()
        head_commit = verify_head.stdout.strip()
        if tag_commit != head_commit:
            raise RuntimeError(
                f"tag `{session.tag}` 没有指向当前 HEAD：tag={tag_commit}，HEAD={head_commit}。"
            )

    return StepOutcome("step2_create_tag", "success", "已创建并校验 tag", {"tag": session.tag})


def release_exists(session: PublishSession, run_project_command: Callable[[list[str], str], object]) -> bool:
    result = run_project_command(
        ["gh", "release", "view", session.tag, "--json", "url", "-q", ".url"],
        "step3_release_exists",
    )
    return result.code == 0


def step_create_release(
    *,
    session: PublishSession,
    options: PublishOptions,
    run_target_command: Callable[[list[str], str], object],
    run_target_or_preview: Callable[[list[str], str], object],
) -> StepOutcome:
    if release_exists(session, run_target_command):
        raise RuntimeError(f"GitHub Release `{session.tag}` 已存在，发布流程在第 3 步停止。")

    create = run_target_or_preview(
        ["gh", "release", "create", session.tag, "--title", session.tag, "--generate-notes"],
        "step3_release_create",
    )
    if create.code != 0:
        raise RuntimeError(create.stderr or "创建 GitHub Release 失败。")

    release_url = "DRY RUN: release URL not created"
    release_notes = ""
    if not options.dry_run:
        view = run_target_command(
            ["gh", "release", "view", session.tag, "--json", "url,body"],
            "step3_release_view",
        )
        if view.code != 0 or not view.stdout:
            raise RuntimeError(view.stderr or "无法读取新建 Release 的 URL。")
        try:
            release_data = json.loads(view.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"无法解析 GitHub Release 详情：{exc}") from exc
        release_url = str(release_data.get("url", "")).strip()
        release_notes = str(release_data.get("body", "")).strip()
        if not release_url:
            raise RuntimeError("无法读取新建 Release 的 URL。")

    return StepOutcome(
        "step3_create_release",
        "success",
        "已创建 GitHub Release",
        {"release_url": release_url, "release_notes": release_notes},
    )


def step_pack_release(
    *,
    session: PublishSession,
    options: PublishOptions,
    run_source_command: Callable[[list[str], str], object],
    target_repo: str | None,
    pack_script: Path,
) -> StepOutcome:
    args = [sys.executable, str(pack_script), "--tag", session.tag]
    if options.repo_visibility is not None:
        args.extend(["--repo-visibility", options.repo_visibility])
    if options.selected_license is not None:
        args.extend(["--license", options.selected_license])
    if target_repo is not None:
        args.extend(["--target-repo", target_repo])
    args.append("--confirmed")
    if options.dry_run:
        args.append("--dry-run")
    if options.verbose:
        args.append("--verbose")
    if not options.dry_run and not options.skip_upload and not options.pack_only:
        args.append("--upload")

    result = run_source_command(args, "step4_pack_release")
    if result.code != 0:
        raise RuntimeError(result.stderr or f"{pack_script.relative_to(session.project_root).as_posix()} 执行失败。")

    try:
        pack_result = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 pack_release.py 输出：{exc}") from exc

    session.session_dir.mkdir(parents=True, exist_ok=True)
    (session.session_dir / "pack_release_result.json").write_text(
        json.dumps(pack_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not (options.skip_upload or options.pack_only or options.dry_run) and not pack_result.get("uploaded"):
        error = pack_result.get("error") or "Assets 未成功上传。"
        raise RuntimeError(str(error))

    pack_result["pack_script"] = pack_script.relative_to(session.project_root).as_posix()
    return StepOutcome("step4_pack_release", "success", "已执行打包脚本", pack_result)
