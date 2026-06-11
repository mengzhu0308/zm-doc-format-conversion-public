from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pack_release_models import CommandResult


def log(message: str) -> None:
    print(message, file=sys.stderr)


def find_project_root() -> Path:
    candidates = [
        Path(__file__).resolve().parents[1],
        Path.cwd(),
        *Path.cwd().resolve().parents,
    ]
    for candidate in candidates:
        if (candidate / "skills").is_dir() and (candidate / "VERSION.yaml").is_file():
            return candidate
    print("错误：未找到项目根目录（需要同时存在 skills/ 与 VERSION.yaml）。", file=sys.stderr)
    raise SystemExit(2)


def parse_simple_yaml_mapping(text: str) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    current_section: str | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not raw_line.startswith(" "):
            if not stripped.endswith(":"):
                raise ValueError(f"第 {lineno} 行不是合法的顶层映射")
            current_section = stripped[:-1].strip()
            if not current_section:
                raise ValueError(f"第 {lineno} 行缺少顶层键名")
            data.setdefault(current_section, {})
            continue

        if current_section is None:
            raise ValueError(f"第 {lineno} 行在顶层键之前出现缩进字段")
        if not raw_line.startswith("  ") or raw_line.startswith("   "):
            raise ValueError(f"第 {lineno} 行使用了不支持的缩进层级")
        if ":" not in stripped:
            raise ValueError(f"第 {lineno} 行缺少冒号")

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip("'\"")
        if not key:
            raise ValueError(f"第 {lineno} 行缺少字段名")
        data[current_section][key] = value

    return data


def read_project_info(project_root: Path) -> dict[str, str]:
    parsed = parse_simple_yaml_mapping((project_root / "VERSION.yaml").read_text(encoding="utf-8"))
    info = parsed.get("project_info", {})
    if not info.get("name"):
        raise ValueError("VERSION.yaml 缺少 project_info.name")
    return info


def sanitize_tag(tag: str) -> str:
    return tag.replace("/", "-")


def read_top_level_scalar_and_list_sections(config_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    scalars: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    for lineno, raw_line in enumerate(config_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw_line.startswith(" "):
            current_section = None
            if stripped.endswith(":"):
                current_section = stripped[:-1].strip()
                if not current_section:
                    raise ValueError(f"{config_path.name} 第 {lineno} 行缺少顶层键名")
                sections.setdefault(current_section, [])
                continue
            if ":" not in stripped:
                raise ValueError(f"{config_path.name} 第 {lineno} 行不是合法的顶层键")
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip().strip("'\"")
            if not key:
                raise ValueError(f"{config_path.name} 第 {lineno} 行缺少顶层键名")
            scalars[key] = value
            continue
        if current_section is None:
            raise ValueError(f"{config_path.name} 第 {lineno} 行在顶层键之前出现缩进内容")
        if not raw_line.startswith("  - "):
            raise ValueError(f"{config_path.name} 第 {lineno} 行必须使用 `- value` 列表格式")
        item = stripped[2:].strip().strip("'\"")
        if not item:
            raise ValueError(f"{config_path.name} 第 {lineno} 行缺少列表值")
        sections[current_section].append(item)
    return scalars, sections


def read_top_level_list_sections(config_path: Path) -> dict[str, list[str]]:
    _scalars, sections = read_top_level_scalar_and_list_sections(config_path)
    return sections


def find_public_release_config_path(project_root: Path) -> Path:
    config_path = project_root / "project-public-package" / "release.yaml"
    if config_path.is_file():
        return config_path
    raise FileNotFoundError("缺少 project-public-package/release.yaml")


def list_available_skill_names(project_root: Path) -> list[str]:
    skills_dir = project_root / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(entry.name for entry in skills_dir.iterdir() if entry.is_dir() and (entry / "SKILL.md").is_file())


def parse_yaml_bool(config_path: Path, key: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{config_path.name} 中的 `{key}` 只支持 `true` 或 `false`")


def ensure_known_skills(config_path: Path, section_name: str, names: list[str], available: list[str]) -> None:
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise ValueError(
            f"{config_path.name} 中的 `{section_name}` 引用了不存在的 skill：{', '.join(unknown)}"
        )


def normalize_skill_names(names: tuple[str, ...] | list[str]) -> list[str]:
    return [name.strip() for name in names if name.strip()]


def read_public_release_skills(
    project_root: Path,
    *,
    extra_exclude_skills: tuple[str, ...] = (),
) -> list[str]:
    config_path = find_public_release_config_path(project_root)
    scalars, sections = read_top_level_scalar_and_list_sections(config_path)
    available = list_available_skill_names(project_root)
    skills = sections.get("skills", [])
    exclude_skills = sections.get("exclude_skills", [])
    shared_resources = sections.get("shared_resources", [])
    cli_exclude_skills = normalize_skill_names(extra_exclude_skills)

    if len(skills) != len(set(skills)):
        raise ValueError(f"{config_path.name} 中的 `skills` 不能包含重复项")
    if len(exclude_skills) != len(set(exclude_skills)):
        raise ValueError(f"{config_path.name} 中的 `exclude_skills` 不能包含重复项")
    if len(cli_exclude_skills) != len(set(cli_exclude_skills)):
        raise ValueError("命令行中的 `--exclude-skill` 不能包含重复项")
    if "all_skills" in scalars:
        parse_yaml_bool(config_path, "all_skills", scalars["all_skills"])
        raise ValueError(
            f"{config_path.name} 不再支持 `all_skills`；省略 `skills` 即表示使用当前项目全部 skill"
        )
    if shared_resources:
        raise ValueError(f"{config_path.name} 不允许声明 `shared_resources`；public sibling 目录只允许输出 skill 安装态")

    if skills:
        ensure_known_skills(config_path, "skills", skills, available)
    ensure_known_skills(config_path, "exclude_skills", exclude_skills, available)
    ensure_known_skills(config_path, "命令行 exclude_skills", cli_exclude_skills, available)
    base_skills = skills if skills else available
    if not base_skills:
        raise ValueError(f"{config_path.name} 未发现可公开发布的 skill")
    excluded = set(exclude_skills) | set(cli_exclude_skills)
    resolved = [name for name in base_skills if name not in excluded]
    if not resolved:
        raise ValueError(f"{config_path.name} 在应用排除规则后没有可公开发布的 skill")
    return resolved


def read_private_release_filters(project_root: Path) -> tuple[list[str], list[str]]:
    config_path = project_root / "project-publish" / "release.yaml"
    if not config_path.is_file():
        raise FileNotFoundError("缺少 project-publish/release.yaml")
    sections = read_top_level_list_sections(config_path)
    return sections.get("exclude_paths", []), sections.get("exclude_globs", [])


def run_command(args: list[str], *, cwd: Path) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return CommandResult(completed.returncode, completed.stdout.strip(), completed.stderr.strip())
