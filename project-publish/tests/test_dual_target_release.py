from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_models import CommandResult, PublishOptions, PublishSession
from publish_steps import step_pack_release
from publish_support import (
    expected_target_repo_name,
    find_git_repo_root,
    infer_default_target_repo_root,
    validate_release_inputs,
)


class ValidateReleaseInputsTests(unittest.TestCase):
    def test_requires_public_scope_license(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "license"):
            validate_release_inputs(
                repo_visibility="public",
                release_scope="public",
                selected_license=None,
                pack_only=False,
            )

    def test_allows_private_scope_without_license(self) -> None:
        validate_release_inputs(
            repo_visibility="private",
            release_scope="private",
            selected_license=None,
            pack_only=False,
        )


class FindGitRepoRootTests(unittest.TestCase):
    def test_finds_plain_git_repo_without_project_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "public-release-repo"
            nested = repo / "nested" / "dir"
            nested.mkdir(parents=True)
            (repo / ".git").mkdir()

            self.assertEqual(find_git_repo_root(nested), repo)


class TargetRepoConventionTests(unittest.TestCase):
    def test_builds_expected_target_repo_names(self) -> None:
        self.assertEqual(expected_target_repo_name("zm-basic-skills", "public"), "zm-basic-skills")
        self.assertEqual(expected_target_repo_name("zm-basic-skills", "private"), "zm-basic-skills")

    def test_default_target_repo_root_stays_on_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "zm-basic-skills"
            source.mkdir()
            (source / ".git").mkdir()

            self.assertEqual(infer_default_target_repo_root(source, "zm-basic-skills", "private"), source)
            self.assertEqual(infer_default_target_repo_root(source, "zm-basic-skills", "public"), source)


class StepPackReleaseTests(unittest.TestCase):
    def test_passes_target_repo_to_flat_pack_release_script(self) -> None:
        session = PublishSession(
            project_root=Path("/tmp/source-project"),
            tag="v0.1.0",
            project_info={"name": "zm-basic-skills", "version": "0.1.0"},
            session_dir=Path("/tmp/source-project/.cache/project-publish/public/v0.1.0"),
            commands_dir=Path("/tmp/source-project/.cache/project-publish/public/v0.1.0/commands"),
        )
        options = PublishOptions(
            tag=None,
            repo_visibility="public",
            repo_path="/tmp/source-project",
            target_repo_path=None,
            release_scope="public",
            list_local_repos=False,
            selected_license="MIT",
            exclude_skills=(),
            dry_run=True,
            verbose=False,
            skip_upload=False,
            pack_only=False,
        )
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
            session=session,
            options=options,
            run_source_command=run_source_command,
            target_repo="mengzhu0308/zm-basic-skills",
            pack_script=Path("/tmp/source-project/project-publish/pack_release.py"),
        )

        self.assertEqual(outcome.status, "success")
        self.assertIn("--confirmed", captured)
        self.assertIn("--target-repo", captured)
        self.assertIn("mengzhu0308/zm-basic-skills", captured)
        self.assertNotIn("--release-scope", captured)


if __name__ == "__main__":
    unittest.main()
