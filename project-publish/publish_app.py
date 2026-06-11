from __future__ import annotations

import sys
from dataclasses import replace
from typing import Callable
from pathlib import Path

from publish_models import GitHubRepoContext, PublishOptions, PublishSession, StepOutcome
from publish_reporting import (
    build_wechat_post_for_project,
    is_publish_complete,
    print_pack_summary,
    write_summary,
    write_wechat_archive,
)
from publish_steps import (
    ensure_gh_auth,
    ensure_gh_version,
    ensure_git_repo,
    ensure_main_branch,
    ensure_pack_script,
    predict_release_tag,
    record_skipped_prepare_steps,
    step_commit_and_push,
    step_create_release,
    step_create_tag,
    step_pack_release,
)
from publish_support import (
    PUBLIC_LICENSE_CHOICES,
    cleanup_temp_files,
    ensure_command,
    ensure_named_publish_repo_context,
    ensure_private_target_git_repo,
    ensure_public_target_git_repo,
    ensure_target_repo_name_matches_convention,
    find_git_repo_root,
    find_project_root,
    infer_default_target_repo_root,
    read_project_info,
    require_success,
    run_command,
    run_or_preview,
    sanitize_tag,
    validate_github_repo_name,
    validate_release_inputs,
)
from release_confirmation import (
    build_release_manifest,
    print_release_manifest,
    require_release_confirmation,
    write_release_manifest,
)


class ProjectPublishApp:
    def __init__(self, options: PublishOptions) -> None:
        self.options = options
        self.session = self._build_session()
        self.target_project_root = self._build_target_project_root()
        self.pack_script = ensure_pack_script(self.session.project_root, self.release_scope())
        self.step_outcomes: list[StepOutcome] = []
        self.failed_step: str | None = None
        self.failure_reason: str | None = None
        self.release_url = ""
        self.release_manifest: list[str] = []
        self.current_step = "preflight"
        self.repo_context: GitHubRepoContext | None = None

    def _build_session(self) -> PublishSession:
        project_root = find_project_root(self.options.repo_path)
        cleanup_temp_files(project_root, verbose=self.options.verbose)
        project_info = read_project_info(project_root)
        tag = self.options.tag or f"v{project_info['version']}"
        release_scope = self.options.release_scope or "private"
        session_dir = project_root / ".cache" / "project-publish" / release_scope / sanitize_tag(tag)
        commands_dir = session_dir / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        return PublishSession(
            project_root=project_root,
            tag=tag,
            project_info=project_info,
            session_dir=session_dir,
            commands_dir=commands_dir,
        )

    def _build_target_project_root(self):
        if self.options.target_repo_path is None:
            if self.release_scope() == "public":
                return self.session.session_dir / "target-repo"
            return infer_default_target_repo_root(
                self.session.project_root,
                self.session.project_root.name,
                self.options.repo_visibility,
            )
        if self.release_scope() == "public":
            return Path(self.options.target_repo_path).expanduser().resolve()
        return find_git_repo_root(
            self.options.target_repo_path,
            default_path=self.session.project_root,
        )

    def run(self) -> int:
        try:
            self.preflight()
            self.print_header()
            self.preview_release()
            self.run_steps()
        except Exception as exc:
            self.failed_step = self.current_step
            self.failure_reason = str(exc)
            print(f"错误：{self.failure_reason}", file=sys.stderr)
        return self.finish()

    def preflight(self) -> None:
        self.ensure_public_license_selection()
        validate_release_inputs(
            self.options.repo_visibility,
            self.options.release_scope,
            self.options.selected_license,
            pack_only=self.options.pack_only,
        )
        ensure_command("git")
        ensure_git_repo(self.run_source_command)
        ensure_main_branch(self.run_source_command, repo_label="源码仓库")
        self.preflight_target_branch_if_needed()
        if self.options.pack_only:
            return
        ensure_command("gh")
        ensure_gh_version(self.run_source_command)
        ensure_gh_auth(self.run_source_command)
        self.repo_context = ensure_named_publish_repo_context(
            self.options.repo_visibility,
            self.target_repo_name_with_owner(),
            self.run_source_command,
            create_if_missing=True,
            dry_run=self.options.dry_run,
        )
        if self.release_scope() == "public":
            self.sync_public_target_repo()
            ensure_git_repo(self.run_target_command)
        else:
            ensure_git_repo(self.run_target_command)
            ensure_gh_auth(self.run_target_command)
        ensure_target_repo_name_matches_convention(
            self.expected_target_project_name(),
            self.options.repo_visibility,
            self.repo_context,
        )

    def ensure_public_license_selection(self) -> None:
        if self.release_scope() != "public":
            return
        if self.options.selected_license in PUBLIC_LICENSE_CHOICES:
            return
        self.options = replace(
            self.options,
            selected_license=self.prompt_public_license_selection(),
        )

    def prompt_public_license_selection(self) -> str:
        choices = "\n".join(
            f"{index}. {license_name}" for index, license_name in enumerate(PUBLIC_LICENSE_CHOICES, start=1)
        )
        answer = input(
            "公开发布前必须先确认发布 license，请输入编号或协议名：\n"
            f"{choices}\n"
            "选择："
        ).strip()
        return self.resolve_public_license_choice(answer)

    def resolve_public_license_choice(self, answer: str) -> str:
        normalized = answer.strip()
        indexed_choices = {
            str(index): license_name
            for index, license_name in enumerate(PUBLIC_LICENSE_CHOICES, start=1)
        }
        if normalized in indexed_choices:
            return indexed_choices[normalized]
        if normalized in PUBLIC_LICENSE_CHOICES:
            return normalized
        choices = " / ".join(PUBLIC_LICENSE_CHOICES)
        raise RuntimeError(f"仅支持以下发布 license：{choices}。")

    def preflight_target_branch_if_needed(self) -> None:
        if self.target_project_root == self.session.project_root:
            return
        if self.options.target_repo_path is None:
            return
        if not (self.target_project_root / ".git").exists():
            return
        ensure_git_repo(self.run_target_command)
        repo_label = "public 目标仓库" if self.release_scope() == "public" else "private 目标仓库"
        ensure_main_branch(self.run_target_command, repo_label=repo_label)

    def print_header(self) -> None:
        print(f"源码项目根目录：{self.session.project_root}")
        print(f"发布 tag：{self.session.tag}")
        print(f"会话目录：{self.session.session_dir}")
        if self.target_project_root != self.session.project_root:
            print(f"发布目标仓库根目录：{self.target_project_root}")
        if self.repo_context is not None:
            print(f"目标仓库可见性（已确认）：{self.repo_context.visibility}")
        if self.options.selected_license is not None:
            print(f"发布 license（已确认）：{self.options.selected_license}")
        print(f"发布范围：{self.release_scope()}")
        if self.options.dry_run:
            print("模式：dry-run")

    def release_scope(self) -> str:
        return self.options.release_scope or "private"

    def expected_target_project_name(self) -> str:
        target_name = (
            self.session.project_root.name
            if self.release_scope() == "private"
            else self.session.project_info["name"]
        )
        return validate_github_repo_name(target_name, label=f"{self.release_scope()} GitHub 仓库名")

    def target_repo_name_with_owner(self) -> str:
        owner_login = require_success(
            self.run_source_command(["gh", "api", "user", "--jq", ".login"], "preflight_target_repo_owner"),
            "无法识别当前 gh 登录账号。",
        )
        return f"{owner_login}/{self.expected_target_project_name()}"

    def sync_public_target_repo(self) -> None:
        ensure_public_target_git_repo(
            source_project_root=self.session.project_root,
            target_project_root=self.target_project_root,
            commands_dir=self.session.commands_dir,
            expected_repo_name=self.expected_target_project_name(),
            release_scope=self.release_scope(),
            verbose=self.options.verbose,
        )

    def sync_private_target_repo(self) -> None:
        ensure_private_target_git_repo(
            source_project_root=self.session.project_root,
            target_project_root=self.target_project_root,
            verbose=self.options.verbose,
        )

    def sync_distinct_target_repo(self) -> None:
        if self.target_project_root == self.session.project_root:
            return
        if self.release_scope() == "public":
            self.sync_public_target_repo()
            return
        self.sync_private_target_repo()

    def requires_confirmation(self) -> bool:
        if self.options.dry_run or self.options.pack_only:
            return False
        return True

    def preview_release(self) -> None:
        self.current_step = "preview_release"
        self.release_manifest = build_release_manifest(
            self.session.project_root,
            release_scope=self.release_scope(),
            selected_license=self.options.selected_license,
            exclude_skills=self.options.exclude_skills,
        )
        print_release_manifest(
            release_scope=self.release_scope(),
            tag=self.session.tag,
            manifest=self.release_manifest,
            selected_license=self.options.selected_license,
        )
        write_release_manifest(self.session.session_dir / "release_manifest.txt", self.release_manifest)
        if self.requires_confirmation():
            require_release_confirmation(release_scope=self.release_scope())

    def run_steps(self) -> None:
        if self.options.pack_only:
            self.step_outcomes.extend(record_skipped_prepare_steps())
        else:
            self.run_prepare_steps()
        self.run_pack_step()
        self.run_wechat_step()

    def run_prepare_steps(self) -> None:
        step1_outcome = self.run_step("步骤 1/5：提交并 push 当前改动", "step1_commit_push", self.step_commit_and_push)
        # 若 step1 执行了自动 bump，用新的 tag 更新 session
        bumped_tag: str | None = step1_outcome.details.get("bumped_tag")  # type: ignore[assignment]
        if bumped_tag:
            from publish_support import sanitize_tag
            new_session_dir = self.session.project_root / ".cache" / "project-publish" / self.release_scope() / sanitize_tag(bumped_tag)
            new_session_dir.mkdir(parents=True, exist_ok=True)
            self.session = replace(
                self.session,
                tag=bumped_tag,
                session_dir=new_session_dir,
            )
        self.run_step("步骤 2/5：创建并校验 tag", "step2_create_tag", self.step_create_tag)
        release_outcome = self.run_step("步骤 3/5：创建 GitHub Release", "step3_create_release", self.step_create_release)
        self.release_url = str(release_outcome.details.get("release_url", ""))

    def run_pack_step(self) -> None:
        pack_script_label = self.pack_script.relative_to(self.session.project_root).as_posix()
        outcome = self.run_step(
            f"步骤 4/5：执行 {pack_script_label}",
            "step4_pack_release",
            self.step_pack_release,
        )
        if not self.release_url:
            self.release_url = str(outcome.details.get("release_url", ""))

    def run_wechat_step(self) -> None:
        print("步骤 5/5：生成微信动态文案")
        self.current_step = "step5_wechat_post"
        release_url = self.release_url or self.build_release_url(self.session.tag)
        wechat_post = self.build_wechat_post_content(self.session.tag, release_url)
        print("\n微信动态文案：")
        print(wechat_post)
        if self.options.dry_run:
            self.step_outcomes.append(
                StepOutcome(
                    name="step5_wechat_post",
                    status="success",
                    message="已生成微信动态文案（dry-run 未归档）",
                    details={"dry_run": True},
                )
            )
            return
        wechat_path = write_wechat_archive(
            project_root=self.session.project_root,
            tag=self.session.tag,
            release_url=release_url,
            content=wechat_post,
        )
        print(f"\n微信文案已归档：{wechat_path}")
        self.step_outcomes.append(
            StepOutcome(
                name="step5_wechat_post",
                status="success",
                message="已生成并归档微信动态文案",
                details={"path": str(wechat_path)},
            )
        )

    def build_release_url(self, tag: str) -> str:
        if self.options.dry_run:
            return "DRY RUN: release URL not created"
        if self.repo_context is None:
            return self.release_url or ""
        return f"https://github.com/{self.repo_context.name_with_owner}/releases/tag/{tag}"

    def build_wechat_post_content(self, tag: str, release_url: str) -> str:
        release_notes = self.release_notes_for_wechat()
        return build_wechat_post_for_project(
            project_root=self.session.project_root,
            project_name=self.expected_target_project_name(),
            release_notes=release_notes,
            release_manifest=self.release_manifest,
            project_description=self.session.project_info.get("description", ""),
            tag=tag,
            release_url=release_url,
        )

    def release_notes_for_wechat(self) -> str:
        for outcome in reversed(self.step_outcomes):
            if outcome.name != "step3_create_release":
                continue
            return str(outcome.details.get("release_notes", "")).strip()
        return ""

    def sync_wechat_archive_for_release(self, tag: str) -> None:
        if self.options.dry_run or self.options.pack_only:
            return
        release_url = self.build_release_url(tag)
        wechat_post = self.build_wechat_post_content(tag, release_url)
        write_wechat_archive(
            project_root=self.session.project_root,
            tag=tag,
            release_url=release_url,
            content=wechat_post,
        )

    def run_step(self, label: str, step_name: str, handler: Callable[[], StepOutcome]) -> StepOutcome:
        print(label)
        self.current_step = step_name
        outcome = handler()
        self.step_outcomes.append(outcome)
        return outcome

    def run_source_command(self, args: list[str], snapshot_name: str):
        return run_command(
            args,
            cwd=self.session.project_root,
            commands_dir=self.session.commands_dir,
            snapshot_name=snapshot_name,
        )

    def run_source_or_preview(self, args: list[str], snapshot_name: str):
        return run_or_preview(
            args,
            cwd=self.session.project_root,
            commands_dir=self.session.commands_dir,
            snapshot_name=snapshot_name,
            dry_run=self.options.dry_run,
        )

    def run_target_command(self, args: list[str], snapshot_name: str):
        return run_command(
            args,
            cwd=self.target_project_root,
            commands_dir=self.session.commands_dir,
            snapshot_name=snapshot_name,
        )

    def run_target_or_preview(self, args: list[str], snapshot_name: str):
        return run_or_preview(
            args,
            cwd=self.target_project_root,
            commands_dir=self.session.commands_dir,
            snapshot_name=snapshot_name,
            dry_run=self.options.dry_run,
        )

    def step_commit_and_push(self) -> StepOutcome:
        predicted_tag = predict_release_tag(self.session, self.run_source_command)
        self.sync_wechat_archive_for_release(predicted_tag)
        outcome = step_commit_and_push(
            session=self.session,
            run_project_command=self.run_source_command,
            run_or_preview=self.run_source_or_preview,
            repo_label="源码仓库",
            dry_run=self.options.dry_run,
        )
        if self.target_project_root == self.session.project_root:
            return outcome

        self.sync_distinct_target_repo()
        repo_label = "public 目标仓库" if self.release_scope() == "public" else "private 目标仓库"
        commit_message = (
            f"chore(release): sync public repo for {self.session.tag}"
            if self.release_scope() == "public"
            else f"chore(release): sync private repo for {self.session.tag}"
        )
        target_outcome = step_commit_and_push(
            session=self.session,
            run_project_command=self.run_target_command,
            run_or_preview=self.run_target_or_preview,
            repo_label=repo_label,
            commit_message=commit_message,
            dry_run=self.options.dry_run,
        )
        return StepOutcome(
            name="step1_commit_push",
            status="success" if "success" in {outcome.status, target_outcome.status} else "skipped",
            message=f"{outcome.message}；{target_outcome.message}",
            details={
                "source": outcome.details,
                "target": target_outcome.details,
            },
        )

    def step_create_tag(self) -> StepOutcome:
        return step_create_tag(
            session=self.session,
            options=self.options,
            run_target_command=self.run_target_command,
            run_target_or_preview=self.run_target_or_preview,
        )

    def step_create_release(self) -> StepOutcome:
        return step_create_release(
            session=self.session,
            options=self.options,
            run_target_command=self.run_target_command,
            run_target_or_preview=self.run_target_or_preview,
        )

    def step_pack_release(self) -> StepOutcome:
        return step_pack_release(
            session=self.session,
            options=self.options,
            run_source_command=self.run_source_command,
            target_repo=self.repo_context.name_with_owner if self.repo_context is not None else None,
            pack_script=self.pack_script,
        )

    def finish(self) -> int:
        summary = write_summary(
            session=self.session,
            step_outcomes=self.step_outcomes,
            release_url=self.release_url,
            failed_step=self.failed_step,
            failure_reason=self.failure_reason,
            publish_complete=is_publish_complete(
                options=self.options,
                step_outcomes=self.step_outcomes,
                failed_step=self.failed_step,
            ),
        )
        print("\n发布摘要：")
        print(summary)
        print_pack_summary(self.step_outcomes)
        return 0 if self.failed_step is None else 1
