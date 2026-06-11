from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pack_release_app import ReleasePackager, build_package_name, resolve_release_scope
from pack_release_collect import gather_public_repo_seed_entries, gather_release_entries
from pack_release_models import CommandResult, PackOptions
from pack_release_support import read_public_release_skills


def create_project_root(root: Path) -> None:
    (root / "README.md").write_text("root readme\n", encoding="utf-8")
    (root / "VERSION.yaml").write_text(
        "project_info:\n  name: demo-project\n  version: 0.1.0\n",
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
        "  - .claude\n"
        "  - .codex\n"
        "  - .git\n"
        "exclude_globs:\n"
        "  - \"*.pyc\"\n",
        encoding="utf-8",
    )
    (root / "project-public-package").mkdir()
    (root / "project-public-package" / "release.yaml").write_text(
        "exclude_skills:\n"
        "  - drop-skill\n",
        encoding="utf-8",
    )
    skills = root / "skills"
    keep = skills / "keep-skill"
    drop = skills / "drop-skill"
    (keep / "scripts").mkdir(parents=True)
    (drop / "scripts").mkdir(parents=True)
    (keep / "SKILL.md").write_text("keep\n", encoding="utf-8")
    (keep / "VERSION.yaml").write_text("version: 1\n", encoding="utf-8")
    (keep / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (drop / "SKILL.md").write_text("drop\n", encoding="utf-8")
    (drop / "VERSION.yaml").write_text("version: 1\n", encoding="utf-8")
    (drop / "scripts" / "run.py").write_text("print(2)\n", encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache" / "temp.txt").write_text("ignore\n", encoding="utf-8")


class ReleaseScopeTests(unittest.TestCase):
    def test_pack_scope_defaults_to_private(self) -> None:
        options = PackOptions(
            tag="v0.1.0",
            upload=False,
            dry_run=True,
            verbose=False,
            release_scope=None,
            selected_license=None,
            target_repo=None,
            exclude_skills=(),
            confirmed=False,
        )
        self.assertEqual(resolve_release_scope(options), "private")

    def test_public_scope_preserves_license_value(self) -> None:
        options = PackOptions(
            tag="v0.1.0",
            upload=False,
            dry_run=True,
            verbose=False,
            release_scope="public",
            selected_license="MIT",
            target_repo=None,
            exclude_skills=(),
            confirmed=False,
        )
        self.assertEqual(resolve_release_scope(options), "public")

    def test_package_names_keep_public_default_and_private_suffix(self) -> None:
        self.assertEqual(
            build_package_name("demo-project", "v0.1.0", "private"),
            "demo-project-private_v0.1.0_installable.zip",
        )
        self.assertEqual(
            build_package_name("demo-project", "v0.1.0", "public"),
            "demo-project_v0.1.0_installable.zip",
        )

    def test_pack_release_run_rejects_non_main_branch_before_collecting_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            (root / ".git").mkdir()
            options = PackOptions(
                tag="v0.1.0",
                upload=False,
                dry_run=True,
                verbose=False,
                release_scope="private",
                selected_license=None,
                target_repo=None,
                exclude_skills=(),
                confirmed=False,
            )

            def fake_run_command(args: list[str], *, cwd: Path) -> CommandResult:
                del cwd
                mapping = {
                    ("git", "rev-parse", "--is-inside-work-tree"): CommandResult(0, "true", ""),
                    ("git", "rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "feature/release\n", ""),
                }
                key = tuple(args)
                if key not in mapping:
                    raise AssertionError(f"unexpected command: {key}")
                return mapping[key]

            with (
                unittest.mock.patch("pack_release_app.find_project_root", return_value=root),
                unittest.mock.patch("pack_release_app.read_project_info", return_value={"name": "demo-project", "version": "0.1.0"}),
                unittest.mock.patch("pack_release_app.run_command", side_effect=fake_run_command),
                unittest.mock.patch("pack_release_app.gather_release_entries") as gather_entries,
            ):
                packager = ReleasePackager(options)
                exit_code = packager.run()

            self.assertEqual(exit_code, 1)
            gather_entries.assert_not_called()


class ReleaseEntriesTests(unittest.TestCase):
    def test_public_release_now_packages_full_repo_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
            (root / ".codex").mkdir()
            (root / ".codex" / "config.toml").write_text("root=true\n", encoding="utf-8")
            (root / ".github").mkdir()
            (root / ".github" / "workflows.yml").write_text("name: ci\n", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / ".codex").mkdir()
            (root / "nested" / ".codex" / "config.toml").write_text("x=1\n", encoding="utf-8")

            entries, _warnings = gather_release_entries(root, release_scope="public")
            archive_names = {archive_name.as_posix() for _, archive_name in entries}

            self.assertIn("README.md", archive_names)
            self.assertIn("project-publish/main.py", archive_names)
            self.assertIn("project-public-package/release.yaml", archive_names)
            self.assertIn("skills/drop-skill/SKILL.md", archive_names)
            self.assertIn(".github/workflows.yml", archive_names)
            self.assertIn("nested/.codex/config.toml", archive_names)
            self.assertNotIn(".cache/temp.txt", archive_names)
            self.assertNotIn(".claude/settings.json", archive_names)
            self.assertNotIn(".codex/config.toml", archive_names)

    def test_public_package_seed_keeps_old_public_subset_plus_project_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)

            resolved = read_public_release_skills(root)
            self.assertEqual(resolved, ["keep-skill"])

            entries = gather_public_repo_seed_entries(root)
            archive_names = {archive_name.as_posix() for _, archive_name in entries}

            self.assertIn("README.md", archive_names)
            self.assertIn("project-install/main.py", archive_names)
            self.assertIn("project-uninstall/main.py", archive_names)
            self.assertIn("skills/keep-skill/SKILL.md", archive_names)
            self.assertIn("skills/keep-skill/scripts/run.py", archive_names)
            self.assertIn("project-publish/main.py", archive_names)
            self.assertIn("project-publish/pack_release.py", archive_names)
            self.assertNotIn("skills/drop-skill/SKILL.md", archive_names)


if __name__ == "__main__":
    unittest.main()
