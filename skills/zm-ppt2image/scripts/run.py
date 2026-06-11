#!/usr/bin/env python3
"""
zm-ppt2image: 将 PPT/PPTX 转换为图片（PNG/JPG）
支持单文件和目录下批量转换。

流程：LibreOffice PPT→PDF（存 /tmp/ppt/） → pdf2image PDF→图片
PDF→图片逻辑直接内嵌在当前脚本中，无跨 skill 依赖。
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

# 模块级常量：SKILL.md / README.md 通过引用保持单一真相
CLEAN_TMP_DAYS_DEFAULT = 7
CLEAN_TMP_DAYS_MIN = 0
CLEAN_TMP_DAYS_MAX = 3650
SECONDS_PER_DAY = 86400
LIBREOFFICE_TIMEOUT = 120

PDF2IMAGE_AVAILABLE = importlib.util.find_spec("pdf2image") is not None
PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


class Result(NamedTuple):
    status: str
    message: str
    files_created: list[str]
    details: dict


# 全局锁：序列化 PIL/Pillow 的 convert_from_path 调用，避免多线程并发冲突
_pil_lock = threading.Lock()


def _register_profile_cleanup(profile_dir: Path) -> None:
    """注册 LibreOffice profile 的进程退出清理钩子。"""
    import atexit
    def _cleanup():
        shutil.rmtree(profile_dir, ignore_errors=True)
    atexit.register(_cleanup)


def _new_libreoffice_profile() -> Path:
    """为本次进程生成唯一 LibreOffice profile，注册进程退出清理。"""
    profile = Path(tempfile.gettempdir()) / f"lo_profile_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    _register_profile_cleanup(profile)
    return profile


def safe_filename(name: str) -> str:
    """将字符串转换为安全的文件系统 basename：去除控制字符、剥离路径分隔符与 `..` 段；全部为空时回退为 "untitled" 占位名。"""
    basename = Path(name).name
    cleaned = "".join(
        c for c in basename
        if unicodedata.category(c)[0] != "C" and c not in ("/", "\\") or c in ("_", "-")
    ).rstrip()
    if cleaned in ("", ".", ".."):
        return "untitled"
    return cleaned


def get_ppt_files(path: str) -> list[Path]:
    """获取路径下的所有 PPT/PPTX 文件（不递归）。"""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in (".ppt", ".pptx"):
            return [p]
        else:
            return []
    elif p.is_dir():
        return sorted([
            f for f in p.iterdir()
            if f.suffix.lower() in (".ppt", ".pptx") and f.is_file()
        ])
    else:
        return []


def _get_pdf_base_dir() -> Path:
    """返回中间 PDF 的存放根目录：Linux/macOS 为 /tmp/ppt/，Windows 为 %TEMP%/ppt/。"""
    base = Path(tempfile.gettempdir()) / "ppt"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _find_libreoffice() -> str | None:
    """跨平台检测 LibreOffice 命令路径。"""
    for name in ("libreoffice", "soffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def clean_tmp_pdfs(older_than_days: int = 7) -> dict:
    """
    清理 temp 根目录 / ppt/ 下超过指定天数的中间 PDF 及其空父目录。
    返回 {"removed_files": int, "removed_dirs": int, "scanned_root": str}
    """
    import time
    base = _get_pdf_base_dir()
    cutoff = time.time() - older_than_days * SECONDS_PER_DAY
    removed_files = 0
    removed_dirs = 0
    for pdf in base.rglob("*.pdf"):
        try:
            if pdf.stat().st_mtime < cutoff:
                pdf.unlink()
                removed_files += 1
        except OSError:
            continue
    for d in sorted(base.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
                removed_dirs += 1
            except OSError:
                continue
    return {
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "scanned_root": str(base),
    }


def clean_libreoffice_profiles(older_than_days: int = 7) -> dict:
    """
    清理 temp 根目录 / lo_profile_*/ 下超过指定天数的 LibreOffice 临时 profile 目录。
    返回 {"removed_dirs": int, "scanned_root": str}
    """
    import time
    base = Path(tempfile.gettempdir())
    cutoff = time.time() - older_than_days * SECONDS_PER_DAY
    removed_dirs = 0
    # 排序后从叶到根清理
    profiles = sorted(base.glob("lo_profile_*"), reverse=True)
    for prof in profiles:
        if not prof.is_dir():
            continue
        try:
            mtime = prof.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(prof, ignore_errors=False)
                removed_dirs += 1
            except OSError:
                continue
    return {
        "removed_dirs": removed_dirs,
        "scanned_root": str(base),
    }


def _abs_path_without_drive(path: Path) -> str:
    """
    将绝对路径转为跨平台相对路径字符串，用于在 temp/ppt/ 下重建目录结构。
    - Unix: /home/user/docs/demo.pptx → home/user/docs/demo.pptx
    - Windows: C:\\Users\\demo.pptx → Users/demo.pptx（盘符被 Path.parts[1:] 丢弃）
    注意：本函数始终使用正斜杠 `/` 拼接；调用方在 Windows 上若需反斜杠，请自行替换。
    依赖假设：脚本主要在 Windows / Linux 上跑，已在 Linux 验证；Windows 行为依赖 Path.parts[1:] 切片。
    """
    resolved = path.resolve()
    return "/".join(resolved.parts[1:])


def _find_pdf_in_outdir(outdir: Path, stem: str) -> list[Path]:
    """
    在 outdir 下精确查找 stem.pdf；用 iterdir + 字符串比较替代 glob，
    避免文件名含 glob 特殊字符（`[` `]` `*` `?`）时被当作模式语法解析。
    """
    if not outdir.is_dir():
        return []
    target_name = f"{stem}.pdf"
    return sorted(p for p in outdir.iterdir() if p.is_file() and p.name == target_name)


def _remove_empty_parents(start: Path, stop: Path) -> int:
    """
    从 start 开始逐级 rmdir 空父目录，直到 stop 或第一个非空目录。返回清理的目录数。
    用于 `--keep-pdf false` 删除 PDF 后清理空父目录链。
    """
    removed = 0
    current = start
    while current != stop and current.is_dir():
        try:
            current.rmdir()
            removed += 1
        except OSError:
            break
        current = current.parent
    return removed


def convert_ppt_to_pdf(ppt_path: Path) -> tuple[str, Result]:
    """
    通过 LibreOffice 将 PPT 转换为 PDF，返回 (pdf_path_str, Result)。
    PDF 保存到 temp 根目录 / ppt/ 下重建原路径结构。
    失败时返回 ("", Result) 其中 Result.status != "success"。
    """
    libreoffice_path = _find_libreoffice()
    if not libreoffice_path:
        return "", Result(
            status="libreoffice_failed",
            message="未找到 libreoffice/soffice 命令，请确认已安装并配置 PATH",
            files_created=[],
            details={"ppt": str(ppt_path), "error_type": "not_found"},
        )

    # 计算 PDF 保存路径（pdf_dir = temp/ppt/<rel_path>）
    # rel_path 末尾包含原 PPT 文件名，pdf_dir 的父目录才是 --outdir
    base_dir = _get_pdf_base_dir()
    rel_path = _abs_path_without_drive(ppt_path)
    pdf_dir = base_dir / rel_path          # pdf_dir 以 .pptx 结尾，可能是已存在的目录或文件
    outdir = pdf_dir.parent                 # --outdir 必须是 pdf_dir 的父目录，避免 LibreOffice 把 .pptx 当目录

    # 在 outdir 下查找已存在的同名 PDF（LibreOffice 可能复用旧结果）
    original_stem = ppt_path.stem          # 不含扩展名的原文件名

    # 清理 outdir 下可能的同名残留 PDF（LibreOffice 不会自动覆盖）
    # 精确匹配：仅删除 stem+.pdf，避免误删同名不同路径的 PDF
    residual_pdf = outdir / (original_stem + ".pdf")
    if residual_pdf.exists():
        try:
            residual_pdf.unlink()
        except OSError as e:
            print(f"warning: cleanup residual pdf {residual_pdf} failed: {e}", file=sys.stderr)

    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return "", Result(
            status="libreoffice_failed",
            message=f"无法创建输出目录 {outdir}: {e}",
            files_created=[],
            details={"ppt": str(ppt_path), "error_type": "mkdir_failed"},
        )

    # 转换：--outdir 指向 pdf_dir 的父目录，避免 .pptx 文件被当作输出子目录；
    # 使用进程级唯一 UserInstallation 隔离 LibreOffice profile，避免并发 profile 冲突。
    # profile 在函数内生成并在进程退出时清理，避免模块级常量导致的资源泄漏。
    profile_dir = _new_libreoffice_profile()
    try:
        result = subprocess.run(
            [
                libreoffice_path,
                f"-env:UserInstallation=file://{profile_dir}",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(outdir),
                str(ppt_path),
            ],
            capture_output=True,
            text=True,
            timeout=LIBREOFFICE_TIMEOUT,
        )
        if result.returncode != 0:
            stderr = result.stderr or result.stdout or ""
            if "font" in stderr.lower() or "missing" in stderr.lower():
                error_type = "fonts_missing"
            else:
                error_type = "convert_failed"
            return "", Result(
                status="libreoffice_failed",
                message=f"LibreOffice 转换失败: {stderr}",
                files_created=[],
                details={"ppt": str(ppt_path), "error_type": error_type},
            )
    except subprocess.TimeoutExpired:
        return "", Result(
            status="libreoffice_failed",
            message="LibreOffice 转换超时（超过 120 秒）",
            files_created=[],
            details={"ppt": str(ppt_path), "error_type": "timeout"},
        )
    except FileNotFoundError:
        return "", Result(
            status="libreoffice_failed",
            message="未找到 libreoffice 命令，请确认已安装",
            files_created=[],
            details={"ppt": str(ppt_path), "error_type": "not_found"},
        )
    except Exception as e:
        # result 可能在 subprocess.run 调用前抛异常，locals().get 防止 unbound
        prev_result = locals().get("result")
        return "", Result(
            status="libreoffice_failed",
            message=f"LibreOffice 转换异常: {e}",
            files_created=[],
            details={
                "ppt": str(ppt_path),
                "error_type": "unknown",
                "returncode": getattr(prev_result, "returncode", None),
                "stdout": getattr(prev_result, "stdout", None),
                "stderr": getattr(prev_result, "stderr", None),
            },
        )

    # 在 outdir 下查找 LibreOffice 输出的 PDF（精确字符串比较，避开 glob 特殊字符）
    pdf_candidates = _find_pdf_in_outdir(outdir, original_stem)
    if not pdf_candidates:
        return "", Result(
            status="libreoffice_failed",
            message=f"LibreOffice 未在 {outdir} 生成 PDF 文件（查找 {original_stem}.pdf）",
            files_created=[],
            details={
                "ppt": str(ppt_path),
                "error_type": "no_pdf_output",
                "stdout": getattr(result, "stdout", None),
                "stderr": getattr(result, "stderr", None),
                "returncode": getattr(result, "returncode", None),
            },
        )

    if len(pdf_candidates) > 1:
        return "", Result(
            status="libreoffice_failed",
            message=f"同一目录下存在多个同名 PDF，无法确定目标文件: {pdf_candidates}",
            files_created=[],
            details={"ppt": str(ppt_path), "error_type": "ambiguous_pdf", "candidates": [str(p) for p in pdf_candidates]},
        )

    pdf_path_str = str(pdf_candidates[0].resolve())
    return pdf_path_str, Result(
        status="success",
        message=f"LibreOffice 转换成功: {pdf_path_str}",
        files_created=[pdf_path_str],
        details={"pdf": pdf_path_str},
    )


def convert_pdf_to_images(pdf_path_str: str, output_dir: Path, img_format: str, dpi: int) -> Result:
    """
    通过 pdf2image 将 PDF 渲染为图片（内嵌逻辑，无跨 skill 依赖）。
    """
    if not PDF2IMAGE_AVAILABLE:
        return Result(
            status="import_failed",
            message="pdf2image 未安装，请执行 `pip install pdf2image` 后重试",
            files_created=[],
            details={"missing_package": "pdf2image"},
        )
    if not PIL_AVAILABLE:
        return Result(
            status="import_failed",
            message="Pillow 未安装，pdf2image 需要依赖 Pillow，请执行 `pip install Pillow` 后重试",
            files_created=[],
            details={"missing_package": "Pillow"},
        )

    # 在 PDF2IMAGE_AVAILABLE 为 True 的分支内 lazy import，并 try/except ImportError
    # 避免 find_spec 通过但实际 import 失败时整个脚本崩溃
    try:
        from pdf2image import convert_from_path  # type: ignore[reportMissingImports, reportUnusedImport]
    except ImportError as e:
        return Result(
            status="import_failed",
            message=f"pdf2image 模块导入失败: {e}",
            files_created=[],
            details={"missing_package": "pdf2image", "import_error": str(e)},
        )
    try:
        images = convert_from_path(pdf_path_str, dpi=dpi)
    except Exception as e:
        return Result(
            status="conversion_failed",
            message=f"pdf2image 渲染失败: {e}",
            files_created=[],
            details={
                "pdf": pdf_path_str,
                "dpi": dpi,
                "error_type": "render_error",
                "pdf_size": Path(pdf_path_str).stat().st_size if Path(pdf_path_str).exists() else None,
            },
        )

    stem = safe_filename(Path(pdf_path_str).stem)
    ext = "jpg" if img_format == "jpg" else "png"

    # 每个 PDF 一个同名子目录
    out_subdir = output_dir / stem
    try:
        out_subdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return Result(
            status="output_dir_create_failed",
            message=f"无法创建输出子目录 {out_subdir}: {e}",
            files_created=[],
            details={"pdf": pdf_path_str, "error_type": "mkdir_failed"},
        )

    # 计算固定宽度
    total_pages = len(images)
    width = len(str(total_pages)) if total_pages > 0 else 1

    created = []
    for i, img in enumerate(images, start=1):
        page_name = f"image-{i:0{width}d}.{ext}"
        out_path = out_subdir / page_name
        try:
            pillow_format = "JPEG" if img_format.upper() == "JPG" else img_format.upper()
            img.save(str(out_path), format=pillow_format)
            created.append(str(out_path))
        except Exception as e:
            return Result(
                status="save_failed",
                message=f"保存第 {i} 页失败: {e}",
                files_created=created,
                details={"pdf": pdf_path_str, "page": i, "error_type": "save_error"},
            )

    return Result(
        status="success",
        message=f"渲染成功，共 {len(images)} 页",
        files_created=created,
        details={"pdf": pdf_path_str, "pages": len(images)},
    )


def convert_ppt(ppt_path: Path, output_dir: Path, img_format: str, dpi: int, keep_pdf: bool = True) -> Result:
    """
    完整流程：LibreOffice PPT→PDF → pdf2image PDF→图片。
    中间 PDF 默认保留在 temp 根目录 / ppt/ 下（keep_pdf=True）；keep_pdf=False 时会在渲染成功后删除并清理空父目录链。
    """
    pdf_path_str, pdf_result = convert_ppt_to_pdf(ppt_path)
    if pdf_result.status != "success":
        return pdf_result

    image_result = convert_pdf_to_images(pdf_path_str, output_dir, img_format, dpi)
    if not keep_pdf and image_result.status == "success":
        pdf_path = Path(pdf_path_str)
        try:
            pdf_path.unlink()
        except OSError as e:
            print(f"warning: remove pdf {pdf_path} failed: {e}", file=sys.stderr)
        else:
            # 清理中间 PDF 所在的空父目录链，从 PDF 父目录逐级 rmdir 到 base_dir
            base_dir = _get_pdf_base_dir()
            _remove_empty_parents(pdf_path.parent, base_dir)
    return image_result


def run(path: str, output_dir: str | None, img_format: str, dpi: int, keep_pdf: bool = True, workers: int = 1) -> dict:
    """核心运行函数。workers >= 2 时使用 ThreadPoolExecutor 并发；PIL 调用由 _pil_lock 保护。"""
    path_p = Path(path)

    if not path_p.exists():
        return {
            "result_status": "missing_input",
            "summary": f"输入路径不存在: {path}",
            "targets": [],
        }

    ppt_files = get_ppt_files(path)

    if path_p.is_file() and path_p.suffix.lower() not in (".ppt", ".pptx"):
        return {
            "result_status": "not_a_ppt",
            "summary": f"输入文件不是 PPT/PPTX: {path}",
            "targets": [],
        }

    if path_p.is_dir() and not ppt_files:
        return {
            "result_status": "empty_input_dir",
            "summary": f"目录中没有 PPT/PPTX 文件: {path}",
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

    # 并发处理（workers=1 走原串行路径，避免无意义线程创建）
    targets: list[dict] = []
    if workers <= 1 or len(ppt_files) <= 1:
        for ppt_file in ppt_files:
            with _pil_lock:
                result = convert_ppt(ppt_file, out_dir, img_format, dpi, keep_pdf=keep_pdf)
            targets.append({
                "source": str(ppt_file),
                "status": result.status,
                "message": result.message,
                "output_files": result.files_created,
                "details": result.details,
            })
    else:
        def _worker(ppt_file: Path) -> dict:
            with _pil_lock:
                r = convert_ppt(ppt_file, out_dir, img_format, dpi, keep_pdf=keep_pdf)
            return {
                "source": str(ppt_file),
                "status": r.status,
                "message": r.message,
                "output_files": r.files_created,
                "details": r.details,
            }

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, p): p for p in ppt_files}
            for fut in as_completed(futures):
                targets.append(fut.result())

    all_created: list[str] = []
    total_pages = 0
    for t in targets:
        all_created.extend(t["output_files"])
        total_pages += t["details"].get("pages", 0)

    success_count = sum(1 for t in targets if t["status"] == "success")
    failed_count = len(targets) - success_count

    summary_parts = []
    if success_count > 0:
        summary_parts.append(f"成功转换 {success_count} 个 PPT，共 {total_pages} 页")
    if failed_count > 0:
        summary_parts.append(f"失败 {failed_count} 个")
    if all_created:
        summary_parts.append(f"输出图片: {len(all_created)} 个")

    return {
        "result_status": "success" if failed_count == 0 else "partial",
        "summary": "；".join(summary_parts) if summary_parts else "完成",
        "targets": targets,
        "files_created": all_created,
        "total_pages": total_pages,
    }


def main():
    parser = argparse.ArgumentParser(
        description="将 PPT/PPTX 转换为图片（PNG/JPG），支持单文件和批量目录转换。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
流程：LibreOffice PPT→PDF（存 temp/ppt/） → pdf2image → 图片

示例:
  %(prog)s --path demo.pptx                       # 单文件转 PNG
  %(prog)s --path demo.ppt --format jpg           # 单文件转 JPG（支持旧版 .ppt）
  %(prog)s --path demo.pptx --output-dir out/     # 指定输出目录
  %(prog)s --path ppts/                           # 批量转换目录
  %(prog)s --path ppts/ --dpi 300                 # 高清转换
  %(prog)s --path demo.pptx --no-keep-pdf         # 转换后立即删除中间 PDF
  %(prog)s --clean-tmp                            # 清理 7 天前的中间 PDF
  %(prog)s --clean-tmp --clean-tmp-days 30        # 清理 30 天前的中间 PDF
  %(prog)s --clean-profiles                       # 清理 LibreOffice 临时 profile 目录
  %(prog)s --path ppts/ --workers 4               # 批量并发
        """,
    )
    parser.add_argument("--path", help="输入 PPT/PPTX 文件或包含 PPT/PPTX 的目录")
    parser.add_argument("--output-dir", help="输出目录（默认写回源文件同目录）")
    parser.add_argument(
        "--format",
        choices=["png", "jpg"],
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
        "--keep-pdf",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否在转换成功后保留中间 PDF（默认 true；用 --no-keep-pdf 时渲染完成后立即删除）",
    )
    parser.add_argument(
        "--clean-tmp",
        action="store_true",
        help="清理 temp/ppt/ 下超过指定天数的中间 PDF；与 --path / --clean-profiles 互斥",
    )
    parser.add_argument(
        "--clean-profiles",
        action="store_true",
        help="清理 temp/lo_profile_*/ 下超过指定天数的 LibreOffice 临时 profile 目录；与 --path / --clean-tmp 互斥",
    )
    parser.add_argument(
        "--clean-tmp-days",
        type=int,
        default=CLEAN_TMP_DAYS_DEFAULT,
        help=f"配合 --clean-tmp / --clean-profiles 使用，指定清理阈值天数（默认 {CLEAN_TMP_DAYS_DEFAULT}，合法范围 [{CLEAN_TMP_DAYS_MIN}, {CLEAN_TMP_DAYS_MAX}]）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="批量并发数（默认 1，串行；>=2 时启用 ThreadPoolExecutor 并发，PIL 并发安全由内部 _pil_lock 保护）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )

    args = parser.parse_args()

    # 互斥校验
    if args.clean_tmp and args.path:
        parser.error("--clean-tmp 与 --path 互斥，请二选一")
    if args.clean_profiles and args.path:
        parser.error("--clean-profiles 与 --path 互斥，请二选一")
    if args.clean_tmp and args.clean_profiles:
        parser.error("--clean-tmp 与 --clean-profiles 互斥，请二选一")
    if not args.clean_tmp and not args.clean_profiles and not args.path:
        parser.error("必须提供 --path、--clean-tmp、--clean-profiles 之一")
    if not (CLEAN_TMP_DAYS_MIN <= args.clean_tmp_days <= CLEAN_TMP_DAYS_MAX):
        parser.error(
            f"--clean-tmp-days 必须在 [{CLEAN_TMP_DAYS_MIN}, {CLEAN_TMP_DAYS_MAX}] 之间，当前值: {args.clean_tmp_days}"
        )
    if args.workers < 1:
        parser.error(f"--workers 必须 >= 1，当前值: {args.workers}")

    if args.clean_tmp:
        result = clean_tmp_pdfs(args.clean_tmp_days)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n{'='*50}")
            print(f"zm-ppt2image 清理中间 PDF 结果")
            print(f"{'='*50}")
            print(f"扫描根目录: {result['scanned_root']}")
            print(f"删除 PDF: {result['removed_files']} 个")
            print(f"删除空目录: {result['removed_dirs']} 个")
        sys.exit(0)

    if args.clean_profiles:
        result = clean_libreoffice_profiles(args.clean_tmp_days)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n{'='*50}")
            print(f"zm-ppt2image 清理 LibreOffice profiles 结果")
            print(f"{'='*50}")
            print(f"扫描根目录: {result['scanned_root']}")
            print(f"删除 profile 目录: {result['removed_dirs']} 个")
        sys.exit(0)

    # args.keep_pdf 已由 BooleanOptionalAction 直接解析为 bool
    result = run(args.path, args.output_dir, args.format, args.dpi, keep_pdf=args.keep_pdf, workers=args.workers)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-ppt2image 转换结果")
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

    if result["result_status"] in ("missing_input", "output_dir_create_failed"):
        sys.exit(1)
    elif result["result_status"] == "partial":
        sys.exit(2)
    elif result["result_status"] == "import_failed":
        # 依赖包缺失（pdf2image / Pillow 未安装），CI/调用方必须能识别
        sys.exit(11)
    elif result["result_status"] == "libreoffice_failed":
        # LibreOffice 转换失败，核心流程失败
        sys.exit(12)
    elif result["result_status"] == "conversion_failed":
        # pdf2image 渲染失败，核心流程失败
        sys.exit(13)
    elif result["result_status"] == "save_failed":
        # 写入图片失败
        sys.exit(14)
    else:
        # success / not_a_ppt / empty_input_dir：输入侧筛选或正常完成，退出码 0
        sys.exit(0)


if __name__ == "__main__":
    main()
