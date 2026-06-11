from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure project root is on path so shared modules are importable from subdirs
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.shared_tool_targets import (
    AI_TOOL_DIRS,
    AI_TOOL_EXTRA_LINKS,
    AI_TOOL_LABELS,
    AI_TOOL_ORDER,
    CC_SWITCH_SSOT,
)


@dataclass(frozen=True)
class UninstallOptions:
    dry_run: bool
    verbose: bool
    skills: tuple[str, ...]
    pattern: str | None
    tools: tuple[str, ...]


@dataclass(frozen=True)
class ToolTarget:
    key: str
    label: str
    path: Path
    exists: bool


@dataclass(frozen=True)
class InstallPlan:
    mode: str
    install_path: Path | None
    tool_targets: tuple[ToolTarget, ...]
    extra_link_targets: tuple[ToolTarget, ...] = ()

    @property
    def available_tool_targets(self) -> tuple[ToolTarget, ...]:
        return tuple(target for target in self.tool_targets if target.exists)

    @property
    def missing_tool_targets(self) -> tuple[ToolTarget, ...]:
        return tuple(target for target in self.tool_targets if not target.exists)

    @property
    def all_link_targets(self) -> tuple[ToolTarget, ...]:
        return self.tool_targets + self.extra_link_targets
