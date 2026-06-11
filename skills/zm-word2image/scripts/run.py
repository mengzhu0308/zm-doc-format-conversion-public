#!/usr/bin/env python3
"""
zm-word2image: 将 Word 文档转换为图片（PNG/JPG）
两步转换：LibreOffice Word→PDF（/tmp/word/） + pdf2image_run.py PDF→图片
支持单文件和目录下批量转换。
"""

import argparse
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path

from _common import Result, read_skill_version, safe_filename, validate_output_dir

# 固定中间 PDF 根目录
TEMP_PDF_ROOT = Path("/tmp/word")
# 当输入路径已经在 TEMP_PDF_ROOT 下时，落到 _chained/ 子目录避免路径双层嵌套
CHAINED_SUBDIR = "_chained"

# bundled pdf2image 脚本路径（与本脚本同目录）
SCRIPT_DIR = Path(__file__).parent.resolve()
PDF2IMAGE_SCRIPT = SCRIPT_DIR / "pdf2image_run.py"


def get_word_files(path: str) -> list[Path]:
    """
    获取路径下的所有 Word 文件（不递归）。

    过滤规则：
    1. 跳过以 `.` 开头的隐藏文件（A-2 P1-9，包括 LibreOffice 锁文件 `.~lock.*` 等）
    2. 跟随 symlink 后再校验扩展名（A-3 P0-2），避免 `xxx.docx → real.txt` 这种
       扩展名伪装被当成 Word 文件
    """
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in (".doc", ".docx"):
            return [p]
        return []
    if p.is_dir():
        out: list[Path] = []
        for f in p.iterdir():
            if f.name.startswith("."):
                continue
            # 第一道保护：is_file() 在 self-loop / 损坏 symlink 上回 False（B-P1-1），
            # 可直接短路，避免进入 resolve() 触发 RuntimeError("Symlink loop")
            if not f.is_file():
                continue
            # 第二道保护：跟随 symlink 后再校验扩展名（A-3 P0-2）
            try:
                real = f.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            if real.suffix.lower() in (".doc", ".docx") and real.is_file():
                out.append(f)
        return sorted(out)
    return []


def _run_subprocess(cmd: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    """封装 subprocess.run，统一捕获 FileNotFoundError。"""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return None


def _try_direct_pdf2image() -> bool:
    """优先尝试直接 import pdf2image，避免跨平台 conda 依赖。"""
    try:
        import pdf2image  # noqa: F401
        return True
    except Exception:
        return False


def word_to_pdf(word_path: Path, final_state: bool = False) -> Result:
    """使用 LibreOffice 将 Word 文件转换为 PDF。"""
    # 检查 libreoffice 是否可用
    version_check = _run_subprocess(["libreoffice", "--version"], timeout=10)
    if version_check is None:
        return Result(
            status="libreoffice_not_found",
            message="未找到 LibreOffice，请确认已安装并加入 PATH",
            files_created=[],
            details={},
        )
    if version_check.returncode != 0 and not version_check.stdout.strip():
        return Result(
            status="libreoffice_not_found",
            message="LibreOffice 检查失败（无法获取版本）",
            files_created=[],
            details={},
        )

    # 计算 PDF 输出路径：/tmp/word/<原绝对路径>.pdf
    # 当源路径已经在 TEMP_PDF_ROOT 下时，把中间 PDF 落到 _chained/ 子目录，
    # 避免 /tmp/word/foo.docx → /tmp/word/tmp/word/foo.pdf 的双层嵌套。
    if not word_path.is_file():
        return Result(
            status="not_a_word",
            message=f"输入路径不是文件: {word_path}",
            files_created=[],
            details={"word": str(word_path)},
        )
    abs_path = word_path.resolve()
    try:
        rel = abs_path.relative_to(TEMP_PDF_ROOT)
        pdf_path = TEMP_PDF_ROOT / CHAINED_SUBDIR / rel
    except ValueError:
        pdf_path = TEMP_PDF_ROOT / str(abs_path).lstrip("/")
    pdf_path = pdf_path.with_suffix(".pdf")

    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return Result(
            status="output_dir_create_failed",
            message=f"无法创建 PDF 输出目录 {pdf_path.parent}: {e}",
            files_created=[],
            details={},
        )

    cmd = [
        "libreoffice", "--headless",
        "--convert-to", "pdf",
        "--outdir", str(pdf_path.parent),
    ]
    if final_state:
        if word_path.suffix.lower() == ".docx":
            cmd.insert(2, "--infilter=MS Word 2007 XML:SHOWCHANGES=0")
        else:
            cmd.insert(2, "--infilter=MS Word 95/97/2000/XP/2003:SHOWCHANGES=0")
    cmd.append(str(abs_path))

    def _run_once() -> subprocess.CompletedProcess:
        return _run_subprocess(cmd, timeout=120)  # type: ignore[return-value]

    result = _run_once()
    if result is None:
        return Result(
            status="libreoffice_not_found",
            message="未找到 LibreOffice 可执行文件",
            files_created=[],
            details={"word": str(word_path)},
        )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        return Result(
            status="libreoffice_conversion_failed",
            message=f"LibreOffice 转换失败: {stderr[:200]}",
            files_created=[],
            details={"word": str(word_path)},
        )

    if not pdf_path.exists():
        return Result(
            status="libreoffice_conversion_failed",
            message=f"LibreOffice 未生成 PDF 文件: {pdf_path}",
            files_created=[],
            details={"word": str(word_path)},
        )

    pdf_size = pdf_path.stat().st_size
    if pdf_size == 0:
        return Result(
            status="libreoffice_conversion_failed",
            message=f"LibreOffice 生成的 PDF 文件为空（0 字节）: {pdf_path}",
            files_created=[],
            details={"word": str(word_path)},
        )

    pdf_mtime = pdf_path.stat().st_mtime
    word_mtime = abs_path.stat().st_mtime
    if pdf_mtime < word_mtime:
        try:
            pdf_path.unlink()
        except OSError as e:
            logging.warning("旧 PDF 清理失败: %s, err=%s", pdf_path, e)
        result = _run_once()
        if result is None or result.returncode != 0 or not pdf_path.exists():
            return Result(
                status="libreoffice_conversion_failed",
                message="PDF 重新转换失败",
                files_created=[],
                details={"word": str(word_path)},
            )
        if pdf_path.stat().st_size == 0:
            return Result(
                status="libreoffice_conversion_failed",
                message="PDF 重新转换后文件为空（0 字节）",
                files_created=[],
                details={"word": str(word_path)},
            )

    return Result(
        status="pdf_generated",
        message=f"PDF 生成成功: {pdf_path}",
        files_created=[str(pdf_path)],
        details={"pdf_path": str(pdf_path)},
    )


def _build_pdf2image_cmd(pdf_path: Path, output_dir: Path, img_format: str, dpi: int) -> list[str] | None:
    """
    构造调用 pdf2image_run.py 的命令。
    优先直接调用当前 Python（不依赖 conda），回退到 `conda run -n agent-skills`。
    """
    base = [
        sys.executable,
        str(PDF2IMAGE_SCRIPT),
        "--path", str(pdf_path),
        "--output-dir", str(output_dir),
        "--format", img_format,
        "--dpi", str(dpi),
        "--json",
    ]
    if _try_direct_pdf2image():
        return base
    # 回退：让 conda 激活目标环境后再跑
    return [
        "conda", "run", "-n", "agent-skills", "python",
        str(PDF2IMAGE_SCRIPT),
        "--path", str(pdf_path),
        "--output-dir", str(output_dir),
        "--format", img_format,
        "--dpi", str(dpi),
        "--json",
    ]


def convert_pdf_to_images(pdf_path: Path, output_dir: Path, img_format: str, dpi: int) -> Result:
    """
    调用内嵌的 pdf2image_run.py 将 PDF 转换为图片。
    """
    cmd = _build_pdf2image_cmd(pdf_path, output_dir, img_format, dpi)
    if cmd is None:
        return Result(
            status="conversion_failed",
            message="无法构造 pdf2image 调用命令",
            files_created=[],
            details={"pdf": str(pdf_path)},
        )
    result = _run_subprocess(cmd, timeout=300)
    if result is None:
        return Result(
            status="conversion_failed",
            message="无法启动 pdf2image 进程（Python 或 conda 未找到）",
            files_created=[],
            details={"pdf": str(pdf_path)},
        )
    output = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0 and not output.strip():
        stderr = result.stderr.decode("utf-8", errors="replace")
        return Result(
            status="conversion_failed",
            message=f"pdf2image_run.py 执行失败（exit {result.returncode}）: {stderr[:200]}",
            files_created=[],
            details={"pdf": str(pdf_path)},
        )
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        stderr = result.stderr.decode("utf-8", errors="replace")
        return Result(
            status="conversion_failed",
            message=f"pdf2image 输出解析失败（exit {result.returncode}）: {output[:100]} | {stderr[:100]}",
            files_created=[],
            details={"pdf": str(pdf_path)},
        )
    files = data.get("files_created", [])
    pages = data.get("total_pages", 0)
    result_status = data.get("result_status", "unknown")
    summary = data.get("summary", "")
    if result_status == "success":
        return Result(
            status="success",
            message=f"转换成功，共 {pages} 页",
            files_created=files,
            details={"pdf": str(pdf_path), "pages": pages},
        )
    return Result(
        status="conversion_failed",
        message=f"pdf2image 转换失败: {summary}",
        files_created=files,
        details={"pdf": str(pdf_path)},
    )


def run(
    path: str,
    output_dir: str | None,
    img_format: str,
    dpi: int,
    final_state: bool = False,
    clean_tmp: bool = False,
) -> dict:
    """核心运行函数，返回结构化结果字典。"""
    path_p = Path(path)

    if not path_p.exists():
        return {
            "result_status": "missing_input",
            "summary": f"输入路径不存在: {path}",
            "targets": [],
        }

    word_files = get_word_files(path)

    if path_p.is_file() and path_p.suffix.lower() not in (".doc", ".docx"):
        return {
            "result_status": "not_a_word",
            "summary": f"输入文件不是 Word 文档: {path}",
            "targets": [],
        }

    if path_p.is_dir() and not word_files:
        return {
            "result_status": "empty_input_dir",
            "summary": f"目录中没有 Word 文件: {path}",
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

    try:
        TEMP_PDF_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {
            "result_status": "output_dir_create_failed",
            "summary": f"无法创建临时 PDF 目录 {TEMP_PDF_ROOT}: {e}",
            "targets": [],
        }

    targets: list[dict] = []
    all_created: list[str] = []
    total_pages = 0
    produced_pdfs: list[str] = []

    for word_file in word_files:
        step1_result = word_to_pdf(word_file, final_state=final_state)

        if step1_result.status != "pdf_generated":
            targets.append({
                "source": str(word_file),
                "status": step1_result.status,
                "message": step1_result.message,
                "output_files": [],
                "details": step1_result.details,
            })
            continue

        produced_pdfs.append(step1_result.files_created[0])
        pdf_path = Path(step1_result.files_created[0])
        word_stem = safe_filename(word_file.stem)

        step2_result = convert_pdf_to_images(pdf_path, out_dir, img_format, dpi)

        if step2_result.status == "success" and step2_result.files_created:
            correct_dir = out_dir / word_stem
            try:
                correct_dir.mkdir(parents=True, exist_ok=True)
                moved = []
                for f in step2_result.files_created:
                    src = Path(f)
                    dst = correct_dir / src.name
                    if dst.exists():
                        unique_suffix = uuid.uuid4().hex[:8]
                        dst = correct_dir / f"{src.stem}_{unique_suffix}{src.suffix}"
                    src.rename(dst)
                    moved.append(str(dst))
                step2_result = Result(
                    status=step2_result.status,
                    message=step2_result.message,
                    files_created=moved,
                    details=step2_result.details,
                )
            except (OSError, PermissionError) as e:
                step2_result = Result(
                    status="save_failed",
                    message=f"移动图片到 {correct_dir} 失败: {e}",
                    files_created=step2_result.files_created,
                    details={**step2_result.details, "rename_error": str(e)},
                )

        targets.append({
            "source": str(word_file),
            "status": step2_result.status,
            "message": step2_result.message,
            "output_files": step2_result.files_created,
            "details": step2_result.details,
        })
        all_created.extend(step2_result.files_created)
        total_pages += step2_result.details.get("pages", 0)

    if clean_tmp:
        for pdf in produced_pdfs:
            try:
                Path(pdf).unlink()
            except OSError as e:
                print(f"清理中间 PDF 失败: {pdf}: {e}", file=sys.stderr)

    success_count = sum(1 for t in targets if t["status"] == "success")
    failed_count = len(targets) - success_count

    summary_parts: list[str] = []
    if success_count > 0:
        summary_parts.append(f"成功转换 {success_count} 个 Word，共 {total_pages} 页")
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


def _validate_dpi(value: str) -> int:
    """--dpi 范围校验：50-2400"""
    try:
        v = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--dpi 必须是整数，收到 {value!r}")
    if not 50 <= v <= 2400:
        raise argparse.ArgumentTypeError(f"--dpi 必须在 50-2400 之间，收到 {v}")
    return v


# --format 大小写兼容：接受 png / PNG / jpg / JPG / JPEG 等（B-P2-1）
_FORMAT_CHOICES = {"png", "jpg", "jpeg"}


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


def main():
    parser = argparse.ArgumentParser(
        description="将 Word 文档转换为图片（PNG/JPG），支持单文件和批量目录转换。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --path demo.docx                  # 单文件转 PNG
  %(prog)s --path demo.docx --format jpg     # 单文件转 JPG
  %(prog)s --path demo.docx --output-dir out/  # 指定输出目录
  %(prog)s --path docs/                      # 批量转换目录
  %(prog)s --path docs/ --dpi 300            # 高清转换
  %(prog)s --path demo.docx --final-state    # 审阅模式文档使用最终状态转换
  %(prog)s --path demo.docx --clean-tmp      # 转换后清理 /tmp/word/ 中间 PDF
        """,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"zm-word2image {read_skill_version()}",
    )
    parser.add_argument("--path", required=True, help="输入 Word 文件或包含 Word 文件的目录")
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
        type=_validate_dpi,
        default=300,
        help="图片 DPI，必须在 50-2400 之间（默认 300）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )
    parser.add_argument(
        "--final-state",
        action="store_true",
        help="以最终状态转换（接受所有修订痕迹，用于审阅模式的 Word 文档）",
    )
    parser.add_argument(
        "--clean-tmp",
        action="store_true",
        help="转换完成后删除 /tmp/word/ 下本次产生的中间 PDF",
    )

    args = parser.parse_args()
    result = run(args.path, args.output_dir, args.format, args.dpi, args.final_state, args.clean_tmp)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-word2image 转换结果")
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

    # 进程退出码：success=0, partial=2, 其他错误=1
    # 失败状态码单一真相来源：见 SKILL.md「失败口径」表格
    # 当前覆盖（run() 顶层实际返回的状态码）：
    #   success / partial / missing_input / empty_input_dir / not_a_word /
    #   libreoffice_not_found / libreoffice_conversion_failed /
    #   conversion_failed / output_dir_create_failed / save_failed
    # 注意：import_failed / not_a_pdf 仅在 pdf2image_run.py 子进程出现，
    # 被 run.py 包装为 conversion_failed 后退出 1。
    if result["result_status"] == "success":
        sys.exit(0)
    if result["result_status"] == "partial":
        sys.exit(2)
    sys.exit(1)


if __name__ == "__main__":
    main()
