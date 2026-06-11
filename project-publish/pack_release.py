#!/usr/bin/env python3
"""project-publish 统一打包入口。"""

from __future__ import annotations

from pack_release_app import ReleasePackager
from pack_release_cli import parse_args


def main() -> int:
    return ReleasePackager(parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
