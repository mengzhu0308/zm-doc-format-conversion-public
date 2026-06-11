from __future__ import annotations

import argparse

from install_models import AI_TOOL_ORDER, InstallOptions


def parse_args() -> InstallOptions:
    parser = argparse.ArgumentParser(description="将项目 skills/ 中的全部或指定 skill 安装至系统最优位置")
    parser.add_argument("--dry-run", action="store_true", help="预览模式：显示将执行的操作但不实际安装")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument("--skill", action="append", default=[], help="安装指定 skill 名称；可重复传入。")
    parser.add_argument("--pattern", help="按 glob 模式安装 skill，例如 `zm-humanizer-*`。")
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        choices=AI_TOOL_ORDER,
        help=f"安装目标工具；可重复传入。可选值：{'、'.join(AI_TOOL_ORDER)}。",
    )
    args = parser.parse_args()
    if args.skill and args.pattern:
        parser.error("不能同时使用重复 --skill 和 --pattern。")
    return InstallOptions(
        dry_run=args.dry_run,
        verbose=args.verbose,
        skills=tuple(args.skill),
        pattern=args.pattern,
        tools=tuple(args.tool),
    )
