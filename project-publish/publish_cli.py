from __future__ import annotations

import argparse

from publish_models import PublishOptions


def parse_args() -> PublishOptions:
    parser = argparse.ArgumentParser(description="按统一流程执行项目级私有发布或公开发布。")
    parser.add_argument(
        "visibility",
        nargs="?",
        choices=("public", "private"),
        help="兼容语义入口的位置参数，可写为 private 或 public；等价于 --repo-visibility。",
    )
    parser.add_argument("--tag", help="显式指定 tag，默认从 VERSION.yaml 推导为 v<version>")
    parser.add_argument(
        "--repo-visibility",
        choices=("public", "private"),
        help="显式确认目标 GitHub 仓库是 public 还是 private；非 pack-only 模式必填。",
    )
    parser.add_argument("--repo-path", help="显式指定源码项目根目录。")
    parser.add_argument(
        "--target-repo-path",
        help="显式指定发布目标仓库的本地 Git 根目录；未传入时 private 默认使用当前源码仓库，public 默认使用会话目录下的临时目标仓库。",
    )
    parser.add_argument(
        "--list-local-repos",
        action="store_true",
        help="列出本机可用于 project-publish 的本地 GitHub 项目仓库，并退出。",
    )
    parser.add_argument("--dry-run", action="store_true", help="预览模式：不修改远端状态")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument("--skip-upload", action="store_true", help="执行打包但不上传 Assets；此模式不算发布完成")
    parser.add_argument("--pack-only", action="store_true", help="只执行第 4 步与第 5 步，只打包不上传")
    parser.add_argument("--license", dest="selected_license", help=argparse.SUPPRESS)
    args = parser.parse_args()
    repo_visibility = args.repo_visibility or args.visibility
    if args.repo_visibility and args.visibility and args.repo_visibility != args.visibility:
        parser.error("位置参数 visibility 与 --repo-visibility 不一致。")
    return PublishOptions(
        tag=args.tag,
        repo_visibility=repo_visibility,
        repo_path=args.repo_path,
        target_repo_path=args.target_repo_path,
        release_scope=repo_visibility,
        list_local_repos=args.list_local_repos,
        selected_license=args.selected_license,
        exclude_skills=(),
        dry_run=args.dry_run,
        verbose=args.verbose,
        skip_upload=args.skip_upload,
        pack_only=args.pack_only,
    )
