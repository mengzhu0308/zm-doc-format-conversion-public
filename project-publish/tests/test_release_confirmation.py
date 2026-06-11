from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_app import ProjectPublishApp
from publish_models import PublishOptions
from release_confirmation import build_manifest_tree_lines, build_release_manifest, print_release_manifest


def create_project_root(root: Path) -> None:
    (root / "skills").mkdir()
    (root / "README.md").write_text("root readme\n", encoding="utf-8")
    (root / "VERSION.yaml").write_text(
        "project_info:\n  name: zm-basic-skills\n  version: 0.1.0\n",
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


class ReleaseManifestTests(unittest.TestCase):
    def test_build_manifest_tree_lines_renders_nested_structure(self) -> None:
        lines = build_manifest_tree_lines(
            ["README.md", "project-install/main.py", "project-install/tests/test_install_flow.py"]
        )
        self.assertEqual(".", lines[0])
        self.assertIn("README.md", "\n".join(lines))
        self.assertIn("project-install/", "\n".join(lines))

    def test_build_release_manifest_for_private_scope_uses_flat_pack_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)

            manifest = build_release_manifest(root, release_scope="private", selected_license=None)

            self.assertIn("README.md", manifest)
            self.assertIn("project-install/main.py", manifest)
            self.assertIn("project-publish/pack_release.py", manifest)

    def test_print_release_manifest_includes_directory_tree(self) -> None:
        stream = io.StringIO()
        with redirect_stdout(stream):
            print_release_manifest(
                release_scope="public",
                tag="v0.1.0",
                manifest=["README.md", "project-install/main.py"],
                selected_license="MIT",
            )
        output = stream.getvalue()
        self.assertIn("发布资源预览", output)
        self.assertIn("目录结构", output)
        self.assertIn("README.md", output)
        self.assertIn("project-install/", output)
        self.assertIn("发布 license：MIT", output)


class ProjectPublishConfirmationTests(unittest.TestCase):
    def make_options(self, root: Path) -> PublishOptions:
        return PublishOptions(
            tag=None,
            repo_visibility="private",
            repo_path=str(root),
            target_repo_path=None,
            release_scope="private",
            list_local_repos=False,
            selected_license=None,
            exclude_skills=(),
            dry_run=False,
            verbose=False,
            skip_upload=False,
            pack_only=False,
        )

    def test_rejects_release_when_user_chooses_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_project_root(root)
            app = ProjectPublishApp(self.make_options(root))
            app.preflight = lambda: None  # type: ignore[method-assign]
            app.run_steps = lambda: self.fail("未确认时不应进入正式发布步骤")  # type: ignore[method-assign]

            with patch("builtins.input", return_value="否"):
                exit_code = app.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(app.failed_step, "preview_release")
            self.assertIn("取消", app.failure_reason or "")


if __name__ == "__main__":
    unittest.main()
