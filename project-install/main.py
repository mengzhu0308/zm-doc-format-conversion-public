#!/usr/bin/env python3
"""project-install 的 CLI 入口。"""

from __future__ import annotations

from install_app import SkillInstallerApp
from install_cli import parse_args


def main() -> int:
    return SkillInstallerApp(parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
