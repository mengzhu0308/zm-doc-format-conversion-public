#!/usr/bin/env python3
"""project-publish 统一发布入口。"""

from __future__ import annotations

from publish_app import ProjectPublishApp
from publish_cli import parse_args
from publish_support import discover_local_publish_candidates, print_public_publish_warning


def print_local_repo_candidates(repo_visibility: str | None, *, verbose: bool) -> int:
    if repo_visibility == "public":
        print_public_publish_warning("扫描可用于公开发布的本地 GitHub 仓库")
    candidates = discover_local_publish_candidates()
    if repo_visibility is not None:
        candidates = [item for item in candidates if item.visibility == repo_visibility]
    if not candidates:
        scope = repo_visibility or "project-publish"
        print(f"未找到可用于 {scope} 的本机 GitHub 项目仓库。")
        return 1

    print("本机可用于 project-publish 的 GitHub 项目仓库：")
    for index, candidate in enumerate(candidates, start=1):
        print(f"{index}. {candidate.name_with_owner} [{candidate.visibility}]")
        print(f"   本地路径：{candidate.project_root}")
        if verbose:
            print(f"   远端地址：{candidate.remote_url}")
    print("\n请先让用户明确选择一个本地路径后重新执行。")
    return 0


def main() -> int:
    options = parse_args()
    if options.list_local_repos:
        return print_local_repo_candidates(options.repo_visibility, verbose=options.verbose)
    if options.repo_visibility == "public":
        print_public_publish_warning("正式公开发布")
    return ProjectPublishApp(options).run()


if __name__ == "__main__":
    raise SystemExit(main())
