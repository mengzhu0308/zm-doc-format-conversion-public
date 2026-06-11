from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from publish_models import PublishOptions, PublishSession, StepOutcome

CHANGELOG_SECTION_RE = re.compile(r"^## \[(?P<label>[^\]]+)\]")
CHANGELOG_CATEGORIES = ("Added", "Changed", "Fixed")
RELEASE_NOTE_BULLET_RE = re.compile(r"^[-*]\s+")
RELEASE_NOTE_NUMBERED_RE = re.compile(r"^\d+\.\s+")
RELEASE_NOTE_PR_SUFFIX_RE = re.compile(r"\s+\(#\d+\)$")
WECHAT_MAX_LENGTH = 180
CHANGELOG_BENEFIT_TERMS = (
    "发布",
    "微信动态",
    "归档",
    "dry-run",
    "工作树",
    "整仓快照",
    "语义触发",
    "目标仓库",
    "可见性",
    "上传",
    "打包",
    "自动",
    "不再",
    "统一",
    "支持",
)
CHANGELOG_NOISE_TERMS = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "references/",
    "project-test",
    "tests/",
    "模板",
    "文档",
    "说明",
    "口径",
    "校验",
)


def build_wechat_post_for_project(
    *,
    project_root: Path,
    project_name: str,
    tag: str,
    release_url: str,
    release_notes: str = "",
    release_manifest: list[str] | None = None,
    project_description: str = "",
) -> str:
    highlights = resolve_wechat_highlights(
        project_root=project_root,
        tag=tag,
        release_notes=release_notes,
        release_manifest=release_manifest or [],
    )
    return build_wechat_post(
        project_name,
        tag,
        release_url,
        highlights=highlights,
        project_description=project_description,
    )


def build_wechat_post(
    project_name: str,
    tag: str,
    release_url: str,
    *,
    highlights: list[str] | None = None,
    project_description: str = "",
) -> str:
    intro = f"{project_name} {tag} 已完成发布。"
    release_line = f"Release URL：{release_url or '待生成'}。"
    normalized_highlights = [item.strip().rstrip("。") for item in (highlights or []) if item.strip()]
    detail_candidates: list[str] = []
    if normalized_highlights:
        for count in range(min(3, len(normalized_highlights)), 0, -1):
            detail_candidates.append(f"本次更新重点是{'；'.join(normalized_highlights[:count])}。")
    if project_description:
        detail_candidates.append(f"{project_description}，本次版本继续完善发布体验与交付链路。")
    detail_candidates.append("本次版本继续完善发布体验与交付链路。")
    for detail in detail_candidates:
        text = f"{intro}{detail}{release_line}"
        if len(text) <= WECHAT_MAX_LENGTH:
            return text
    fallback = detail_candidates[-1]
    allowed = max(0, WECHAT_MAX_LENGTH - len(intro) - len(release_line))
    if len(fallback) > allowed and allowed > 1:
        fallback = fallback[: allowed - 1].rstrip("，；。 ") + "…"
    return f"{intro}{fallback}{release_line}"


def resolve_wechat_highlights(
    *,
    project_root: Path,
    tag: str,
    release_notes: str,
    release_manifest: list[str],
    limit: int = 3,
) -> list[str]:
    changelog_highlights = extract_wechat_highlights(project_root, tag, limit=limit)
    if changelog_highlights:
        return changelog_highlights
    release_note_highlights = extract_release_note_highlights(release_notes, limit=limit)
    if release_note_highlights:
        return release_note_highlights
    return build_manifest_highlights(release_manifest, limit=limit)


def extract_wechat_highlights(project_root: Path, tag: str, limit: int = 3) -> list[str]:
    changelog_path = project_root / "CHANGELOG.md"
    if not changelog_path.exists():
        return []
    sections = parse_changelog_sections(changelog_path.read_text(encoding="utf-8"))
    for label in (tag.lstrip("v"), "Unreleased"):
        entries = flatten_changelog_entries(sections.get(label, {}))
        highlights = select_wechat_highlights(entries, limit=limit)
        if highlights:
            return highlights
    return []


def parse_changelog_sections(text: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current_label: str | None = None
    current_category: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        section_match = CHANGELOG_SECTION_RE.match(line)
        if section_match:
            current_label = section_match.group("label")
            sections.setdefault(current_label, {category: [] for category in CHANGELOG_CATEGORIES})
            current_category = None
            continue
        if current_label is None:
            continue
        if line.startswith("### "):
            heading = line[4:].strip()
            current_category = heading if heading in CHANGELOG_CATEGORIES else None
            continue
        if current_category is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            sections[current_label][current_category].append(stripped[2:].strip())
            continue
        if stripped and sections[current_label][current_category]:
            sections[current_label][current_category][-1] += f" {stripped}"
    return sections


def flatten_changelog_entries(section: dict[str, list[str]]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for category in ("Changed", "Fixed", "Added"):
        for entry in section.get(category, []):
            entries.append((category, entry))
    return entries


def select_wechat_highlights(entries: list[tuple[str, str]], limit: int) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, (category, entry) in enumerate(entries):
        normalized = normalize_wechat_highlight(entry)
        if not normalized or normalized in seen:
            continue
        score = score_wechat_highlight(raw_entry=entry, normalized_entry=normalized, category=category)
        if score < 1:
            continue
        seen.add(normalized)
        candidates.append((score, index, normalized))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in candidates[:limit]]


def extract_release_note_highlights(release_notes: str, limit: int = 3) -> list[str]:
    if not release_notes.strip():
        return []
    entries: list[tuple[str, str]] = []
    for raw_line in release_notes.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "Full Changelog" in stripped or stripped == "What's Changed":
            continue
        stripped = RELEASE_NOTE_BULLET_RE.sub("", stripped)
        stripped = RELEASE_NOTE_NUMBERED_RE.sub("", stripped)
        stripped = RELEASE_NOTE_PR_SUFFIX_RE.sub("", stripped).strip()
        if len(stripped) < 8:
            continue
        entries.append(("Changed", stripped))
    return select_wechat_highlights(entries, limit=limit)


def build_manifest_highlights(manifest: list[str], limit: int = 3) -> list[str]:
    if not manifest:
        return []
    top_levels = sorted({Path(item).parts[0] for item in manifest if item})
    if not top_levels:
        return []
    preview = "、".join(top_levels[:3])
    if len(top_levels) > 3:
        preview = f"{preview} 等"
    highlights = [f"发布包共整理 {len(manifest)} 项资源，覆盖 {preview}"]
    if "project-publish" in top_levels and "project-install" in top_levels:
        highlights.append("项目级发布与安装脚本会随版本一并交付")
    elif "project-publish" in top_levels and "skills" in top_levels:
        highlights.append("skills 与项目级发布脚本会随版本一起交付")
    return highlights[:limit]


def normalize_wechat_highlight(entry: str) -> str:
    text = entry.replace("`", "")
    if "：" in text:
        prefix, suffix = text.split("：", 1)
        if text.startswith(
            (
                "修复 ",
                "调整 ",
                "重定义 ",
                "新增 ",
                "优化 ",
                "重构 ",
                "放宽 ",
                "补强 ",
                "明确 ",
                "对齐 ",
                "重写 ",
                "审计并修复 ",
                "同步更新 ",
                "补齐 ",
            )
        ) or any(token in prefix for token in CHANGELOG_NOISE_TERMS):
            text = suffix
    replacements = (
        ("private 与 public 发布", "私有/公开发布"),
        ("private 和 public 发布", "私有/公开发布"),
        ("private / public", "private/public"),
        ("project-publish/WeChat.md", "WeChat 归档"),
        ("project-public-package/", "project-public-package"),
        ("project-publish/", "project-publish"),
        ("会插入到文件头部之后，保持“最新版本在前”的倒序记录", "按最新版本在前归档"),
        ("统一追加归档到 WeChat 归档", "自动归档到 WeChat 记录"),
        ("统一追加归档到 project-publish/WeChat.md", "自动归档到 WeChat 记录"),
        ("现在 dry-run dry-run 只预演版本与文案，不再改写工作树", "dry-run 只预演版本与文案，不再改写工作树"),
        ("只推导下一版 tag 并打印微信文案，不再写入工作树", "dry-run 只预演版本与文案，不再改写工作树"),
        ("bug fixes", "修复若干问题"),
        ("new features", "补充新能力"),
        ("不会读取 project-public-package 的生成结果", "不再依赖 public sibling 目录"),
        ("不再读取 project-public-package 的生成结果", "不再依赖 public sibling 目录"),
        ("继续在 shell 中直接打印微信动态文案，并补测试防止后续回归", "继续在终端直接输出微信动态文案"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    if "私有发布和公开发布都允许语义触发" in text and "仓库可见性" in text:
        text = "私有/公开发布统一收口到同一流程，并按仓库可见性区分目标"
    elif "微信动态方案" in text and "归档" in text:
        text = "私有/公开发布会自动生成并归档微信动态"
    elif "按最新版本在前归档" in text:
        text = "微信动态按最新版本在前归档"
    elif "dry-run" in text and "不再改写工作树" in text:
        text = "dry-run 只预演版本与文案，不再改写工作树"
    elif "公开发布仍依赖 sibling public 工作目录" in text or (
        "整仓快照驱动目标仓库" in text and "public sibling 目录" in text
    ):
        text = "公开发布不再依赖 public sibling 目录，直接按整仓快照驱动目标仓库"
    text = re.sub(r"\s+", " ", text).strip("。；， ")
    return text


def score_wechat_highlight(*, raw_entry: str, normalized_entry: str, category: str) -> int:
    benefit_hits = sum(1 for token in CHANGELOG_BENEFIT_TERMS if token in raw_entry)
    noise_hits = sum(1 for token in CHANGELOG_NOISE_TERMS if token in raw_entry)
    if noise_hits and benefit_hits == 0:
        return -100
    category_score = {"Changed": 4, "Fixed": 3, "Added": 2}.get(category, 0)
    score = category_score + benefit_hits * 2 - noise_hits * 3
    if any(token in raw_entry for token in ("现在", "支持", "自动", "不再", "统一", "改为")):
        score += 1
    if len(normalized_entry) > 70:
        score -= 1
    return score


def write_wechat_archive(*, project_root: Path, tag: str, release_url: str, content: str) -> Path:
    archive_path = project_root / "project-publish" / "WeChat.md"
    header = "# WeChat\n"
    release_url_text = release_url or "待生成"
    section = "\n".join(
        [
            f"## {tag}",
            f"- 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Release URL：{release_url_text}",
            "",
            content,
            "",
        ]
    )
    if not archive_path.exists():
        archive_path.write_text(f"{header}\n{section}", encoding="utf-8")
        return archive_path

    current = archive_path.read_text(encoding="utf-8")
    if not current.startswith(header):
        current = f"{header}\n{current.lstrip()}"

    marker = f"## {tag}\n"
    if marker in current:
        before, remainder = current.split(marker, 1)
        next_marker = remainder.find("\n## ")
        current_section = marker + (remainder if next_marker == -1 else remainder[:next_marker])
        normalized_section = "\n".join(
            line for line in current_section.strip().splitlines() if not line.startswith("- 更新时间：")
        ).strip()
        expected_section = "\n".join(
            [
                f"## {tag}",
                f"- Release URL：{release_url_text}",
                "",
                content,
            ]
        ).strip()
        if normalized_section == expected_section:
            return archive_path
        after = "" if next_marker == -1 else remainder[next_marker + 1 :]
        updated = f"{before}{section}"
        if after:
            updated = f"{updated}\n{after.lstrip()}"
        archive_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
        return archive_path

    header_block, body = current.split("\n", 1)
    body = body.strip()
    updated = f"{header_block}\n\n{section}"
    if body:
        updated = f"{updated}\n\n{body}"
    archive_path.write_text(updated.rstrip() + "\n", encoding="utf-8")
    return archive_path


def write_summary(
    *,
    session: PublishSession,
    step_outcomes: list[StepOutcome],
    release_url: str,
    failed_step: str | None,
    failure_reason: str | None,
    publish_complete: bool,
) -> str:
    lines = [
        f"项目：{session.project_info['name']}",
        f"版本：{session.project_info['version']}",
        f"Tag：{session.tag}",
        f"Release URL：{release_url or '未生成'}",
        f"发布完成：{'是' if publish_complete else '否'}",
        f"失败步骤：{failed_step or '无'}",
        f"阻塞原因：{failure_reason or '无'}",
        "",
        "步骤结果：",
    ]
    for outcome in step_outcomes:
        lines.append(f"- {outcome.name}: {outcome.status} - {outcome.message}")
    summary = "\n".join(lines)
    (session.session_dir / "summary.txt").write_text(summary, encoding="utf-8")
    return summary


def is_publish_complete(
    *,
    options: PublishOptions,
    step_outcomes: list[StepOutcome],
    failed_step: str | None,
) -> bool:
    if failed_step is not None or options.dry_run or options.skip_upload or options.pack_only:
        return False
    return any(
        outcome.name == "step4_pack_release" and bool(outcome.details.get("uploaded")) for outcome in step_outcomes
    )


def print_pack_summary(step_outcomes: list[StepOutcome]) -> None:
    for outcome in step_outcomes:
        if outcome.name != "step4_pack_release":
            continue
        pack_script = str(outcome.details.get("pack_script", "project-publish/unknown/pack_release.py"))
        print("\n打包结果：")
        print(f"- {pack_script} 已执行：是")
        print(f"- 单一 skills 发布包：{outcome.details.get('package_path', '未生成')}")
        print(f"- 发布包状态：{outcome.details.get('package_status', 'unknown')}")
        print(f"- Assets 上传成功：{'是' if outcome.details.get('uploaded') else '否'}")
        return
    print("\n打包结果：")
    print("- project-publish/<scope>/pack_release.py 已执行：否")
    print("- Assets 上传成功：否")
