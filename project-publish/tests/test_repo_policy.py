from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_models import CommandResult, GitHubRepoContext
from publish_support import (
    ensure_named_publish_repo_context,
    ensure_private_target_git_repo,
    ensure_public_publish_project_root,
    ensure_public_target_git_repo,
    ensure_target_repo_name_matches_convention,
    parse_github_remote_url,
    print_public_publish_warning,
)


class ParseGitHubRemoteUrlTests(unittest.TestCase):
    def test_parse_https_remote(self) -> None:
        self.assertEqual(
            parse_github_remote_url("https://github.com/mengzhu0308/temp.git"),
            ("mengzhu0308", "temp"),
        )

    def test_parse_ssh_remote(self) -> None:
        self.assertEqual(
            parse_github_remote_url("git@github.com:mengzhu0308/temp.git"),
            ("mengzhu0308", "temp"),
        )


class EnsurePublishRepoContextTests(unittest.TestCase):
    def make_runner(self, mapping: dict[tuple[str, ...], CommandResult]):
        def run_project_command(args: list[str], snapshot_name: str) -> CommandResult:
            key = tuple(args)
            if key not in mapping:
                raise AssertionError(f"unexpected command: {key} ({snapshot_name})")
            return mapping[key]

        return run_project_command

    def test_creates_missing_public_repo_when_enabled(self) -> None:
        create_calls: list[tuple[str, ...]] = []
        repo_created = False

        def runner(args: list[str], snapshot_name: str) -> CommandResult:
            del snapshot_name
            nonlocal repo_created
            key = tuple(args)
            if key == ("gh", "api", "user", "--jq", ".login"):
                return CommandResult(0, "mengzhu0308", "")
            if key == (
                "gh",
                "repo",
                "view",
                "mengzhu0308/public-release",
                "--json",
                "nameWithOwner,visibility,owner",
            ):
                if not repo_created:
                    return CommandResult(1, "", "GraphQL: Could not resolve to a Repository with the name 'mengzhu0308/public-release'.")
                return CommandResult(
                    0,
                    '{"nameWithOwner":"mengzhu0308/public-release","visibility":"PUBLIC","owner":{"login":"mengzhu0308"}}',
                    "",
                )
            if key == ("gh", "repo", "create", "mengzhu0308/public-release", "--public", "--confirm"):
                create_calls.append(key)
                repo_created = True
                return CommandResult(0, "created", "")
            raise AssertionError(f"unexpected command: {key}")

        context = ensure_named_publish_repo_context(
            "public",
            "mengzhu0308/public-release",
            runner,
            create_if_missing=True,
            dry_run=False,
        )

        self.assertEqual(context.name_with_owner, "mengzhu0308/public-release")
        self.assertEqual(context.visibility, "public")
        self.assertEqual(create_calls, [("gh", "repo", "create", "mengzhu0308/public-release", "--public", "--confirm")])


class TargetRepoNameConventionTests(unittest.TestCase):
    def test_allows_matching_public_repo_name(self) -> None:
        ensure_target_repo_name_matches_convention(
            "zm-basic-skills",
            "public",
            GitHubRepoContext(
                viewer_login="mengzhu0308",
                owner_login="mengzhu0308",
                name_with_owner="mengzhu0308/zm-basic-skills",
                visibility="public",
                remote_url="https://github.com/mengzhu0308/zm-basic-skills.git",
            ),
        )


class ProjectRootValidationTests(unittest.TestCase):
    def create_public_repo_root(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "skills").mkdir()
        (root / "project-install").mkdir()
        (root / "project-publish").mkdir()
        (root / "VERSION.yaml").write_text("project_info:\n  name: demo\n  version: 0.1.0\n", encoding="utf-8")
        return root

    def test_accepts_new_public_publish_project_root(self) -> None:
        root = self.create_public_repo_root()
        ensure_public_publish_project_root(root, require_git=False)

    def test_requires_git_when_requested(self) -> None:
        root = self.create_public_repo_root()
        with self.assertRaisesRegex(RuntimeError, "Git 工作树"):
            ensure_public_publish_project_root(root, require_git=True)


class PublishWarningTests(unittest.TestCase):
    def test_warning_mentions_full_repo_exposure(self) -> None:
        stderr_stream = io.StringIO()
        with redirect_stderr(stderr_stream):
            print_public_publish_warning("正式公开发布")
        self.assertIn("整个仓库内容", stderr_stream.getvalue())


class TargetRepoSyncTests(unittest.TestCase):
    def create_source_root(self, root: Path) -> None:
        (root / "README.md").write_text("root readme\n", encoding="utf-8")
        (root / "VERSION.yaml").write_text("project_info:\n  name: demo\n  version: 0.1.0\n", encoding="utf-8")
        (root / "skills").mkdir()
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

    def test_public_target_repo_sync_initializes_git_and_copies_full_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            self.create_source_root(root)
            target_root = Path(tmp) / "public-release"
            calls: list[tuple[Path, tuple[str, ...]]] = []

            def fake_run_command(args: list[str], *, cwd: Path, commands_dir: Path, snapshot_name: str) -> CommandResult:
                del commands_dir, snapshot_name
                key = tuple(args)
                calls.append((cwd, key))
                if cwd == root and key == ("gh", "api", "user", "--jq", ".login"):
                    return CommandResult(0, "alice", "")
                if cwd == target_root and key == ("git", "remote", "get-url", "origin"):
                    return CommandResult(1, "", "missing")
                if cwd == target_root and key == ("git", "init", "-b", "main"):
                    return CommandResult(0, "", "")
                if cwd == target_root and key == ("git", "remote", "add", "origin", "https://github.com/alice/public-release.git"):
                    return CommandResult(0, "", "")
                raise AssertionError(f"unexpected command: cwd={cwd} args={key}")

            from unittest.mock import patch

            with patch("publish_support.run_command", side_effect=fake_run_command):
                remote_url = ensure_public_target_git_repo(
                    source_project_root=root,
                    target_project_root=target_root,
                    commands_dir=root / ".cache" / "commands",
                    expected_repo_name="public-release",
                    release_scope="public",
                    verbose=False,
                )

            self.assertEqual(remote_url, "https://github.com/alice/public-release.git")
            self.assertTrue((target_root / "README.md").is_file())
            self.assertTrue((target_root / "project-publish" / "main.py").is_file())

    def test_private_target_repo_sync_copies_full_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            self.create_source_root(root)
            target_root = Path(tmp) / "private-target"
            (target_root / ".git").mkdir(parents=True)

            ensure_private_target_git_repo(
                source_project_root=root,
                target_project_root=target_root,
                verbose=False,
            )

            self.assertTrue((target_root / "README.md").is_file())
            self.assertTrue((target_root / "project-publish" / "pack_release.py").is_file())


if __name__ == "__main__":
    unittest.main()
