from __future__ import annotations

import argparse
from pathlib import Path

from pack_release_models import PackOptions


def _find_project_root() -> Path | None:
    """定位当前项目根目录；找不到时返回 None，由调用方决定如何报错。"""
    candidates = [
        Path(__file__).resolve().parents[1],
        Path.cwd(),
        *Path.cwd().resolve().parents,
    ]
    for candidate in candidates:
        if (candidate / "skills").is_dir() and (candidate / "VERSION.yaml").is_file():
            return candidate
    return None


def _infer_default_tag(parser: argparse.ArgumentParser) -> str:
    """从 VERSION.yaml.project_info.version 推断默认 tag，格式为 v<version>。

    无法定位项目根目录或缺少版本号时，调用 parser.error 给出清晰错误。
    """
    project_root = _find_project_root()
    if project_root is None:
        parser.error(
            "未找到项目根目录（需要同时存在 skills/ 与 VERSION.yaml），无法推断 --tag 默认值；"
            "请显式通过 --tag 指定。"
        )
    version_path = project_root / "VERSION.yaml"
    current_section: str | None = None
    project_version: str | None = None
    for raw_line in version_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(":"):
            current_section = stripped[:-1].strip()
            continue
        if current_section != "project_info" or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key.strip() == "version":
            project_version = value.strip().strip("'\"")
            break
    if not project_version:
        parser.error(
            f"无法从 {version_path} 的 project_info.version 推断默认 tag；"
            "请显式通过 --tag 指定。"
        )
    return f"v{project_version}"


def parse_args() -> PackOptions:
    parser = argparse.ArgumentParser(description="为指定 tag 生成并可选上传项目级发布包。")
    parser.add_argument(
        "--tag",
        default=None,
        help="目标 tag，例如 v0.1.0；未传入时默认从 VERSION.yaml 推断为 v<version>。",
    )
    parser.add_argument("--upload", action="store_true", help="生成 zip 后上传到对应 GitHub Release")
    parser.add_argument("--dry-run", action="store_true", help="预览模式：不实际生成 zip，不上传资产")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument(
        "--repo-visibility",
        choices=("public", "private"),
        dest="repo_visibility",
        help="发布目标仓库可见性；未传入时默认 private。",
    )
    parser.add_argument(
        "--scope",
        choices=("public", "private"),
        dest="repo_visibility",
        help="--repo-visibility 的别名，便于在历史调用方式中继续使用。",
    )
    parser.add_argument(
        "--target-repo",
        help="显式指定上传 Release Asset 的 GitHub 仓库（owner/name）；未传入时默认使用当前仓库。",
    )
    parser.add_argument("--confirmed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--license", dest="selected_license", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.tag is None:
        args.tag = _infer_default_tag(parser)
    if not args.tag:
        parser.error("--tag 不能为空。")

    return PackOptions(
        tag=args.tag,
        upload=args.upload,
        dry_run=args.dry_run,
        verbose=args.verbose,
        release_scope=args.repo_visibility,
        selected_license=args.selected_license,
        target_repo=args.target_repo,
        exclude_skills=(),
        confirmed=args.confirmed,
    )
