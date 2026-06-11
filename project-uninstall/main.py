#!/usr/bin/env python3
"""project-uninstall 的 CLI 入口。"""

from __future__ import annotations

from uninstall_app import SkillUninstallerApp
from uninstall_cli import parse_args


def main() -> int:
    return SkillUninstallerApp(parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
