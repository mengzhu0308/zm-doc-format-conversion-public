from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_app import ProjectPublishApp
from publish_models import GitHubRepoContext, PublishOptions, StepOutcome
from publish_reporting import build_wechat_post_for_project, build_wechat_post, write_wechat_archive


def create_project_root(root: Path) -> None:
    (root / "skills").mkdir()
    (root / "README.md").write_text("root readme\n", encoding="utf-8")
    (root / "VERSION.yaml").write_text(
        "project_info:\n"
        "  name: zm-basic-skills\n"
        "  version: 0.1.0\n"
        "  description: 面向 Codex CLI/Claude Code 的基础 Agent Skill 集合\n",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Changed\n\n"
        "- 重定义 `project-publish/` 的发布边界：私有发布和公开发布都允许语义触发，且都改为整仓快照发布；两者只在目标 GitHub 仓库可见性上区分为 `private / public`\n"
        "- 调整 `project-publish` 的第 5 步：`private` 与 `public` 发布现在都会生成微信动态方案，并统一追加归档到 `project-publish/WeChat.md`\n\n"
        "### Fixed\n\n"
        "- 修复 `project-publish --dry-run` 仍会改写 `VERSION.yaml` 与 `project-publish/WeChat.md` 的问题：现在 dry-run 只推导下一版 tag 并打印微信文案，不再写入工作树\n",
        encoding="utf-8",
    )
    (root / "project-install").mkdir()
    (root / "project-install" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (root / "project-uninstall").mkdir()
    (root / "project-uninstall" / "main.py").write_text("print(2)\n", encoding="utf-8")
    (root / "project-publish").mkdir()
    (root / "project-publish" / "main.py").write_text("print('publish')\n", encoding="utf-8")
    (root / "project-publish" / "pack_release.py").write_text("print('pack')\n", encoding="utf-8")
    (root / "project-publish" / "release.yaml").write_text(
        "exclude_paths:\n"
        "  - .cache\n"
        "  - .git\n"
        "exclude_globs:\n"
        "  - \"*.pyc\"\n",
        encoding="utf-8",
    )


def make_options(
    root: Path,
    repo_visibility: str,
    *,
    target_repo_path: str | None = None,
    dry_run: bool = True,
    pack_only: bool = False,
) -> PublishOptions:
    return PublishOptions(
        tag="v0.1.1",
        repo_visibility=repo_visibility,
        repo_path=str(root),
        target_repo_path=target_repo_path,
        release_scope=repo_visibility,
        list_local_repos=False,
        selected_license="MIT" if repo_visibility == "public" else None,
        exclude_skills=(),
        dry_run=dry_run,
        verbose=False,
        skip_upload=False,
        pack_only=pack_only,
    )


class PreflightBranchPolicyTests(unittest.TestCase):
    def test_preflight_prompts_for_public_license_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(
                PublishOptions(
                    tag="v0.1.1",
                    repo_visibility="public",
                    repo_path=str(root),
                    target_repo_path=None,
                    release_scope="public",
                    list_local_repos=False,
                    selected_license=None,
                    exclude_skills=(),
                    dry_run=True,
                    verbose=False,
                    skip_upload=False,
                    pack_only=True,
                )
            )

            with (
                patch("builtins.input", return_value="2"),
                patch("publish_app.ensure_command"),
                patch("publish_app.ensure_git_repo"),
                patch("publish_app.ensure_main_branch"),
            ):
                app.preflight()

            self.assertEqual(app.options.selected_license, "Apache-2.0")

    def test_run_stops_before_preview_when_source_branch_is_not_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", dry_run=True))

            with (
                patch("publish_app.ensure_command"),
                patch("publish_app.ensure_git_repo"),
                patch("publish_app.ensure_main_branch", side_effect=RuntimeError("源码仓库当前分支必须为 `main`。")),
                patch.object(ProjectPublishApp, "preview_release") as preview_release,
            ):
                exit_code = app.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(app.failed_step, "preflight")
            preview_release.assert_not_called()

    def test_pack_only_still_rejects_non_main_source_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", pack_only=True))

            with (
                patch("publish_app.ensure_command"),
                patch("publish_app.ensure_git_repo"),
                patch("publish_app.ensure_main_branch", side_effect=RuntimeError("源码仓库当前分支必须为 `main`。")),
                patch.object(ProjectPublishApp, "preview_release") as preview_release,
            ):
                exit_code = app.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(app.failed_step, "preflight")
            preview_release.assert_not_called()

    def test_preflight_rejects_non_main_explicit_target_repo_before_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "source"
            source_root.mkdir()
            create_project_root(source_root)
            target_root = Path(tmp) / "private-target"
            target_root.mkdir()
            (target_root / ".git").mkdir()
            app = ProjectPublishApp(
                make_options(source_root, "private", dry_run=True, target_repo_path=str(target_root))
            )

            with (
                patch("publish_app.ensure_command"),
                patch("publish_app.ensure_git_repo"),
                patch("publish_app.ensure_gh_auth"),
                patch(
                    "publish_app.ensure_main_branch",
                    side_effect=["main", RuntimeError("private 目标仓库当前分支必须为 `main`。")],
                ),
                patch("publish_app.ensure_named_publish_repo_context") as repo_context,
                patch("publish_app.ensure_private_target_git_repo") as sync_target,
                patch.object(ProjectPublishApp, "preview_release") as preview_release,
            ):
                exit_code = app.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(app.failed_step, "preflight")
            repo_context.assert_not_called()
            sync_target.assert_not_called()
            preview_release.assert_not_called()


class WechatStepTests(unittest.TestCase):
    def test_build_wechat_post_prefers_version_section_highlights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n"
                "## [Unreleased]\n\n"
                "### Changed\n\n"
                "- 同步更新 `AGENTS.md` 与 `CLAUDE.md`\n\n"
                "## [0.1.1] - 2026-04-18\n\n"
                "### Changed\n\n"
                "- 调整 `project-publish` 的第 5 步：`private` 与 `public` 发布现在都会生成微信动态方案，并统一追加归档到 `project-publish/WeChat.md`\n"
                "- 调整 `project-publish/WeChat.md` 的归档顺序：新版本文案现在会插入到文件头部之后，保持“最新版本在前”的倒序记录\n\n"
                "### Fixed\n\n"
                "- 修复 `project-publish --dry-run` 仍会改写 `VERSION.yaml` 与 `project-publish/WeChat.md` 的问题：现在 dry-run 只推导下一版 tag 并打印微信文案，不再写入工作树\n",
                encoding="utf-8",
            )

            post = build_wechat_post_for_project(
                project_root=root,
                project_name="zm-basic-skills",
                project_description="面向 Codex CLI/Claude Code 的基础 Agent Skill 集合",
                tag="v0.1.1",
                release_url="https://example.com/v0.1.1",
            )

            self.assertIn("微信动态", post)
            self.assertIn("dry-run 只预演版本与文案", post)
            self.assertNotIn("AGENTS.md", post)
            self.assertLessEqual(len(post), 180)

    def test_build_wechat_post_falls_back_to_unreleased_highlights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)

            post = build_wechat_post_for_project(
                project_root=root,
                project_name="zm-basic-skills",
                project_description="面向 Codex CLI/Claude Code 的基础 Agent Skill 集合",
                tag="v0.1.1",
                release_url="https://example.com/v0.1.1",
            )

            self.assertIn("私有/公开发布统一收口到同一流程", post)
            self.assertIn("微信动态", post)
            self.assertLessEqual(len(post), 180)

    def test_build_wechat_post_falls_back_to_generic_copy_without_changelog(self) -> None:
        post = build_wechat_post(
            "zm-basic-skills",
            "v0.1.1",
            "https://example.com/v0.1.1",
            project_description="面向 Codex CLI/Claude Code 的基础 Agent Skill 集合",
        )

        self.assertIn("面向 Codex CLI/Claude Code 的基础 Agent Skill 集合", post)
        self.assertIn("Release URL：https://example.com/v0.1.1。", post)
        self.assertLessEqual(len(post), 180)

    def test_build_wechat_post_falls_back_to_release_notes_without_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            (root / "CHANGELOG.md").unlink()

            post = build_wechat_post_for_project(
                project_root=root,
                project_name="zm-basic-skills",
                project_description="面向 Codex CLI/Claude Code 的基础 Agent Skill 集合",
                tag="v0.1.1",
                release_url="https://example.com/v0.1.1",
                release_notes=(
                    "## What's Changed\n\n"
                    "- Add release note fallback for WeChat copy (#123)\n"
                    "- Remove owner/name from normal publish header (#124)\n\n"
                    "**Full Changelog**: https://example.com/compare\n"
                ),
            )

            self.assertIn("release note fallback for WeChat copy", post)
            self.assertIn("Remove owner/name from normal publish header", post)
            self.assertNotIn("Full Changelog", post)

    def test_build_wechat_post_falls_back_to_manifest_without_changelog_or_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            (root / "CHANGELOG.md").unlink()

            post = build_wechat_post_for_project(
                project_root=root,
                project_name="zm-basic-skills",
                project_description="面向 Codex CLI/Claude Code 的基础 Agent Skill 集合",
                tag="v0.1.1",
                release_url="https://example.com/v0.1.1",
                release_manifest=[
                    "README.md",
                    "project-install/main.py",
                    "project-publish/main.py",
                    "skills/demo/SKILL.md",
                ],
            )

            self.assertIn("发布包共整理 4 项资源", post)
            self.assertIn("项目级发布与安装脚本会随版本一并交付", post)

    def test_wechat_archive_keeps_latest_tag_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)

            write_wechat_archive(
                project_root=root,
                tag="v0.1.0",
                release_url="https://example.com/v0.1.0",
                content="older",
            )
            write_wechat_archive(
                project_root=root,
                tag="v0.1.1",
                release_url="https://example.com/v0.1.1",
                content="newer",
            )

            content = (root / "project-publish" / "WeChat.md").read_text(encoding="utf-8")
            self.assertLess(content.index("## v0.1.1"), content.index("## v0.1.0"))

    def test_private_release_generates_wechat_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", dry_run=False))

            stream = io.StringIO()
            with redirect_stdout(stream):
                app.run_wechat_step()

            self.assertEqual(app.step_outcomes[-1].status, "success")
            wechat_path = root / "project-publish" / "WeChat.md"
            self.assertTrue(wechat_path.exists())
            self.assertIn("## v0.1.1", wechat_path.read_text(encoding="utf-8"))
            self.assertIn("微信动态文案", stream.getvalue())

    def test_dry_run_wechat_step_does_not_write_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", dry_run=True))

            stream = io.StringIO()
            with redirect_stdout(stream):
                app.run_wechat_step()

            self.assertFalse((root / "project-publish" / "WeChat.md").exists())
            self.assertIn("微信动态文案", stream.getvalue())

    def test_public_release_generates_wechat_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "public", dry_run=False))

            stream = io.StringIO()
            with redirect_stdout(stream):
                app.run_wechat_step()

            self.assertEqual(app.step_outcomes[-1].status, "success")
            wechat_path = root / "project-publish" / "WeChat.md"
            self.assertTrue(wechat_path.exists())
            self.assertIn("## v0.1.1", wechat_path.read_text(encoding="utf-8"))
            self.assertIn("微信动态文案", stream.getvalue())

    def test_run_wechat_step_is_idempotent_when_archive_already_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", dry_run=False))
            app.repo_context = GitHubRepoContext(
                viewer_login="alice",
                owner_login="alice",
                name_with_owner="alice/zm-basic-skills",
                visibility="private",
                remote_url="https://github.com/alice/zm-basic-skills.git",
            )
            app.release_url = "https://github.com/alice/zm-basic-skills/releases/tag/v0.1.1"

            first_clock = Mock()
            first_clock.now.return_value.strftime.return_value = "2026-04-17 21:00:00"
            with patch("publish_reporting.datetime", first_clock):
                write_wechat_archive(
                    project_root=root,
                    tag="v0.1.1",
                    release_url=app.release_url,
                    content=build_wechat_post_for_project(
                        project_root=root,
                        project_name=app.expected_target_project_name(),
                        project_description=app.session.project_info.get("description", ""),
                        tag="v0.1.1",
                        release_url=app.release_url,
                    ),
                )

            before = (root / "project-publish" / "WeChat.md").read_text(encoding="utf-8")
            second_clock = Mock()
            second_clock.now.return_value.strftime.return_value = "2026-04-17 21:00:59"
            with (
                patch("publish_reporting.datetime", second_clock),
                redirect_stdout(io.StringIO()),
            ):
                app.run_wechat_step()

            after = (root / "project-publish" / "WeChat.md").read_text(encoding="utf-8")
            self.assertEqual(after, before)

    def test_public_and_private_same_tag_use_different_session_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            public_app = ProjectPublishApp(make_options(root, "public"))
            private_app = ProjectPublishApp(make_options(root, "private"))

            self.assertNotEqual(public_app.session.session_dir, private_app.session.session_dir)
            self.assertEqual(public_app.session.session_dir, root / ".cache" / "project-publish" / "public" / "v0.1.1")
            self.assertEqual(private_app.session.session_dir, root / ".cache" / "project-publish" / "private" / "v0.1.1")

    def test_public_default_target_repo_uses_session_temp_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)

            app = ProjectPublishApp(make_options(root, "public"))

            self.assertEqual(app.target_project_root, app.session.session_dir / "target-repo")

    def test_print_header_hides_target_repo_name_with_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", dry_run=False))
            app.repo_context = GitHubRepoContext(
                viewer_login="alice",
                owner_login="alice",
                name_with_owner="alice/zm-basic-skills",
                visibility="private",
                remote_url="https://github.com/alice/zm-basic-skills.git",
            )

            stream = io.StringIO()
            with redirect_stdout(stream):
                app.print_header()

            output = stream.getvalue()
            self.assertIn("目标仓库可见性（已确认）：private", output)
            self.assertNotIn("目标 GitHub 仓库：", output)
            self.assertNotIn("alice/zm-basic-skills", output)

    def test_print_header_includes_confirmed_public_license(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "public", dry_run=False))
            app.repo_context = GitHubRepoContext(
                viewer_login="alice",
                owner_login="alice",
                name_with_owner="alice/zm-basic-skills",
                visibility="public",
                remote_url="https://github.com/alice/zm-basic-skills.git",
            )

            stream = io.StringIO()
            with redirect_stdout(stream):
                app.print_header()

            output = stream.getvalue()
            self.assertIn("目标仓库可见性（已确认）：public", output)
            self.assertIn("发布 license（已确认）：MIT", output)


class CommitAndPushOrderingTests(unittest.TestCase):
    def test_step_commit_and_push_prewrites_wechat_archive_before_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(make_options(root, "private", dry_run=False))
            app.repo_context = GitHubRepoContext(
                viewer_login="alice",
                owner_login="alice",
                name_with_owner="alice/zm-basic-skills",
                visibility="private",
                remote_url="https://github.com/alice/zm-basic-skills.git",
            )

            observed_archive_contents: list[str] = []

            def fake_step_commit_and_push(**_kwargs) -> StepOutcome:
                observed_archive_contents.append(
                    (root / "project-publish" / "WeChat.md").read_text(encoding="utf-8")
                )
                return StepOutcome(
                    name="step1_commit_push",
                    status="success",
                    message="源码仓库 已提交",
                    details={},
                )

            with (
                patch("publish_app.predict_release_tag", return_value="v0.1.1"),
                patch("publish_app.step_commit_and_push", side_effect=fake_step_commit_and_push),
            ):
                outcome = app.step_commit_and_push()

            self.assertEqual(outcome.status, "success")
            self.assertEqual(len(observed_archive_contents), 1)
            self.assertIn("## v0.1.1", observed_archive_contents[0])
            self.assertIn(
                "https://github.com/alice/zm-basic-skills/releases/tag/v0.1.1",
                observed_archive_contents[0],
            )

    def test_public_release_resyncs_target_repo_before_target_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "source"
            source_root.mkdir()
            create_project_root(source_root)
            target_root = Path(tmp) / "public-target"
            app = ProjectPublishApp(make_options(source_root, "public", target_repo_path=str(target_root)))
            events: list[str] = []

            def fake_step_commit_and_push(*, repo_label: str, **_kwargs) -> StepOutcome:
                events.append(f"commit:{repo_label}")
                return StepOutcome(
                    name="step1_commit_push",
                    status="success",
                    message=f"{repo_label} 已提交",
                    details={"repo_label": repo_label},
                )

            def fake_sync_public_target_repo(**_kwargs) -> str:
                events.append("sync:public-target")
                return "https://github.com/example/public-target.git"

            with (
                patch("publish_app.step_commit_and_push", side_effect=fake_step_commit_and_push),
                patch("publish_app.ensure_public_target_git_repo", side_effect=fake_sync_public_target_repo),
            ):
                outcome = app.step_commit_and_push()

            self.assertEqual(outcome.status, "success")
            self.assertEqual(
                events,
                ["commit:源码仓库", "sync:public-target", "commit:public 目标仓库"],
            )

    def test_private_release_resyncs_target_repo_before_target_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "source"
            source_root.mkdir()
            create_project_root(source_root)
            target_root = Path(tmp) / "private-target"
            target_root.mkdir()
            (target_root / ".git").mkdir()
            app = ProjectPublishApp(make_options(source_root, "private", target_repo_path=str(target_root)))
            events: list[str] = []

            def fake_step_commit_and_push(*, repo_label: str, **_kwargs) -> StepOutcome:
                events.append(f"commit:{repo_label}")
                return StepOutcome(
                    name="step1_commit_push",
                    status="success",
                    message=f"{repo_label} 已提交",
                    details={"repo_label": repo_label},
                )

            def fake_sync_private_target_repo(**_kwargs) -> None:
                events.append("sync:private-target")

            with (
                patch("publish_app.step_commit_and_push", side_effect=fake_step_commit_and_push),
                patch("publish_app.ensure_private_target_git_repo", side_effect=fake_sync_private_target_repo),
            ):
                outcome = app.step_commit_and_push()

            self.assertEqual(outcome.status, "success")
            self.assertEqual(
                events,
                ["commit:源码仓库", "sync:private-target", "commit:private 目标仓库"],
            )


if __name__ == "__main__":
    unittest.main()
