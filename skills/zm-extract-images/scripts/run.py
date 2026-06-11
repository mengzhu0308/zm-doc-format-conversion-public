#!/usr/bin/env python3
"""
extract-images: 从 PDF/Word/PPT 中提取内嵌图片资源
"""

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from typing import Any

try:
    import fitz  # PyMuPDF  # type: ignore[reportMissingImports]
except ImportError:
    fitz = None


# 允许的纯字母数字扩展名（≤8 位），用于把上游 ext 规整到安全范围内
_SAFE_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")

# ZIP 媒体目录白名单：caller 解析 zip member 时必须命中其中之一；用于防路径穿越
_ZIP_MEDIA_PREFIXES = ("word/media/", "ppt/media/")

# 输入文档扩展名白名单：与 SKILL.md "支持的格式" 表保持单一真相；process_directory
# 与 extract_images 共享，新增/废弃格式只改此处
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".pptx")

# 单个媒体文件最大字节数；超出阈值视为可疑/炸弹，跳过（与 caller continue 语义一致）
MAX_MEDIA_BYTES = 100 * 1024 * 1024  # 100 MB

# 退出码：成功 0 / 完全失败 2；退出码 1 拆为"部分失败"（批量有失败但未全挂）
# 与 --on-conflict 行为无关，命名沿用早期 EXIT_FAIL 占位已不再使用
EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ERROR = 2

# 来源 fallback：DOCX/PPTX 解析不到时使用同一字面量
SOURCE_FALLBACK_DOCX = "Word 内容"
SOURCE_FALLBACK_PPTX_SLIDE = "未知幻灯片"
SOURCE_FALLBACK_PDF_PAGE = "未识别页面"


def sanitize_ext(raw_ext: str | None) -> str:
    """清洗图片扩展名；非空且仅含 [A-Za-z0-9]{1,8} 时返回小写值，否则回退 png。"""
    if not raw_ext:
        return "png"
    ext = raw_ext.strip().lstrip(".").lower()
    return ext if _SAFE_EXT_RE.fullmatch(ext) else "png"


def _zip_read_to_file(zf: zipfile.ZipFile, member: str, dest_path: str) -> None:
    """从 zip 直接读取 member 字节并写到 dest_path，不在磁盘上展开中间目录。

    改用 zf.open + 手动写盘，避免 zipfile.extract 在 output_dir 下创建
    word/media/ 或 ppt/media/ 中间目录；显式 dest_path 也规避了路径穿越攻击面。
    """
    # 防路径穿越：member 必须命中 zip 媒体白名单前缀，且不能含 '..' 段
    if ".." in member.replace("\\", "/").split("/"):
        raise ValueError(f"非法 zip member（包含 .. 段）：{member!r}")
    if not any(member.startswith(prefix) for prefix in _ZIP_MEDIA_PREFIXES):
        raise ValueError(f"非媒体目录的 zip member：{member!r}")

    # 大文件炸弹防护：超阈值直接 raise 让 caller 走 continue
    info = zf.getinfo(member)
    if info.file_size > MAX_MEDIA_BYTES:
        raise ValueError(
            f"zip member 体积 {info.file_size} 字节超过阈值 {MAX_MEDIA_BYTES}：{member!r}"
        )

    with zf.open(member) as src, open(dest_path, "wb") as dst:
        shutil.copyfileobj(src, dst)


def error_exit(
    code: str,
    message: str,
    exit_code: int = EXIT_ERROR,
    json_payload: dict[str, Any] | None = None,
) -> None:
    """输出错误并以指定退出码退出（默认 2，区别于 partial_success 的 1）。

    当 caller 在 --json 模式下需要消费结构化错误时，传入 json_payload；本函数
    会把 payload 写到 stdout（程序可解析），再以非零退出码退出。
    """
    if json_payload is not None:
        print(json.dumps(json_payload, ensure_ascii=False, indent=2))
    else:
        print(f"Error [{code}]: {message}", file=sys.stderr)
    sys.exit(exit_code)


def generate_index_md(source_file: str, images: list[dict[str, Any]]) -> str:
    """生成 index.md 内容。"""
    lines = [
        f"# 图片索引: {os.path.basename(source_file)}",
        "",
        "## 图片列表",
        "",
        "| # | 文件名 | 来源 | 尺寸 |",
        "|---|--------|------|------|",
    ]

    for i, img in enumerate(images, 1):
        filename = img["filename"]
        source = img["source"]  # 必填，调用方已统一 fallback
        size = ""
        if img.get("width") and img.get("height"):
            size = f"{img['width']}x{img['height']}"

        lines.append(f"| {i} | {filename} | {source} | {size} |")

    lines.append("")
    lines.append(f"- 共提取 {len(images)} 张图片")

    return "\n".join(lines)


def extract_images_from_pdf(file_path: str, output_dir: str, verbose: bool = False) -> dict[str, Any]:
    """从 PDF 提取图片"""
    if fitz is None:
        return {
            "status": "import_failed",
            "message": "PyMuPDF (fitz) 未安装，请运行: pip install pymupdf",
            "path": file_path,
        }

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return {"status": "extraction_failed", "message": f"无法打开 PDF: {e}"}

    try:
        # 第一遍：收集所有图片信息
        seen_xrefs: set[int] = set()
        collected_images = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            img_list = page.get_images()
            page_label = f"第 {page_num + 1} 页"

            for img in img_list:
                xref = img[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                try:
                    base_image = doc.extract_image(xref)
                    collected_images.append({
                        "image_bytes": base_image["image"],
                        "ext": sanitize_ext(base_image["ext"] or "png"),
                        "source": page_label,
                        "page": page_num + 1,
                        "width": base_image.get("width"),
                        "height": base_image.get("height"),
                    })

                except Exception as e:
                    if verbose:
                        print(f"  警告: 提取图片 {xref} 失败: {e}")
                    continue

        # 计算填充宽度
        total_images = len(collected_images)
        width = len(str(total_images)) if total_images > 0 else 1

        # 第二遍：保存并重命名
        images = []
        os.makedirs(output_dir, exist_ok=True)

        for i, img_info in enumerate(collected_images, 1):
            image_filename = f"image-{i:0{width}d}.{img_info['ext']}"
            output_path = os.path.join(output_dir, image_filename)

            with open(output_path, "wb") as f:
                f.write(img_info["image_bytes"])

            images.append({
                "filename": image_filename,
                "path": output_path,
                "source": img_info["source"],
                "page": img_info["page"],
                "width": img_info["width"],
                "height": img_info["height"],
                "ext": img_info["ext"]
            })

            if verbose:
                print(f"  提取: {image_filename} ({img_info['source']}, {img_info['width']}x{img_info['height']})")

        # 生成 index.md
        if images:
            index_path = os.path.join(output_dir, "index.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(generate_index_md(file_path, images))

        return {
            "status": "success",
            "images_count": len(images),
            "images": images
        }
    finally:
        # 任何路径下都要关闭 PDF 文档，避免文件描述符泄漏
        doc.close()


def extract_images_from_docx(file_path: str, output_dir: str, verbose: bool = False) -> dict[str, Any]:
    """从 Word 文档提取图片"""
    try:
        zf = zipfile.ZipFile(file_path, 'r')
    except zipfile.BadZipFile:
        return {"status": "extraction_failed", "message": "文件不是有效的 DOCX 格式"}
    except Exception as e:
        return {"status": "extraction_failed", "message": f"无法读取 DOCX: {e}"}

    try:
        media_files = [f for f in zf.namelist() if f.startswith('word/media/')]

        if not media_files:
            return {"status": "success", "images_count": 0, "images": []}

        # 计算填充宽度
        total_images = len(media_files)
        width = len(str(total_images)) if total_images > 0 else 1

        images = []
        os.makedirs(output_dir, exist_ok=True)

        for i, media_path in enumerate(media_files, 1):
            try:
                original_filename = os.path.basename(media_path)
                ext = sanitize_ext(os.path.splitext(original_filename)[1].lstrip('.'))
                image_filename = f"image-{i:0{width}d}.{ext}"
                output_path = os.path.join(output_dir, image_filename)

                # 直接从 zip 读取并写到目标路径，避免在 output_dir 下展开 word/media/ 中间目录
                _zip_read_to_file(zf, media_path, output_path)

                images.append({
                    "filename": image_filename,
                    "path": output_path,
                    "source": SOURCE_FALLBACK_DOCX,
                    "ext": ext
                })

                if verbose:
                    print(f"  提取: {image_filename}")

            except Exception as e:
                if verbose:
                    print(f"  警告: 提取 {media_path} 失败: {e}")
                continue

        # 生成 index.md
        if images:
            index_path = os.path.join(output_dir, "index.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(generate_index_md(file_path, images))

        return {
            "status": "success",
            "images_count": len(images),
            "images": images
        }
    finally:
        zf.close()


def _build_pptx_media_slide_map(zf: zipfile.ZipFile) -> tuple[dict[str, str], int]:
    """建立 media 文件名到幻灯片编号的映射。

    使用 xml.etree.ElementTree 解析 `ppt/slides/_rels/slideN.xml.rels`，
    从 <Relationship> 元素中读取 Target 属性。忽略无法解析的 rels 片段；
    返回值第二项为解析失败计数，供 caller 决定是否提示用户。
    """
    from xml.etree import ElementTree as ET

    media_to_slide: dict[str, str] = {}
    parse_failed = 0

    # 查找所有幻灯片关系文件: ppt/slides/_rels/slide*.xml.rels
    slide_rels = [f for f in zf.namelist() if f.startswith('ppt/slides/_rels/') and f.endswith('.xml.rels')]

    for rels_path in sorted(slide_rels):
        # 从文件名提取幻灯片编号，如 ppt/slides/_rels/slide1.xml.rels -> 1
        rels_basename = os.path.basename(rels_path)  # slide1.xml.rels
        if not rels_basename.startswith('slide') or not rels_basename.endswith('.xml.rels'):
            continue
        slide_num = rels_basename[len('slide'):-len('.xml.rels')]  # "1"
        try:
            slide_label = f"幻灯片 {int(slide_num)}"
        except ValueError:
            continue

        try:
            rels_content = zf.read(rels_path)
            root = ET.fromstring(rels_content)
        except (ET.ParseError, UnicodeDecodeError, OSError):
            parse_failed += 1
            continue

        # 关系文件根节点是 <Relationships>，子元素是 <Relationship>，Target 属性是图片相对路径
        for rel in root.findall("{*}Relationship"):
            target = rel.get("Target")
            if not target:
                continue
            # Target 既可能写 "ppt/media/x.png" 也可能写 "../media/x.png"，都归一为 basename
            if "media/" not in target:
                continue
            media_name = os.path.basename(target)
            if not media_name:
                continue
            # 同一 media 跨多张幻灯片时，按最小编号优先
            media_to_slide.setdefault(media_name, slide_label)

    return media_to_slide, parse_failed


def extract_images_from_pptx(file_path: str, output_dir: str, verbose: bool = False) -> dict[str, Any]:
    """从 PowerPoint 提取图片"""
    try:
        zf = zipfile.ZipFile(file_path, 'r')
    except zipfile.BadZipFile:
        return {"status": "extraction_failed", "message": "文件不是有效的 PPTX 格式"}
    except Exception as e:
        return {"status": "extraction_failed", "message": f"无法读取 PPTX: {e}"}

    try:
        media_files = [f for f in zf.namelist() if f.startswith('ppt/media/')]
        # 建立 media -> slide 的映射；rels_parse_failed 供 verbose 提示与 JSON 字段
        media_slide_map, rels_parse_failed = _build_pptx_media_slide_map(zf)
        if rels_parse_failed and verbose:
            print(f"  警告: rels 解析失败 {rels_parse_failed} 个文件")

        if not media_files:
            return {
                "status": "success",
                "images_count": 0,
                "images": [],
                "rels_parse_failed": rels_parse_failed,
            }

        # 计算填充宽度
        total_images = len(media_files)
        width = len(str(total_images)) if total_images > 0 else 1

        images = []
        os.makedirs(output_dir, exist_ok=True)

        for i, media_path in enumerate(media_files, 1):
            try:
                original_filename = os.path.basename(media_path)
                ext = sanitize_ext(os.path.splitext(original_filename)[1].lstrip('.'))
                image_filename = f"image-{i:0{width}d}.{ext}"
                output_path = os.path.join(output_dir, image_filename)

                # 直接从 zip 读取并写到目标路径，避免在 output_dir 下展开 ppt/media/ 中间目录
                _zip_read_to_file(zf, media_path, output_path)

                # 查找对应的幻灯片编号
                slide_label = media_slide_map.get(original_filename, SOURCE_FALLBACK_PPTX_SLIDE)

                images.append({
                    "filename": image_filename,
                    "path": output_path,
                    "source": slide_label,
                    "ext": ext
                })

                if verbose:
                    print(f"  提取: {image_filename} (来源: {slide_label})")

            except Exception as e:
                if verbose:
                    print(f"  警告: 提取 {media_path} 失败: {e}")
                continue

        # 生成 index.md
        if images:
            index_path = os.path.join(output_dir, "index.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(generate_index_md(file_path, images))

        return {
            "status": "success",
            "images_count": len(images),
            "images": images,
            "rels_parse_failed": rels_parse_failed,
        }
    finally:
        zf.close()


def extract_images(file_path: str, output_dir: str, verbose: bool = False) -> dict[str, Any]:
    """根据文件类型提取图片。

    output_dir 由调用方预先拼成最终目录（含 {basename}_assets）。
    内部不再做二次拼装，避免上层与本层重复嵌套。
    """
    file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        return {
            "status": "missing_input",
            "message": f"文件不存在: {os.path.basename(file_path)}",
            "path": file_path,
        }

    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.pdf':
        return extract_images_from_pdf(file_path, output_dir, verbose)
    elif ext == '.docx':
        return extract_images_from_docx(file_path, output_dir, verbose)
    elif ext == '.pptx':
        return extract_images_from_pptx(file_path, output_dir, verbose)
    elif ext in ['.doc', '.ppt']:
        return {
            "status": "not_supported",
            "message": f"不支持 {ext} 格式，仅支持 .docx/.pptx",
            "path": file_path,
        }
    else:
        return {
            "status": "not_supported",
            "message": f"不支持的文件格式: {ext}",
            "path": file_path,
        }


def process_directory(dir_path: str, output_dir: str | None = None, verbose: bool = False) -> dict[str, Any]:
    """批量处理目录中的所有支持的文件"""
    dir_path = os.path.abspath(dir_path)

    if not os.path.isdir(dir_path):
        return {
            "status": "missing_input",
            "message": f"目录不存在: {os.path.basename(dir_path) or dir_path}",
            "path": dir_path,
        }

    supported_extensions = SUPPORTED_EXTENSIONS
    files = []

    for entry in os.scandir(dir_path):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in supported_extensions:
                files.append(entry.path)

    if not files:
        return {
            "status": "empty_input_dir",
            "message": "目录中没有支持的文档文件",
            "path": dir_path,
        }

    results = []
    total_images = 0
    failed_files: list[dict[str, str]] = []

    for file_path in sorted(files):
        if verbose:
            print(f"\n处理: {file_path}")

        file_basename = os.path.basename(file_path)
        base_name = os.path.splitext(file_basename)[0]
        if output_dir:
            file_output_dir = os.path.join(output_dir, f"{base_name}_assets")
        else:
            file_output_dir = os.path.join(os.path.dirname(file_path), f"{base_name}_assets")

        result = extract_images(file_path, file_output_dir, verbose)
        result["source_file"] = file_path
        results.append(result)

        if result["status"] == "success":
            total_images += result.get("images_count", 0)
        else:
            failed_files.append({
                "file": file_basename,
                "path": file_path,
                "status": result["status"],
                "message": result.get("message", ""),
            })

    return {
        "status": "success" if not failed_files else "partial_success",
        "files_processed": len(files),
        "files_failed": len(failed_files),
        "total_images_extracted": total_images,
        "failed_files": failed_files,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="从 PDF、Word、PowerPoint 文档中提取内嵌图片资源"
    )
    parser.add_argument("--path", required=True, help="输入文档文件或包含文档的目录")
    parser.add_argument("--output-dir", help="输出目录（不指定则写到源文件同目录）")
    parser.add_argument(
        "--on-conflict",
        choices=["overwrite"],
        default="overwrite",
        help="当输出目录已有同名文件时的策略；当前仅支持 overwrite（直接覆盖）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    parser.add_argument("--verbose", action="store_true", help="显示详细处理信息")

    args = parser.parse_args()

    path = os.path.abspath(args.path)

    if not os.path.exists(path):
        json_payload = None
        if args.json:
            json_payload = {
                "status": "missing_input",
                "message": f"路径不存在: {os.path.basename(path) or path}",
                "path": path,
            }
        error_exit(
            "missing_input",
            f"路径不存在: {os.path.basename(path) or path}",
            json_payload=json_payload,
        )

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    if os.path.isdir(path):
        result = process_directory(path, args.output_dir, args.verbose)
    else:
        base_name = os.path.splitext(os.path.basename(path))[0]
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            output_dir = os.path.join(args.output_dir, f"{base_name}_assets")
        else:
            output_dir = os.path.join(os.path.dirname(path), f"{base_name}_assets")

        result = extract_images(path, output_dir, args.verbose)
        result["source_file"] = path

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["status"] in ("success", "partial_success"):
            if "total_images_extracted" in result:
                print(
                    f"\n完成！共处理 {result['files_processed']} 个文件，"
                    f"提取 {result['total_images_extracted']} 张图片"
                )
                # 批量模式汇总失败文件
                if result.get("failed_files"):
                    print(f"\n以下 {result['files_failed']} 个文件未成功提取：")
                    for f in result["failed_files"]:
                        msg = f" - {f['file']} [{f['status']}] {f['message']}".rstrip()
                        print(msg)
            else:
                print(f"\n完成！提取 {result.get('images_count', 0)} 张图片")
                for img in result.get("images", []):
                    print(f"  {img['filename']} -> {img['path']}")
                # 提示 index.md 位置
                if result.get("images"):
                    output_dir = os.path.dirname(result["images"][0]["path"])
                    index_path = os.path.join(output_dir, "index.md")
                    print(f"  索引文件 -> {index_path}")
        else:
            error_exit(result["status"], result.get("message", "未知错误"), exit_code=EXIT_ERROR)

    # 退出码：成功 0 / 部分失败 1 / 完全失败 2
    if result["status"] == "partial_success":
        sys.exit(EXIT_PARTIAL)
    if result["status"] == "success":
        sys.exit(EXIT_OK)
    sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()
