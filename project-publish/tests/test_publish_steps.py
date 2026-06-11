from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_models import CommandResult, PublishOptions, PublishSession
from publish_steps import (
    bump_version_if_needed,
    ensure_main_branch,
    ensure_pack_script,
    step_commit_and_push,
    step_create_release,
    step_create_tag,
    step_pack_release,
)


class StepCommitAndPushTests(unittest.TestCase):
    def make_session(self) -> PublishSession:
        return PublishSession(
            project_root=Path("/tmp/project"),
            tag="v0.1.0",
            project_info={"name": "zm-basic-skills", "version": "0.1.0"},
            session_dir=Path("/tmp/project/.cache/project-publish/private/v0.1.0"),
            commands_dir=Path("/tmp/project/.cache/project-publish/private/v0.1.0/commands"),
        )

    def make_runner(self, mapping: dict[tuple[str, ...], CommandResult]):
        def run_project_command(args: list[str], snapshot_name: str) -> CommandResult:
            key = tuple(args)
            if key not in mapping:
                raise AssertionError(f"unexpected command: {key} ({snapshot_name})")
            return mapping[key]

        return run_project_command

    def make_preview_runner(self, recorded: list[tuple[str, ...]]):
        def run_or_preview(args: list[str], snapshot_name: str) -> CommandResult:
            del snapshot_name
            recorded.append(tuple(args))
            return CommandResult(0, "", "")

        return run_or_preview

    def test_ensure_main_branch_rejects_non_main_branch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "源码仓库当前分支必须为 `main`"):
            ensure_main_branch(
                self.make_runner(
                    {
                        ("git", "rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "feature/release\n", ""),
                    }
                ),
                repo_label="源码仓库",
            )

    def test_ensure_main_branch_rejects_detached_head(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "detached HEAD"):
            ensure_main_branch(
                self.make_runner(
                    {
                        ("git", "rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "HEAD\n", ""),
                        ("git", "symbolic-ref", "--short", "HEAD"): CommandResult(1, "", "fatal"),
                    }
                ),
                repo_label="源码仓库",
            )

    def test_skips_clean_branch_when_upstream_is_current(self) -> None:
        outcome = step_commit_and_push(
            session=self.make_session(),
            run_project_command=self.make_runner(
                {
                    ("git", "describe", "--tags", "--abbrev=0"): CommandResult(128, "", "no names found"),
                    ("git", "status", "--porcelain"): CommandResult(0, "", ""),
                    ("git", "rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "main\n", ""),
                    ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): CommandResult(0, "origin/main\n", ""),
                    ("git", "fetch", "origin"): CommandResult(0, "", ""),
                    ("git", "rev-list", "--left-right", "--count", "HEAD...@{u}"): CommandResult(0, "0\t0\n", ""),
                }
            ),
            run_or_preview=self.make_preview_runner([]),
        )
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.details["sync_action"], "noop")

    def test_fast_forwards_when_behind_upstream(self) -> None:
        recorded: list[tuple[str, ...]] = []
        outcome = step_commit_and_push(
            session=self.make_session(),
            run_project_command=self.make_runner(
                {
                    ("git", "describe", "--tags", "--abbrev=0"): CommandResult(128, "", "no names found"),
                    ("git", "status", "--porcelain"): CommandResult(0, "", ""),
                    ("git", "rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "main\n", ""),
                    ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): CommandResult(0, "origin/main\n", ""),
                    ("git", "fetch", "origin"): CommandResult(0, "", ""),
                    ("git", "rev-list", "--left-right", "--count", "HEAD...@{u}"): CommandResult(0, "0\t1\n", ""),
                }
            ),
            run_or_preview=self.make_preview_runner(recorded),
        )
        self.assertEqual(outcome.status, "success")
        self.assertEqual(recorded, [("git", "merge", "--ff-only", "@{u}")])

    def test_bump_version_if_needed_dry_run_does_not_modify_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_path = root / "VERSION.yaml"
            version_path.write_text(
                "project_info:\n  name: zm-basic-skills\n  version: 0.2.19\n",
                encoding="utf-8",
            )
            session = PublishSession(
                project_root=root,
                tag="v0.2.19",
                project_info={"name": "zm-basic-skills", "version": "0.2.19"},
                session_dir=root / ".cache" / "project-publish" / "private" / "v0.2.19",
                commands_dir=root / ".cache" / "project-publish" / "private" / "v0.2.19" / "commands",
            )
            recorded: list[tuple[str, ...]] = []

            bumped, new_tag = bump_version_if_needed(
                session=session,
                run_project_command=self.make_runner(
                    {
                        ("git", "describe", "--tags", "--abbrev=0"): CommandResult(0, "v0.2.19\n", ""),
                        ("git", "rev-parse", "v0.2.19"): CommandResult(0, "abc123\n", ""),
                        ("git", "rev-list", "--count", "abc123..HEAD"): CommandResult(0, "1\n", ""),
                    }
                ),
                run_or_preview=self.make_preview_runner(recorded),
                upstream="origin/main",
                branch="main",
                dry_run=True,
            )

            self.assertFalse(bumped)
            self.assertEqual(new_tag, "v0.2.20")
            self.assertEqual(
                version_path.read_text(encoding="utf-8"),
                "project_info:\n  name: zm-basic-skills\n  version: 0.2.19\n",
            )
            self.assertEqual(recorded, [])

    def test_bump_version_if_needed_skips_when_latest_tag_points_at_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_path = root / "VERSION.yaml"
            version_path.write_text(
                "project_info:\n  name: zm-basic-skills\n  version: 0.2.19\n",
                encoding="utf-8",
            )
            session = PublishSession(
                project_root=root,
                tag="v0.2.19",
                project_info={"name": "zm-basic-skills", "version": "0.2.19"},
                session_dir=root / ".cache" / "project-publish" / "private" / "v0.2.19",
                commands_dir=root / ".cache" / "project-publish" / "private" / "v0.2.19" / "commands",
            )
            recorded: list[tuple[str, ...]] = []

            bumped, new_tag = bump_version_if_needed(
                session=session,
                run_project_command=self.make_runner(
                    {
                        ("git", "describe", "--tags", "--abbrev=0"): CommandResult(0, "v0.2.19\n", ""),
                        ("git", "rev-parse", "v0.2.19"): CommandResult(0, "abc123\n", ""),
                        ("git", "rev-list", "--count", "abc123..HEAD"): CommandResult(0, "0\n", ""),
                    }
                ),
                run_or_preview=self.make_preview_runner(recorded),
                upstream="origin/main",
                branch="main",
            )

            self.assertFalse(bumped)
            self.assertIsNone(new_tag)
            self.assertEqual(
                version_path.read_text(encoding="utf-8"),
                "project_info:\n  name: zm-basic-skills\n  version: 0.2.19\n",
            )
            self.assertEqual(recorded, [])


class StepCreateTagAndPackTests(unittest.TestCase):
    def make_session(self) -> PublishSession:
        return PublishSession(
            project_root=Path("/tmp/project"),
            tag="v0.1.0",
            project_info={"name": "zm-basic-skills", "version": "0.1.0"},
            session_dir=Path("/tmp/project/.cache/project-publish/public/v0.1.0"),
            commands_dir=Path("/tmp/project/.cache/project-publish/public/v0.1.0/commands"),
        )

    def make_options(self, repo_visibility: str = "public") -> PublishOptions:
        return PublishOptions(
            tag="v0.1.0",
            repo_visibility=repo_visibility,
            repo_path="/tmp/project",
            target_repo_path=None,
            release_scope=repo_visibility,
            list_local_repos=False,
            selected_license="MIT" if repo_visibility == "public" else None,
            exclude_skills=(),
            dry_run=True,
            verbose=False,
            skip_upload=False,
            pack_only=False,
        )

    def test_ensure_pack_script_uses_flat_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project-publish").mkdir()
            (root / "project-publish" / "pack_release.py").write_text("print('ok')\n", encoding="utf-8")
            self.assertEqual(
                ensure_pack_script(root, "public"),
                root / "project-publish" / "pack_release.py",
            )

    def test_create_new_tag_pushes_origin(self) -> None:
        recorded: list[tuple[str, ...]] = []

        def run_target_command(args: list[str], snapshot_name: str) -> CommandResult:
            mapping = {
                ("git", "rev-parse", "-q", "--verify", "refs/tags/v0.1.0"): CommandResult(1, "", ""),
            }
            key = tuple(args)
            if key not in mapping:
                raise AssertionError(f"unexpected command: {key} ({snapshot_name})")
            return mapping[key]

        def run_target_or_preview(args: list[str], snapshot_name: str) -> CommandResult:
            del snapshot_name
            recorded.append(tuple(args))
            return CommandResult(0, "", "")

        outcome = step_create_tag(
            session=self.make_session(),
            options=self.make_options(),
            run_target_command=run_target_command,
            run_target_or_preview=run_target_or_preview,
        )
        self.assertEqual(outcome.status, "success")
        self.assertEqual(recorded, [("git", "tag", "v0.1.0"), ("git", "push", "origin", "v0.1.0")])

    def test_pack_step_passes_repo_visibility_and_target_repo(self) -> None:
        captured: list[str] = []

        def run_source_command(args: list[str], snapshot_name: str) -> CommandResult:
            del snapshot_name
            captured[:] = args
            return CommandResult(
                0,
                '{"package_path":"/tmp/pkg.zip","package_status":"planned","uploaded":false,"release_url":"","error":null}',
                "",
            )

        outcome = step_pack_release(
            session=self.make_session(),
            options=self.make_options(),
            run_source_command=run_source_command,
            target_repo="mengzhu0308/zm-basic-skills",
            pack_script=Path("/tmp/project/project-publish/pack_release.py"),
        )

        self.assertEqual(outcome.status, "success")
        self.assertIn("--repo-visibility", captured)
        self.assertIn("public", captured)
        self.assertIn("--target-repo", captured)
        self.assertIn("--license", captured)
        self.assertIn("MIT", captured)
        self.assertNotIn("--exclude-skill", captured)

    def test_create_release_returns_url_and_notes(self) -> None:
        def run_target_command(args: list[str], snapshot_name: str) -> CommandResult:
            mapping = {
                ("gh", "release", "view", "v0.1.0", "--json", "url", "-q", ".url"): CommandResult(1, "", "not found"),
                ("gh", "release", "view", "v0.1.0", "--json", "url,body"): CommandResult(
                    0,
                    '{"url":"https://github.com/mengzhu0308/zm-basic-skills/releases/tag/v0.1.0","body":"- Add release note fallback\\n- Remove owner/name from header"}',
                    "",
                ),
            }
            key = tuple(args)
            if key not in mapping:
                raise AssertionError(f"unexpected command: {key} ({snapshot_name})")
            return mapping[key]

        def run_target_or_preview(args: list[str], snapshot_name: str) -> CommandResult:
            del snapshot_name
            self.assertEqual(args, ["gh", "release", "create", "v0.1.0", "--title", "v0.1.0", "--generate-notes"])
            return CommandResult(0, "", "")

        options = self.make_options()
        options = PublishOptions(
            tag=options.tag,
            repo_visibility=options.repo_visibility,
            repo_path=options.repo_path,
            target_repo_path=options.target_repo_path,
            release_scope=options.release_scope,
            list_local_repos=options.list_local_repos,
            selected_license=options.selected_license,
            exclude_skills=options.exclude_skills,
            dry_run=False,
            verbose=options.verbose,
            skip_upload=options.skip_upload,
            pack_only=options.pack_only,
        )

        outcome = step_create_release(
            session=self.make_session(),
            options=options,
            run_target_command=run_target_command,
            run_target_or_preview=run_target_or_preview,
        )

        self.assertEqual(outcome.status, "success")
        self.assertEqual(
            outcome.details["release_url"],
            "https://github.com/mengzhu0308/zm-basic-skills/releases/tag/v0.1.0",
        )
        self.assertIn("Add release note fallback", outcome.details["release_notes"])


if __name__ == "__main__":
    unittest.main()
