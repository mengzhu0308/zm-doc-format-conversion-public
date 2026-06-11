from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uninstall_app import SkillUninstallerApp
from uninstall_cli import parse_args
from uninstall_discovery import discover_uninstall_plan
from uninstall_models import InstallPlan, ToolTarget, UninstallOptions
from uninstall_remove import remove_tool_links, uninstall_skill
from scripts.shared_tool_targets import MANAGED_ENTRY_MARKER


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_accepts_multiple_tools(self) -> None:
        argv = [
            "main.py",
            "--tool",
            "claude",
            "--tool",
            "codex",
            "--skill",
            "zm-init-skill-project",
        ]
        with patch.object(sys, "argv", argv):
            options = parse_args()

        self.assertEqual(options.tools, ("claude", "codex"))
        self.assertEqual(options.skills, ("zm-init-skill-project",))

    def test_parse_args_rejects_mixing_skill_and_pattern(self) -> None:
        argv = ["main.py", "--skill", "zm-init-skill-project", "--pattern", "zm-*"]
        stderr = io.StringIO()

        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as exc:
                parse_args()

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("不能同时使用重复 --skill 和 --pattern", stderr.getvalue())


class DiscoveryTests(unittest.TestCase):
    def test_discover_uninstall_plan_always_uses_ssot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".agent-skills" / ".zm").mkdir(parents=True)
            (home / ".codex" / "skills").mkdir(parents=True)

            with patch("uninstall_discovery.get_home", return_value=home):
                plan = discover_uninstall_plan(("codex", "openclaw"))

        self.assertEqual(plan.mode, "ssot")
        self.assertEqual(plan.install_path, home / ".agent-skills" / ".zm")
        self.assertEqual([item.key for item in plan.tool_targets], ["codex", "openclaw"])
        self.assertEqual([item.key for item in plan.available_tool_targets], ["codex"])
        self.assertEqual([item.key for item in plan.missing_tool_targets], ["openclaw"])

    def test_discover_uninstall_plan_creates_extra_links_for_claude_and_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".agent-skills" / ".zm").mkdir(parents=True)
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".codex" / "skills").mkdir(parents=True)

            with patch("uninstall_discovery.get_home", return_value=home):
                plan = discover_uninstall_plan(("claude", "codex"))

        extra_keys = [item.key for item in plan.extra_link_targets]
        self.assertIn("claude-extra", extra_keys)
        self.assertIn("codex-extra", extra_keys)


class RemoveTests(unittest.TestCase):
    def test_uninstall_skill_removes_links_first_then_ssot_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ssot = root / ".agent-skills" / ".zm"
            claude_dir = root / ".claude" / "skills"
            codex_dir = root / ".codex" / "skills"
            skill_dir = ssot / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("demo", encoding="utf-8")
            claude_dir.mkdir(parents=True)
            codex_dir.mkdir(parents=True)
            (claude_dir / "demo-skill").symlink_to(skill_dir, target_is_directory=True)
            (codex_dir / "demo-skill").symlink_to(skill_dir, target_is_directory=True)

            with patch("uninstall_remove.is_safe_runtime_path", return_value=True):
                uninstall_skill(
                    "demo-skill",
                    ssot,
                    dry_run=False,
                    verbose=False,
                    link_tool_dirs=(claude_dir, codex_dir),
                )

        self.assertFalse(skill_dir.exists())
        self.assertFalse((claude_dir / "demo-skill").exists())
        self.assertFalse((codex_dir / "demo-skill").exists())

    def test_uninstall_skill_skips_missing_target_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            target_dir = Path(tmp) / ".agent-skills" / ".zm"
            target_dir.mkdir(parents=True)

            with redirect_stdout(stdout), patch("uninstall_remove.is_safe_runtime_path", return_value=True):
                uninstall_skill("missing-skill", target_dir, dry_run=True, verbose=False)

        self.assertIn("未安装", stdout.getvalue())

    def test_remove_tool_links_removes_managed_copy_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool_dir = root / ".codex" / "skills"
            copied_entry = tool_dir / "demo-skill"
            copied_entry.mkdir(parents=True)
            (copied_entry / "SKILL.md").write_text("demo", encoding="utf-8")
            (copied_entry / MANAGED_ENTRY_MARKER).write_text("managed", encoding="utf-8")

            with patch("uninstall_remove.is_safe_runtime_path", return_value=True):
                remove_tool_links(
                    "demo-skill",
                    link_tool_dirs=(tool_dir,),
                    dry_run=False,
                    verbose=False,
                )

        self.assertFalse(copied_entry.exists())


class AppRunTests(unittest.TestCase):
    def make_options(self, *, tools: tuple[str, ...] = (), skills: tuple[str, ...] = ("demo-skill",)) -> UninstallOptions:
        return UninstallOptions(
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
            patch("uninstall_app.find_project_root", return_value=root),
            patch("uninstall_app.find_skills", return_value=[skill_path]),
            patch("uninstall_app.sys.stdin.isatty", return_value=False),
            patch.object(SkillUninstallerApp, "open_controlling_tty", return_value=None),
        ):
            app = SkillUninstallerApp(options)
            selected = app.select_tools()

        self.assertEqual(selected, ())
        self.assertIn("无法访问 /dev/tty", app.tool_error or "")

    def test_run_uninstalls_from_ssot_even_when_tool_dir_is_missing(self) -> None:
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
            patch("uninstall_app.find_project_root", return_value=root),
            patch("uninstall_app.find_skills", return_value=[skill_path]),
            patch("uninstall_app.discover_uninstall_plan", return_value=plan),
            patch.object(SkillUninstallerApp, "uninstall_one") as uninstall_one,
            redirect_stdout(stdout),
        ):
            result = SkillUninstallerApp(options).run()

        self.assertEqual(result, 0)
        uninstall_one.assert_called_once_with(
            skill_path, ssot, link_dirs=(Path("/tmp/missing-codex"),)
        )
        self.assertIn(str(ssot), stdout.getvalue())

    def test_run_fails_when_pattern_matches_no_skill(self) -> None:
        root = Path("/tmp/zm-basic-skills")
        available_skill = root / "skills" / "demo-skill"
        options = UninstallOptions(
            dry_run=True,
            verbose=False,
            skills=(),
            pattern="zm-*",
            tools=("codex",),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("uninstall_app.find_project_root", return_value=root),
            patch("uninstall_app.find_skills", return_value=[available_skill]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = SkillUninstallerApp(options).run()

        self.assertEqual(result, 1)
        self.assertIn("未找到匹配模式 `zm-*` 的 skill", stderr.getvalue())
        self.assertIn("可卸载 skill：demo-skill", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
