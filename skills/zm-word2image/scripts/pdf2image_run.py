#!/usr/bin/env python3
"""
pdf2image 转换逻辑（bundled 在 zm-word2image skill 内）
主调用方是 `run.py`：第一步 Word→PDF 之后，由 run.py 子进程调用本脚本完成 PDF→图片。
CLI 入口（`python pdf2image_run.py ...`）仅供调试或独立 PDF→图片场景。
"""

import argparse
import json
import sys
from pathlib import Path

from _common import Result, read_skill_version, safe_filename, validate_output_dir


def get_pdf_files(path: str) -> list[Path]:
    """获取路径下的所有 PDF 文件（不递归）。"""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() == ".pdf":
            return [p]
        return []
    if p.is_dir():
        return sorted([f for f in p.iterdir() if f.suffix.lower() == ".pdf" and f.is_file()])
    return []


# --format 大小写兼容：接受 png / PNG / jpg / JPG / jpeg / JPEG（B-P2-1）


def _validate_format(value: str) -> str:
    """--format 校验：忽略大小写，归一化为 png 或 jpg。"""
    v = value.lower()
    if v == "jpeg":
        v = "jpg"
    if v not in {"png", "jpg"}:
        raise argparse.ArgumentTypeError(
            f"--format 必须是 png 或 jpg，收到 {value!r}"
        )
    return v


def convert_pdf(pdf_path: Path, output_dir: Path, img_format: str, dpi: int) -> Result:
    """转换单个 PDF 文件为图片。"""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return Result(
            status="import_failed",
            message="pdf2image 未安装，请确认 agent-skills 环境已正确安装（需 pdf2image 和 Pillow）",
            files_created=[],
            details={},
        )

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as e:
        return Result(
            status="conversion_failed",
            message=f"pdf2image 转换失败: {e}",
            files_created=[],
            details={"pdf": str(pdf_path)},
        )

    stem = safe_filename(pdf_path.stem)
    ext = "jpg" if img_format == "jpg" else "png"

    pdf_dir = output_dir / stem
    try:
        pdf_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return Result(
            status="output_dir_create_failed",
            message=f"无法创建输出子目录 {pdf_dir}: {e}",
            files_created=[],
            details={"pdf": str(pdf_path)},
        )

    total_pages = len(images)
    width = len(str(total_pages)) if total_pages > 0 else 1

    created: list[str] = []
    for i, img in enumerate(images, start=1):
        page_name = f"image-{i:0{width}d}.{ext}"
        out_path = pdf_dir / page_name
        try:
            pillow_format = "JPEG" if img_format.upper() == "JPG" else img_format.upper()
            img.save(str(out_path), format=pillow_format)
            created.append(str(out_path))
        except Exception as e:
            return Result(
                status="save_failed",
                message=f"保存第 {i} 页失败: {e}",
                files_created=created,
                details={"pdf": str(pdf_path), "page": i},
            )

    return Result(
        status="success",
        message=f"转换成功，共 {len(images)} 页",
        files_created=created,
        details={"pdf": str(pdf_path), "pages": len(images)},
    )


def run(path: str, output_dir: str | None, img_format: str, dpi: int) -> dict:
    """核心运行函数，返回结构化结果字典。"""
    path_p = Path(path)

    if not path_p.exists():
        return {
            "result_status": "missing_input",
            "summary": f"输入路径不存在: {path}",
            "targets": [],
        }

    pdf_files = get_pdf_files(path)

    if path_p.is_file() and path_p.suffix.lower() != ".pdf":
        return {
            "result_status": "not_a_pdf",
            "summary": f"输入文件不是 PDF: {path}",
            "targets": [],
        }

    if path_p.is_dir() and not pdf_files:
        return {
            "result_status": "empty_input_dir",
            "summary": f"目录中没有 PDF 文件: {path}",
            "targets": [],
        }

    if output_dir:
        out_dir = Path(output_dir)
    elif path_p.is_file():
        out_dir = path_p.parent
    else:
        out_dir = path_p

    if not out_dir.exists():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {
                "result_status": "output_dir_create_failed",
                "summary": f"无法创建输出目录: {e}",
                "targets": [],
            }

    targets: list[dict] = []
    all_created: list[str] = []
    total_pages = 0

    for pdf_file in pdf_files:
        result = convert_pdf(pdf_file, out_dir, img_format, dpi)
        targets.append({
            "source": str(pdf_file),
            "status": result.status,
            "message": result.message,
            "output_files": result.files_created,
            "details": result.details,
        })
        all_created.extend(result.files_created)
        total_pages += result.details.get("pages", 0)

    success_count = sum(1 for t in targets if t["status"] == "success")
    failed_count = len(targets) - success_count

    summary_parts: list[str] = []
    if success_count > 0:
        summary_parts.append(f"成功转换 {success_count} 个 PDF，共 {total_pages} 页")
    if failed_count > 0:
        summary_parts.append(f"失败 {failed_count} 个")
    if all_created:
        summary_parts.append(f"输出文件: {len(all_created)} 个")

    return {
        "result_status": "success" if failed_count == 0 else "partial",
        "summary": "；".join(summary_parts) if summary_parts else "完成",
        "targets": targets,
        "files_created": all_created,
        "total_pages": total_pages,
    }


def cli_main():
    parser = argparse.ArgumentParser(
        description="[调试/独立使用] 将 PDF 转换为图片（PNG/JPG），支持单文件和批量目录转换。"
        " 主调用方是 run.py（zm-word2image）；直接调用本脚本仅用于调试或独立 PDF→图片。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --path demo.pdf                  # 单文件转 PNG
  %(prog)s --path demo.pdf --format jpg     # 单文件转 JPG
  %(prog)s --path demo.pdf --output-dir out/  # 指定输出目录
  %(prog)s --path pdfs/                      # 批量转换目录
  %(prog)s --path pdfs/ --dpi 300            # 高清转换
        """,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"zm-word2image {read_skill_version()} (pdf2image_run 子入口)",
    )
    parser.add_argument("--path", required=True, help="输入 PDF 文件或包含 PDF 的目录")
    parser.add_argument(
        "--output-dir",
        type=validate_output_dir,
        help="输出目录（默认写回源文件同目录；不允许写入系统敏感目录及其子路径）",
    )
    parser.add_argument(
        "--format",
        type=_validate_format,
        default="png",
        help="输出格式（默认 png；接受 png / PNG / jpg / JPG / jpeg / JPEG）",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="图片 DPI，越高越清晰（默认 300）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )

    args = parser.parse_args()
    result = run(args.path, args.output_dir, args.format, args.dpi)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"pdf2image 转换结果")
        print(f"{'='*50}")
        print(f"状态: {result['result_status']}")
        print(f"汇总: {result['summary']}")
        if result.get("targets"):
            print(f"\n详细:")
            for t in result["targets"]:
                print(f"  来源: {t['source']}")
                print(f"  状态: {t['status']} - {t['message']}")
                if t["output_files"]:
                    print(f"  输出: {t['output_files'][0]}" + (" ..." if len(t["output_files"]) > 1 else ""))
                print()
        if result.get("files_created"):
            print(f"共生成 {len(result['files_created'])} 个图片文件")

    if result["result_status"] == "success":
        sys.exit(0)
    if result["result_status"] == "partial":
        sys.exit(2)
    sys.exit(1)


if __name__ == "__main__":
    cli_main()
