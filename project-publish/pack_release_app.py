from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from pack_release_collect import cleanup_temp_files, gather_release_entries
from pack_release_models import PackOptions, ReleasePackage
from pack_release_output import build_result, maybe_log_candidates, write_result
from pack_release_support import find_project_root, log, read_project_info, run_command, sanitize_tag
from publish_steps import ensure_git_repo, ensure_main_branch
from publish_support import validate_release_inputs
from release_confirmation import (
    print_release_manifest,
    require_release_confirmation,
    write_release_manifest,
)


def resolve_release_scope(options: PackOptions) -> str:
    return options.release_scope or "private"


def build_package_name(project_name: str, tag: str, release_scope: str) -> str:
    project_part = f"{project_name}-private" if release_scope == "private" else project_name
    return f"{project_part}_{sanitize_tag(tag)}_installable.zip"


class ReleasePackager:
    def __init__(self, options: PackOptions) -> None:
        self.options = options
        self.project_root = find_project_root()
        self.project_info = read_project_info(self.project_root)
        self.release_scope = resolve_release_scope(options)
        self.package_project_name = self.resolve_package_project_name()
        self.package = self._build_package_paths()
        self.uploaded = False
        self.release_url = ""

    def resolve_package_project_name(self) -> str:
        if self.release_scope != "public" or self.options.target_repo is None:
            return self.project_info["name"]
        parts = self.options.target_repo.split("/", 1)
        if len(parts) != 2 or not parts[1].strip():
            return self.project_info["name"]
        return parts[1].strip()

    def _build_package_paths(self) -> ReleasePackage:
        package_name = build_package_name(self.package_project_name, self.options.tag, self.release_scope)
        cache_dir = self.project_root / ".cache" / "pack-release" / self.release_scope / self.options.tag
        cache_dir.mkdir(parents=True, exist_ok=True)
        package_path = cache_dir / package_name
        result_path = cache_dir / "result.json"
        return ReleasePackage(
            package_name=package_name,
            package_path=package_path,
            cache_dir=cache_dir,
            result_path=result_path,
        )

    def build_release_command(self, subcommand_args: list[str]) -> list[str]:
        command = ["gh", "release", *subcommand_args]
        if self.options.target_repo is not None:
            command.extend(["--repo", self.options.target_repo])
        return command

    def run_project_command(self, args: list[str], _snapshot_name: str):
        return run_command(args, cwd=self.project_root)

    def ensure_release_branch_precondition(self) -> None:
        ensure_git_repo(self.run_project_command)
        ensure_main_branch(self.run_project_command)

    def create_zip(self, entries: list[tuple[Path, Path]]) -> None:
        self.package.package_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.package.package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, archive_name in entries:
                archive.write(source, arcname=archive_name.as_posix())

    def ensure_release_exists(self) -> str:
        result = run_command(self.build_release_command(["view", self.options.tag, "--json", "url", "-q", ".url"]), cwd=self.project_root)
        if result.code != 0:
            raise RuntimeError(result.stderr or f"未找到 tag {self.options.tag} 对应的 GitHub Release")
        return result.stdout

    def upload_asset(self) -> str:
        release_url = self.ensure_release_exists()
        result = run_command(
            self.build_release_command(["upload", self.options.tag, str(self.package.package_path), "--clobber"]),
            cwd=self.project_root,
        )
        if result.code != 0:
            raise RuntimeError(result.stderr or "Release Asset 上传失败")
        return release_url

    def maybe_create_zip(self, entries: list[tuple[Path, Path]]) -> str:
        if self.options.dry_run:
            return "planned"
        self.create_zip(entries)
        if self.options.verbose:
            log(f"已生成发布包：{self.package.package_path}")
        return "created"

    def maybe_upload(self) -> None:
        if not self.options.upload:
            return
        if shutil.which("gh") is None:
            raise RuntimeError("未找到 gh CLI，请先安装 GitHub CLI。")
        if self.options.dry_run:
            return
        self.release_url = self.upload_asset()
        self.uploaded = True

    def maybe_probe_release_url(self) -> None:
        if self.release_url or not (self.options.upload or self.options.dry_run):
            return
        try:
            self.release_url = self.ensure_release_exists()
        except RuntimeError:
            self.release_url = ""

    def maybe_confirm_release(self, included_entries: list[Path]) -> None:
        if self.options.confirmed:
            return
        manifest = [entry.as_posix() for entry in included_entries]
        print_release_manifest(
            release_scope=self.release_scope,
            tag=self.options.tag,
            manifest=manifest,
            selected_license=self.options.selected_license,
        )
        write_release_manifest(self.package.cache_dir / "release_manifest.txt", manifest)
        if self.options.upload and not self.options.dry_run:
            require_release_confirmation(release_scope=self.release_scope)

    def run(self) -> int:
        detected = cleanup_temp_files(self.project_root)
        if self.options.verbose and detected:
            log(f"检测到临时文件 {len(detected)} 个；project-publish 不会自动删除")
        package_status = "planned" if self.options.dry_run else "pending"
        try:
            validate_release_inputs(
                self.release_scope,
                self.release_scope,
                self.options.selected_license,
                pack_only=False,
            )
            self.ensure_release_branch_precondition()
            entries, _ = gather_release_entries(
                self.project_root,
                release_scope=self.release_scope,
                selected_license=self.options.selected_license,
                exclude_skills=self.options.exclude_skills,
            )
            included_entries = [archive_name for _, archive_name in entries]
            self.maybe_confirm_release(included_entries)
            maybe_log_candidates(
                self.project_root,
                self.options.tag,
                entries,
                verbose=self.options.verbose,
                release_scope=self.release_scope,
                selected_license=self.options.selected_license,
            )
            package_status = self.maybe_create_zip(entries)
            self.maybe_upload()
            self.maybe_probe_release_url()
            write_result(
                self.package.result_path,
                build_result(
                    tag=self.options.tag,
                    package_name=self.package.package_name,
                    package_path=self.package.package_path,
                    package_status=package_status,
                    release_scope=self.release_scope,
                    selected_license=self.options.selected_license,
                    uploaded=self.uploaded,
                    release_url=self.release_url,
                    dry_run=self.options.dry_run,
                    included_entries=included_entries,
                    error=None,
                ),
            )
            return 0
        except Exception as exc:
            write_result(
                self.package.result_path,
                build_result(
                    tag=self.options.tag,
                    package_name=self.package.package_name,
                    package_path=self.package.package_path,
                    package_status="failed",
                    release_scope=self.release_scope,
                    selected_license=self.options.selected_license,
                    uploaded=self.uploaded,
                    release_url=self.release_url,
                    dry_run=self.options.dry_run,
                    included_entries=[],
                    error=str(exc),
                ),
            )
            return 1
