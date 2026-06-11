from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import TextIO

from install_copy import clean_old_skill, install_skill
from install_discovery import discover_install_plan, find_project_root, find_skills
from install_models import AI_TOOL_LABELS, AI_TOOL_ORDER, InstallOptions, InstallPlan, ToolTarget

DEFAULT_INTERACTIVE_TOOLS = ("claude", "codex")
CONTROLLING_TTY = Path("/dev/tty")


class SkillInstallerApp:
    def __init__(self, options: InstallOptions) -> None:
        self.options = options
        self.project_root = find_project_root()
        self.available_skills = find_skills(self.project_root)
        self.selection_error: str | None = None
        self.tool_error: str | None = None
        self.skills = self.select_skills()
        self.tools: tuple[str, ...] = ()
        self.plan: InstallPlan | None = None

    def run(self) -> int:
        print(f"项目根目录：{self.project_root}")
        if not self.available_skills:
            print("未找到有效的 skill（skills/ 目录为空或不含 SKILL.md）。")
            return 0
        if self.selection_error:
            print(f"错误：{self.selection_error}", file=sys.stderr)
            print(
                f"可安装 skill：{', '.join(skill.name for skill in self.available_skills)}",
                file=sys.stderr,
            )
            return 1
        self.tools = self.select_tools()
        if self.tool_error:
            print(f"错误：{self.tool_error}", file=sys.stderr)
            return 1
        self.plan = discover_install_plan(self.tools)

        if self.options.skills:
            print(f"按指定名称安装 {len(self.skills)} 个 skill：{', '.join(skill.name for skill in self.skills)}")
        elif self.options.pattern:
            print(f"按模式 `{self.options.pattern}` 安装 {len(self.skills)} 个 skill：{', '.join(skill.name for skill in self.skills)}")
        else:
            print(f"发现 {len(self.skills)} 个 skill：{', '.join(skill.name for skill in self.skills)}")
        print(f"已选择工具：{', '.join(AI_TOOL_LABELS[tool] for tool in self.tools)}")
        self.print_install_targets()
        if self.options.dry_run:
            print("\n===== 预览模式 =====\n")

        if not self.plan.install_path:
            print("错误：无法确定安装目标路径。", file=sys.stderr)
            return 1

        installed, successful_tools = self.install_skills()
        mode = "预览" if self.options.dry_run else "安装"
        print(f"\n完成：{mode}了 {installed} 个 skill 至 {self.plan.install_path}")
        if successful_tools:
            print(f"已同步入口：{', '.join(target.label for target in successful_tools)}")
        return 0

    def select_skills(self) -> list[Path]:
        if self.options.skills:
            requested = self._dedupe_names(self.options.skills)
            selected = [skill for skill in self.available_skills if skill.name in requested]
            selected_names = {skill.name for skill in selected}
            missing = [name for name in requested if name not in selected_names]
            if missing:
                self.selection_error = f"未找到指定 skill：{', '.join(missing)}"
            elif not selected:
                self.selection_error = "指定的 skill 为空"
            return selected

        if self.options.pattern:
            selected = [skill for skill in self.available_skills if fnmatch(skill.name, self.options.pattern)]
            if not selected:
                self.selection_error = f"未找到匹配模式 `{self.options.pattern}` 的 skill"
            return selected

        return self.available_skills

    @staticmethod
    def _dedupe_names(names: tuple[str, ...]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped

    def select_tools(self) -> tuple[str, ...]:
        if self.options.tools:
            return tuple(self._dedupe_names(self.options.tools))
        interactive_input = self.get_interactive_input()
        if interactive_input is None:
            self.tool_error = (
                "未指定安装目标工具；当前环境既不是交互式 stdin，也无法访问 /dev/tty，请显式传入一个或多个 --tool。"
            )
            return ()
        try:
            return self.prompt_for_tools(interactive_input)
        finally:
            if interactive_input is not sys.stdin:
                interactive_input.close()

    @staticmethod
    def open_controlling_tty() -> TextIO | None:
        try:
            return CONTROLLING_TTY.open("r", encoding="utf-8")
        except OSError:
            return None

    def get_interactive_input(self) -> TextIO | None:
        if sys.stdin.isatty():
            return sys.stdin
        return self.open_controlling_tty()

    def prompt_for_tools(self, input_stream: TextIO | None = None) -> tuple[str, ...]:
        input_stream = input_stream or sys.stdin
        aliases: dict[str, str] = {
            "0": "all",
            "all": "all",
            "全部": "all",
            "全选": "all",
            **{str(i + 1): tool for i, tool in enumerate(AI_TOOL_ORDER)},
            **{tool: tool for tool in AI_TOOL_ORDER},
            "claude code": "claude",
        }
        default_labels = "、".join(AI_TOOL_LABELS[tool] for tool in DEFAULT_INTERACTIVE_TOOLS)
        print("请选择安装目标工具（可多选，使用逗号分隔）：")
        print("  0. 全选")
        for index, tool in enumerate(AI_TOOL_ORDER, start=1):
            print(f"  {index}. {AI_TOOL_LABELS[tool]} ({tool})")

        while True:
            print(
                f"输入编号或名称，例如 0、1,2 或 claude,codex；直接回车默认选择 {default_labels}：",
                end="",
                flush=True,
            )
            raw = input_stream.readline()
            if raw == "":
                self.tool_error = "未指定安装目标工具；交互终端已关闭或不可读，请重新运行并显式传入一个或多个 --tool。"
                return ()
            raw = raw.strip()
            if not raw:
                return DEFAULT_INTERACTIVE_TOOLS
            items = [part.strip().lower() for part in raw.replace("，", ",").split(",") if part.strip()]
            resolved: list[str] = []
            invalid: list[str] = []
            for item in items:
                tool = aliases.get(item)
                if tool is None:
                    invalid.append(item)
                    continue
                if tool == "all":
                    return AI_TOOL_ORDER
                if tool not in resolved:
                    resolved.append(tool)
            if invalid:
                print(f"错误：无效选择：{', '.join(invalid)}")
                continue
            return tuple(resolved)

    def print_install_targets(self) -> None:
        assert self.plan is not None
        print(f"安装目标：SSOT ({self.plan.install_path})")
        for target in self.plan.tool_targets:
            status = "将同步符号链接" if target.exists else "目标目录不存在，将自动创建"
            print(f"  - {target.label}：{status} {target.path}")
        for target in self.plan.extra_link_targets:
            status = "将同步符号链接" if target.exists else "目标目录不存在，将自动创建"
            print(f"  - {target.label}：{status} {target.path}")

    def install_skills(self) -> tuple[int, tuple[ToolTarget, ...]]:
        assert self.plan is not None
        assert self.plan.install_path is not None

        if not self.options.dry_run:
            self.plan.install_path.mkdir(parents=True, exist_ok=True)

        all_link_dirs = tuple(target.path for target in self.plan.all_link_targets)
        for skill_path in self.skills:
            self.install_one(
                skill_path,
                self.plan.install_path,
                link_dirs=all_link_dirs,
            )

        return len(self.skills), self.plan.all_link_targets

    def install_one(
        self,
        skill_path: Path,
        target_dir: Path,
        *,
        link_dirs: tuple[Path, ...] = (),
    ) -> None:
        prefix = "[预览] " if self.options.dry_run else ""
        print(f"\n{prefix}安装 {skill_path.name} ...")
        clean_old_skill(
            skill_path.name,
            target_dir,
            self.options.dry_run,
            self.options.verbose,
            link_tool_dirs=link_dirs,
        )
        install_skill(
            skill_path,
            target_dir,
            self.options.dry_run,
            self.options.verbose,
            link_tool_dirs=link_dirs,
        )
