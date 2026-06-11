#!/usr/bin/env python3
"""
zm-pdf2image: 将 PDF 转换为图片（PNG/JPG）
支持单文件和目录下批量转换。
"""

import argparse
import hashlib
import json
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import NamedTuple


# 单点格式映射：扩展名与 Pillow 内部格式都从这里取，避免分散判断。
FORMAT_MAP: dict[str, tuple[str, str]] = {
    "png": ("PNG", "png"),
    "jpg": ("JPEG", "jpg"),
}

# Windows 保留名（按 stem 命中；不区分大小写）。
_WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class Result(NamedTuple):
    status: str
    message: str
    files_created: list[str]
    details: dict


def safe_filename(name: str) -> str:
    """将字符串转换为安全的文件系统名字。

    - 去除控制字符
    - 去首尾的 Unicode 空白（覆盖 NBSP/ZWSP 等）+ 尾随的 `.` 与空格
    - 命中 Windows 保留名时追加 `_pdf` 后缀
    - 全部清空时回退到基于原始字符串的哈希名
    """
    cleaned = "".join(
        c for c in name
        if unicodedata.category(c)[0] != "C" or c in ("_", "-")
    ).rstrip(" .")
    cleaned = cleaned.strip()  # P2-4：覆盖 NBSP/ZWSP 等 Unicode 空白
    if cleaned:
        if cleaned.upper() in _WINDOWS_RESERVED_STEMS:
            cleaned = f"{cleaned}_pdf"
        return cleaned
    # 全部被清掉：回退到不可逆的命名，避免把控制字符写到目录
    digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"unnamed_pdf_{digest}"


def get_pdf_files(path: str) -> list[Path]:
    """获取路径下的所有 PDF 文件（不递归）。"""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() == ".pdf":
            return [p]
        else:
            return []
    elif p.is_dir():
        try:
            entries = list(p.iterdir())
        except OSError:
            return []
        return sorted(
            f for f in entries
            if f.suffix.lower() == ".pdf" and f.is_file()
        )
    else:
        return []


# 可注入的 convert_from_path（默认延迟导入真实实现；测试可在主进程 monkeypatch）。
# 模块级变量允许 _smoke.py 等同进程直接替换；跨进程调用不会继承 monkeypatch，
# 但子进程本来就是从 CLI 拉起，不依赖此注入点。
def _default_convert_from_path():
    from pdf2image import convert_from_path
    return convert_from_path


_convert_from_path = _default_convert_from_path  # type: ignore[assignment]


def _classify_convert_error(exc: Exception) -> tuple[str, str]:
    """把 convert_from_path 抛出的异常分类为状态码 + 用户可读 message。"""
    # P1-3 优先：subprocess.TimeoutExpired → timeout
    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            "timeout",
            f"转换超时（超过设定的 timeout 秒）: {exc}",
        )
    # P1-2 已知 pdf2image 异常类型
    pdf2image_exc_types: tuple[type, ...] = ()
    try:
        from pdf2image.exceptions import (  # type: ignore[import-not-found]
            PDFInfoNotInstalledError,
            PDFPageCountError,
            PDFSyntaxError,
        )
        pdf2image_exc_types = (PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError)
    except ImportError:
        pass
    if pdf2image_exc_types and isinstance(exc, pdf2image_exc_types):
        return (
            "poppler_missing",
            "poppler 系统库未安装或不可用；Linux 请执行 `sudo apt install -y poppler-utils`，"
            "macOS 请执行 `brew install poppler`，Windows 无需额外安装。",
        )
    # 兜底：pdftoppm 子进程失败
    if isinstance(exc, subprocess.CalledProcessError):
        return (
            "poppler_missing",
            f"poppler 子进程失败 (returncode={exc.returncode})：{exc}",
        )
    # 兜底：文本特征
    text = (str(exc) or "").lower()
    if "poppler" in text or "pdftoppm" in text:
        return (
            "poppler_missing",
            "poppler 系统库未安装；Linux 请执行 `sudo apt install -y poppler-utils`，"
            "macOS 请执行 `brew install poppler`，Windows 无需额外安装。",
        )
    return ("conversion_failed", f"pdf2image 转换失败: {exc}")


def convert_pdf(
    pdf_path: Path,
    output_dir: Path,
    img_format: str,
    dpi: int,
    cropbox: bool,
    timeout: int = 60,
) -> Result:
    """转换单个 PDF 文件为图片。失败时回滚已写入的文件与空目录。"""
    if img_format not in FORMAT_MAP:
        return Result(
            status="invalid_output_format",
            message=f"不支持的输出格式: {img_format}（仅支持 {sorted(FORMAT_MAP.keys())}）",
            files_created=[],
            details={"pdf": str(pdf_path), "format": img_format},
        )

    try:
        convert_from_path = _convert_from_path()
        images = convert_from_path(
            str(pdf_path), dpi=dpi, use_cropbox=cropbox, timeout=timeout,
        )
    except ImportError:
        return Result(
            status="import_failed",
            message="pdf2image 未安装，请确认 agent-skills 环境已正确安装（需 pdf2image 和 Pillow）",
            files_created=[],
            details={},
        )
    except Exception as e:
        status, message = _classify_convert_error(e)
        return Result(
            status=status,
            message=message,
            files_created=[],
            details={"pdf": str(pdf_path)},
        )

    stem = safe_filename(pdf_path.stem)
    pillow_format, ext = FORMAT_MAP[img_format]

    # 0 页 PDF：不创建子目录，直接返回成功 + 0 页
    if not images:
        return Result(
            status="success",
            message="PDF 为 0 页，已跳过",
            files_created=[],
            details={"pdf": str(pdf_path), "pages": 0},
        )

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

    created: list[Path] = []
    for i, img in enumerate(images, start=1):
        page_name = f"image-{i:0{width}d}.{ext}"
        out_path = pdf_dir / page_name
        try:
            img.save(str(out_path), format=pillow_format)
            created.append(out_path)
        except Exception as e:
            # 失败回滚：清理已写入文件 + 空目录
            for f in created:
                try:
                    f.unlink()
                except OSError:
                    pass
            # 目录若为空则尝试删除
            try:
                pdf_dir.rmdir()
            except OSError:
                pass
            return Result(
                status="save_failed",
                message=f"保存第 {i} 页失败: {e}",
                files_created=[str(p) for p in created],
                details={"pdf": str(pdf_path), "page": i, "rolled_back": True},
            )

    return Result(
        status="success",
        message=f"转换成功，共 {total_pages} 页",
        files_created=[str(p) for p in created],
        details={"pdf": str(pdf_path), "pages": total_pages},
    )


# 状态码 ↔ 退出码 集中映射：
# - success → 0
# - partial → 2
# - 其余 12 个失败状态统一 → 1
STATUS_EXIT_CODES: dict[str, int] = {
    "success": 0,
    "partial": 2,
    "missing_input": 1,
    "not_a_pdf": 1,
    "empty_input_dir": 1,
    "import_failed": 1,
    "conversion_failed": 1,
    "save_failed": 1,
    "output_dir_create_failed": 1,
    "poppler_missing": 1,
    "permission_denied": 1,
    "invalid_output_format": 1,
    "invalid_dpi": 1,
    "timeout": 1,
}


def run(
    path: str,
    output_dir: str | None,
    img_format: str,
    dpi: int,
    cropbox: bool,
    timeout: int = 60,
) -> dict:
    """
    核心运行函数。
    返回结构化结果字典，便于上层流程复用。
    """
    # 预检 0：img_format 合法性。
    # main() 入口受 argparse choices 限制不会触发，但 run() 是模块公共 API
    # （docstring 写"便于上层流程复用"），调用方传非法格式应直接拿到 invalid_output_format，
    # 而不是先被 missing_input / not_a_pdf 等其它状态码分流。
    if img_format not in FORMAT_MAP:
        return {
            "result_status": "invalid_output_format",
            "summary": f"不支持的输出格式: {img_format}（仅支持 {sorted(FORMAT_MAP.keys())}）",
            "targets": [],
            "details": {"format": img_format},
        }

    # 预检 0.5：dpi 类型与范围（P1-1 invalid_dpi）。
    if not isinstance(dpi, int) or isinstance(dpi, bool) or dpi <= 0:
        return {
            "result_status": "invalid_dpi",
            "summary": f"非法 DPI: {dpi}（必须为正整数）",
            "targets": [],
            "details": {"dpi": dpi},
        }
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        return {
            "result_status": "invalid_dpi",
            "summary": f"非法 timeout: {timeout}（必须为正整数）",
            "targets": [],
            "details": {"timeout": timeout},
        }

    # 预检 1：Pillow 单独缺失的诊断。
    # 若 Pillow 未装而 pdf2image 已装，convert_from_path 内部抛 ImportError 会被错误归到 conversion_failed。
    # 提前在 run() 入口预检，失败时直接返回 import_failed。
    try:
        from PIL import Image  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return {
            "result_status": "import_failed",
            "summary": "Pillow 未安装，请确认 agent-skills 环境已正确安装（需 pdf2image 和 Pillow）",
            "targets": [],
        }

    path_p = Path(path)

    # 预检 2：路径存在
    if not path_p.exists():
        return {
            "result_status": "missing_input",
            "summary": f"输入路径不存在: {path}",
            "targets": [],
        }

    pdf_files = get_pdf_files(path)

    # P0-2 预检 3：单文件路径无读权限（chmod 000）应识别为 permission_denied。
    # get_pdf_files 对 is_file() 路径不会触发 PermissionError，仅按 suffix 返回 [p]；
    # 需在 run() 入口对单文件路径做最小化权限预检（不消费内容）。
    if path_p.is_file():
        try:
            with path_p.open("rb"):
                pass
        except PermissionError as e:
            return {
                "result_status": "permission_denied",
                "summary": f"无法读取输入文件 {path}: {e}",
                "targets": [],
            }

    # 路径存在但不可读（权限缺失）时，get_pdf_files 已吞掉 PermissionError；
    # 此时空列表区分"空目录"和"权限缺失"用父目录的可读性再判断一次。
    if not pdf_files:
        if path_p.is_dir():
            try:
                list(path_p.iterdir())
            except OSError as e:
                return {
                    "result_status": "permission_denied",
                    "summary": f"无法读取输入目录 {path}: {e}",
                    "targets": [],
                }
        if path_p.is_file() and path_p.suffix.lower() != ".pdf":
            return {
                "result_status": "not_a_pdf",
                "summary": f"输入文件不是 PDF: {path}",
                "targets": [],
            }
        if path_p.is_dir():
            return {
                "result_status": "empty_input_dir",
                "summary": f"目录中没有 PDF 文件: {path}",
                "targets": [],
            }

    # 确定输出目录
    if output_dir:
        out_dir = Path(output_dir)
    elif path_p.is_file():
        out_dir = path_p.parent
    else:
        out_dir = path_p

    # 创建输出目录
    if not out_dir.exists():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {
                "result_status": "output_dir_create_failed",
                "summary": f"无法创建输出目录: {e}",
                "targets": [],
            }

    # 转换
    targets = []
    all_created = []
    total_pages = 0
    zero_page_count = 0  # P1-4：跟踪 0 页 PDF 数量，避免"成功 X 个，共 0 页"的自相矛盾

    for pdf_file in pdf_files:
        result = convert_pdf(pdf_file, out_dir, img_format, dpi, cropbox, timeout=timeout)
        targets.append({
            "source": str(pdf_file),
            "status": result.status,
            "message": result.message,
            "output_files": result.files_created,
            "details": result.details,
        })
        all_created.extend(result.files_created)
        pages = result.details.get("pages", 0)
        total_pages += pages
        if result.status == "success" and pages == 0:
            zero_page_count += 1

    # 汇总
    success_count = sum(1 for t in targets if t["status"] == "success")
    failed_count = len(targets) - success_count

    summary_parts = []
    if success_count > 0:
        suffix = f"（其中 {zero_page_count} 个 0 页跳过）" if zero_page_count > 0 else ""
        summary_parts.append(f"成功转换 {success_count} 个 PDF{suffix}，共 {total_pages} 页")
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


def main():
    parser = argparse.ArgumentParser(
        description="将 PDF 转换为图片（PNG/JPG），支持单文件和批量目录转换。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --path demo.pdf                  # 单文件转 PNG
  %(prog)s --path demo.pdf --format jpg     # 单文件转 JPG
  %(prog)s --path demo.pdf --output-dir out/  # 指定输出目录
  %(prog)s --path pdfs/                      # 批量转换目录
  %(prog)s --path pdfs/ --dpi 300            # 高清转换
  %(prog)s --path demo.pdf --cropbox         # 按 CropBox 裁切（默认按 MediaBox 全幅渲染）
        """,
    )
    parser.add_argument("--path", required=True, help="输入 PDF 文件或包含 PDF 的目录")
    parser.add_argument("--output-dir", help="输出目录（默认写回源文件同目录）")
    parser.add_argument(
        "--format",
        choices=list(FORMAT_MAP.keys()),
        default="png",
        help="输出格式（默认 png）",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="图片 DPI，越高越清晰（默认 300）",
    )
    parser.add_argument(
        "--cropbox",
        action="store_true",
        help="按 PDF 的 CropBox 裁切；默认按 MediaBox 全幅渲染，避免丢内容",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="单 PDF 转换超时（秒），默认 60",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细日志",
    )

    args = parser.parse_args()

    result = run(args.path, args.output_dir, args.format, args.dpi, args.cropbox, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-pdf2image 转换结果")
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

    # 退出码：直接查 STATUS_EXIT_CODES；缺失时按"失败"处理（非 0）
    sys.exit(STATUS_EXIT_CODES.get(result["result_status"], 1))


if __name__ == "__main__":
    main()
