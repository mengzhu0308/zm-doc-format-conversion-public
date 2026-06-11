from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from install_app import SkillInstallerApp
from install_cli import parse_args
from install_copy import clean_old_skill, install_skill, sync_tool_links
from install_discovery import discover_install_plan
from install_models import InstallOptions, InstallPlan, ToolTarget
from scripts.shared_tool_targets import MANAGED_ENTRY_MARKER, get_extra_link_dirs, get_ssot_dir, get_tool_dir


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_accepts_multiple_tools(self) -> None:
        argv = [
            "main.py",
            "--tool",
            "claude",
            "--tool",
            "codex",
            "--skill",
            "zm-write-skill-readme",
        ]
        with patch.object(sys, "argv", argv):
            options = parse_args()

        self.assertEqual(options.tools, ("claude", "codex"))
        self.assertEqual(options.skills, ("zm-write-skill-readme",))

    def test_parse_args_rejects_mixing_skill_and_pattern(self) -> None:
        argv = ["main.py", "--skill", "zm-write-skill-readme", "--pattern", "zm-*"]
        stderr = io.StringIO()

        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as exc:
                parse_args()

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("不能同时使用重复 --skill 和 --pattern", stderr.getvalue())


class DiscoveryTests(unittest.TestCase):
    def test_shared_paths_are_built_from_home_and_parts(self) -> None:
        home = Path("/Users/demo")

        self.assertEqual(get_ssot_dir(home), home / ".agent-skills" / ".zm")
        self.assertEqual(get_tool_dir(home, "codex"), home / ".codex" / "skills")
        self.assertEqual(
            get_extra_link_dirs(home, "claude"),
            (home / ".claude-official-accounts-provider" / "shared" / "skills",),
        )

    def test_discover_install_plan_always_uses_ssot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".agent-skills" / ".zm").mkdir(parents=True)
            (home / ".codex" / "skills").mkdir(parents=True)

            with patch("install_discovery.get_home", return_value=home):
                plan = discover_install_plan(("codex", "openclaw"))

        self.assertEqual(plan.mode, "ssot")
        self.assertEqual(plan.install_path, home / ".agent-skills" / ".zm")
        self.assertEqual([item.key for item in plan.tool_targets], ["codex", "openclaw"])
        self.assertEqual([item.key for item in plan.available_tool_targets], ["codex"])
        self.assertEqual([item.key for item in plan.missing_tool_targets], ["openclaw"])

    def test_discover_install_plan_creates_extra_links_for_claude_and_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".agent-skills" / ".zm").mkdir(parents=True)
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".codex" / "skills").mkdir(parents=True)

            with patch("install_discovery.get_home", return_value=home):
                plan = discover_install_plan(("claude", "codex"))

        extra_keys = [item.key for item in plan.extra_link_targets]
        self.assertIn("claude-extra", extra_keys)
        self.assertIn("codex-extra", extra_keys)
        self.assertEqual(
            plan.extra_link_targets[0].path,
            home / ".claude-official-accounts-provider" / "shared" / "skills",
        )


class InstallCopyTests(unittest.TestCase):
    def test_clean_old_skill_removes_tool_entry_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ssot = root / ".agent-skills" / ".zm"
            tool_dir = root / ".codex" / "skills"
            old_skill = ssot / "demo-skill"
            tool_entry = tool_dir / "demo-skill"
            old_skill.mkdir(parents=True)
            tool_entry.mkdir(parents=True)
            (tool_entry / "SKILL.md").write_text("old", encoding="utf-8")

            with patch("install_copy.is_safe_runtime_path", return_value=True):
                clean_old_skill(
                    "demo-skill",
                    ssot,
                    dry_run=False,
                    verbose=False,
                    link_tool_dirs=(tool_dir,),
                )

            self.assertFalse(old_skill.exists())
            self.assertFalse(tool_entry.exists())

    def test_sync_tool_links_replaces_tool_entry_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ssot = root / ".agent-skills" / ".zm"
            tool_dir = root / ".codex" / "skills"
            source = ssot / "demo-skill"
            tool_entry = tool_dir / "demo-skill"
            source.mkdir(parents=True)
            tool_dir.mkdir(parents=True)
            tool_entry.write_text("old", encoding="utf-8")

            with patch("install_copy.is_safe_runtime_path", return_value=True):
                sync_tool_links(
                    "demo-skill",
                    source_dir=ssot,
                    link_tool_dirs=(tool_dir,),
                    verbose=False,
                )

            self.assertTrue(tool_entry.is_symlink())
            self.assertEqual(tool_entry.resolve(), source.resolve())

    def test_sync_tool_links_copies_with_marker_when_symlink_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ssot = root / ".agent-skills" / ".zm"
            tool_dir = root / ".codex" / "skills"
            source = ssot / "demo-skill"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("demo", encoding="utf-8")

            with (
                patch("install_copy.is_safe_runtime_path", return_value=True),
                patch("pathlib.Path.symlink_to", side_effect=OSError("symlink denied")),
            ):
                sync_tool_links(
                    "demo-skill",
                    source_dir=ssot,
                    link_tool_dirs=(tool_dir,),
                    verbose=False,
                )

            tool_entry = tool_dir / "demo-skill"
            self.assertTrue((tool_entry / "SKILL.md").is_file())
            self.assertTrue((tool_entry / MANAGED_ENTRY_MARKER).is_file())

    def test_install_skill_copies_agents_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source" / "demo-skill"
            target = root / ".agent-skills" / ".zm"
            (source / "agents").mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
            (source / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")

            with patch("install_copy.is_safe_runtime_path", return_value=True):
                install_skill(source, target, dry_run=False, verbose=False)

            self.assertTrue((target / "demo-skill" / "agents" / "openai.yaml").is_file())


class AppRunTests(unittest.TestCase):
    def make_options(self, *, tools: tuple[str, ...] = (), skills: tuple[str, ...] = ("demo-skill",)) -> InstallOptions:
        return InstallOptions(
            dry_run=True,
            verbose=False,
            skills=skills,
            pattern=None,
            tools=tools,
        )

    def test_select_tools_requires_explicit_flag_when_no_interactive_terminal_exists(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        skill_path = root / "skills" / "demo-skill"
        options = self.make_options(tools=(), skills=("demo-skill",))
        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[skill_path]),
            patch("install_app.sys.stdin.isatty", return_value=False),
            patch.object(SkillInstallerApp, "open_controlling_tty", return_value=None),
        ):
            app = SkillInstallerApp(options)
            selected = app.select_tools()

        self.assertEqual(selected, ())
        self.assertIn("无法访问 /dev/tty", app.tool_error or "")

    def test_select_tools_falls_back_to_dev_tty_when_stdin_is_not_tty(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        skill_path = root / "skills" / "demo-skill"
        options = self.make_options()
        stdout = io.StringIO()
        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[skill_path]),
            patch("install_app.sys.stdin.isatty", return_value=False),
            patch.object(SkillInstallerApp, "open_controlling_tty", return_value=io.StringIO("\n")),
            redirect_stdout(stdout),
        ):
            app = SkillInstallerApp(options)
            selected = app.select_tools()

        self.assertEqual(selected, ("claude", "codex"))
        self.assertIsNone(app.tool_error)

    def test_prompt_for_tools_defaults_to_claude_and_codex_on_empty_input(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        skill_path = root / "skills" / "demo-skill"
        options = self.make_options()
        stdout = io.StringIO()
        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[skill_path]),
            redirect_stdout(stdout),
        ):
            app = SkillInstallerApp(options)
            selected = app.prompt_for_tools(io.StringIO("\n"))

        self.assertEqual(selected, ("claude", "codex"))

    def test_prompt_for_tools_supports_select_all_alias(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        skill_path = root / "skills" / "demo-skill"
        options = self.make_options()
        stdout = io.StringIO()
        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[skill_path]),
            redirect_stdout(stdout),
        ):
            app = SkillInstallerApp(options)
            selected = app.prompt_for_tools(io.StringIO("0\n"))

        self.assertEqual(selected, ("claude", "codex", "gemini", "kimi", "opencode", "openclaw"))

    def test_prompt_for_tools_reports_closed_terminal_as_error(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        skill_path = root / "skills" / "demo-skill"
        options = self.make_options()
        stdout = io.StringIO()
        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[skill_path]),
            redirect_stdout(stdout),
        ):
            app = SkillInstallerApp(options)
            selected = app.prompt_for_tools(io.StringIO(""))

        self.assertEqual(selected, ())
        self.assertIn("交互终端已关闭", app.tool_error or "")

    def test_run_installs_to_ssot_even_when_tool_dir_is_missing(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        skill_path = root / "skills" / "demo-skill"
        ssot = Path("/tmp/ssot/.zm")
        options = self.make_options(tools=("codex",))
        plan = InstallPlan(
            mode="ssot",
            install_path=ssot,
            tool_targets=(
                ToolTarget(
                    key="codex",
                    label="Codex",
                    path=Path("/tmp/missing-codex"),
                    exists=False,
                ),
            ),
        )
        stdout = io.StringIO()
        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[skill_path]),
            patch("install_app.discover_install_plan", return_value=plan),
            patch.object(SkillInstallerApp, "install_one") as install_one,
            redirect_stdout(stdout),
        ):
            result = SkillInstallerApp(options).run()

        self.assertEqual(result, 0)
        install_one.assert_called_once_with(
            skill_path,
            ssot,
            link_dirs=(Path("/tmp/missing-codex"),),
        )
        self.assertIn(str(ssot), stdout.getvalue())

    def test_run_fails_when_pattern_matches_no_skill(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        available_skill = root / "skills" / "demo-skill"
        options = InstallOptions(
            dry_run=True,
            verbose=False,
            skills=(),
            pattern="zm-*",
            tools=("codex",),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("install_app.find_project_root", return_value=root),
            patch("install_app.find_skills", return_value=[available_skill]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = SkillInstallerApp(options).run()

        self.assertEqual(result, 1)
        self.assertIn("未找到匹配模式 `zm-*` 的 skill", stderr.getvalue())
        self.assertIn("可安装 skill：demo-skill", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
