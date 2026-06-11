from __future__ import annotations

import sys
from pathlib import Path

from uninstall_models import InstallPlan, ToolTarget
from scripts.shared_tool_targets import (
    AI_TOOL_LABELS,
    get_extra_link_dirs,
    get_ssot_dir,
    get_tool_dir,
)


def get_home() -> Path:
    return Path.home()


def discover_uninstall_plan(selected_tools: tuple[str, ...]) -> InstallPlan:
    home = get_home()
    tool_targets = tuple(
        ToolTarget(
            key=tool,
            label=AI_TOOL_LABELS[tool],
            path=get_tool_dir(home, tool),
            exists=get_tool_dir(home, tool).is_dir(),
        )
        for tool in selected_tools
    )
    extra_link_targets: list[ToolTarget] = []
    for tool in selected_tools:
        for path in get_extra_link_dirs(home, tool):
            extra_link_targets.append(
                ToolTarget(
                    key=f"{tool}-extra",
                    label=f"{AI_TOOL_LABELS[tool]} 额外",
                    path=path,
                    exists=path.is_dir(),
                )
            )
    ssot = get_ssot_dir(home)
    return InstallPlan(
        mode="ssot",
        install_path=ssot,
        tool_targets=tool_targets,
        extra_link_targets=tuple(extra_link_targets),
    )


def find_project_root() -> Path:
    candidates = [Path(__file__).resolve().parent.parent, Path.cwd()]
    for candidate in candidates:
        if (candidate / "skills").is_dir():
            return candidate

    print("错误：未找到 skills/ 目录。请在项目根目录下运行此脚本。", file=sys.stderr)
    raise SystemExit(1)


def find_skills(project_root: Path) -> list[Path]:
    skills_dir = project_root / "skills"
    if not skills_dir.is_dir():
        return []

    return sorted(
        entry for entry in skills_dir.iterdir() if entry.is_dir() and (entry / "SKILL.md").is_file()
    )
