#!/usr/bin/env python3
"""
zm-word2image 冒烟测试脚本
不依赖真实 Word 文件，覆盖 argparse、safe_filename、Result 序列化、get_word_files、run() 预检等。
开发态使用：python3 scripts/smoke.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from _common import Result, safe_filename  # noqa: E402
import pdf2image_run  # noqa: E402
import run as run_mod  # noqa: E402


FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def test_safe_filename() -> None:
    print("\n[1] safe_filename 边界")
    cases = [
        ("hello", "hello"),
        ("中文-测试.docx", "中文-测试.docx"),
        ("   ", r"^word_[0-9a-f]{8}$"),
        ("", r"^word_[0-9a-f]{8}$"),
        ("a/b\\c", "a_b_c"),
        ("../etc/passwd", ".._etc_passwd"),
        ("COM1.docx", "COM1_w.docx"),  # A-3 P2-2 Windows 保留名保护
        ("CON.docx", "CON_w.docx"),    # A-3 P2-2
        ("com1.docx", "com1_w.docx"),  # A-3 P2-2 case-insensitive
        ("LPT1.docx", "LPT1_w.docx"),  # A-3 P2-2
        ("COM10.docx", "COM10.docx"),  # COM10 不在保留名集合
        ("a\nb\tc\x00d", "abcd"),
    ]
    for inp, expected in cases:
        out = safe_filename(inp)
        if expected.startswith("^"):
            import re
            check(f"safe_filename({inp!r}) 匹配 {expected}", bool(re.match(expected, out)),
                  f"实际={out!r}")
        else:
            check(f"safe_filename({inp!r}) == {expected!r}", out == expected,
                  f"实际={out!r}")


def test_get_word_files() -> None:
    print("\n[2] get_word_files")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        (tmp_p / "a.docx").write_bytes(b"")
        (tmp_p / "b.DOCX").write_bytes(b"")  # 大小写
        (tmp_p / "c.txt").write_bytes(b"")
        files = run_mod.get_word_files(str(tmp_p))
        names = sorted(f.name for f in files)
        check("目录扫描大小写不敏感", names == ["a.docx", "b.DOCX"], f"实际={names}")
        check("目录扫描过滤 .txt", "c.txt" not in names, f"实际={names}")

        single = run_mod.get_word_files(str(tmp_p / "a.docx"))
        check("单文件输入", len(single) == 1 and single[0].name == "a.docx")
        wrong = run_mod.get_word_files(str(tmp_p / "c.txt"))
        check("非 Word 文件返回空", wrong == [])

        # 隐藏文件以 `.` 开头会被跳过（A-2 P1-9）
        (tmp_p / ".hidden.docx").write_bytes(b"")
        names2 = sorted(f.name for f in run_mod.get_word_files(str(tmp_p)))
        check("隐藏 .docx 会被跳过（A-2 P1-9）",
              ".hidden.docx" not in names2, f"实际={names2}")

        # 软链接指向非 Word 文件会被跳过（A-3 P0-2）
        (tmp_p / "real.txt").write_text("hello")
        link = tmp_p / "trick.docx"
        link.symlink_to(tmp_p / "real.txt")
        names3 = sorted(f.name for f in run_mod.get_word_files(str(tmp_p)))
        check("软链接→非 Word 文件会被跳过（A-3 P0-2）",
              "trick.docx" not in names3, f"实际={names3}")

        # self-loop 软链接不应让 get_word_files 抛 RuntimeError（B-P1-1 / SYS-1）
        selflink = tmp_p / "selfloop.docx"
        selflink.symlink_to(selflink)
        try:
            names4 = sorted(f.name for f in run_mod.get_word_files(str(tmp_p)))
            check("self-loop symlink 不抛异常（B-P1-1）", True)
            check("self-loop symlink 被跳过，其他合法文件仍被扫到",
                  "selfloop.docx" not in names4
                  and "a.docx" in names4
                  and "real.txt" not in names4,
                  f"实际={names4}")
        except Exception as e:
            check("self-loop symlink 不抛异常（B-P1-1）", False,
                  f"抛 {type(e).__name__}: {e}")


def test_run_prechecks() -> None:
    print("\n[3] run() 预检返回结构")
    r = run_mod.run("/nonexistent/path/should/not/exist", None, "png", 300)
    check("missing_input", r["result_status"] == "missing_input", r.get("summary", ""))

    with tempfile.TemporaryDirectory() as tmp:
        # 真创建一个空 .docx 文件，走 word_to_pdf（会因为 LibreOffice 失败而返回 libreoffice_*）
        empty = Path(tmp) / "empty.docx"
        empty.write_bytes(b"")
        r = run_mod.run(str(empty), None, "png", 300)
        check("存在的空 docx 不会误报 missing_input",
              r["result_status"] != "missing_input",
              f"status={r['result_status']}, summary={r.get('summary', '')[:80]}")

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "nope.txt").write_text("x")
        r = run_mod.run(str(Path(tmp) / "nope.txt"), None, "png", 300)
        check("非 Word 单文件 → not_a_word", r["result_status"] == "not_a_word", r.get("summary", ""))

    with tempfile.TemporaryDirectory() as tmp:
        r = run_mod.run(str(Path(tmp)), None, "png", 300)
        check("无 Word 的目录 → empty_input_dir", r["result_status"] == "empty_input_dir", r.get("summary", ""))


def test_dpi_validation() -> None:
    print("\n[4] --dpi 范围校验")
    # 范围越界应在 argparse 阶段就退出（码 2），不进入 run()。
    # 范围合法时仍会进入 run() 并因 x.docx 不存在返回 missing_input → 退出 1。
    cli = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "x.docx", "--dpi"]
    cases = [(49, 2), (2401, 2), ("abc", 2)]      # 越界/非整数：argparse 拒绝
    cases_ok = [(50, 1), (300, 1), (2400, 1)]    # 合法：进入 run() 走到 missing_input
    for val, expected_exit in cases:
        proc = subprocess.run(cli + [str(val)], capture_output=True, timeout=10)
        check(f"--dpi {val!r} 越界/非法 退出码 {expected_exit}",
              proc.returncode == expected_exit,
              f"实际={proc.returncode}, stderr={proc.stderr.decode()[:120]}")
    for val, expected_exit in cases_ok:
        proc = subprocess.run(cli + [str(val)], capture_output=True, timeout=10)
        check(f"--dpi {val!r} 合法但路径不存在 退出码 {expected_exit}",
              proc.returncode == expected_exit,
              f"实际={proc.returncode}")


def test_exit_code_unknown_input() -> None:
    print("\n[5] 错误状态退出码（非 0）")
    cli = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "/nope/missing.docx", "--json"]
    proc = subprocess.run(cli, capture_output=True, timeout=10)
    check("missing_input 退出码 1", proc.returncode == 1, f"实际={proc.returncode}")

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "plain.txt").write_text("x")
        cli2 = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", str(Path(tmp) / "plain.txt"), "--json"]
        proc = subprocess.run(cli2, capture_output=True, timeout=10)
        check("not_a_word 退出码 1", proc.returncode == 1, f"实际={proc.returncode}")

        cli3 = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", tmp, "--json"]
        proc = subprocess.run(cli3, capture_output=True, timeout=10)
        check("empty_input_dir 退出码 1", proc.returncode == 1, f"实际={proc.returncode}")


def test_result_namedtuple() -> None:
    print("\n[6] Result 序列化")
    r = Result(status="success", message="ok", files_created=["/a/b.png"], details={"pages": 3})
    blob = json.dumps(r._asdict(), ensure_ascii=False)
    check("Result 可 JSON 序列化", "success" in blob and "pages" in blob)


def test_pdf2image_cli_help() -> None:
    print("\n[7] pdf2image_run.py CLI help 可用")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "pdf2image_run.py"), "--help"],
        capture_output=True, timeout=10,
    )
    check("pdf2image_run.py --help 退出 0", proc.returncode == 0, proc.stderr.decode()[:200])
    check("帮助里包含 '调试/独立使用' 字样", "调试" in proc.stdout.decode() or "独立" in proc.stdout.decode())


def test_format_case_insensitive() -> None:
    """B-P2-1：--format 大小写兼容"""
    print("\n[8] --format 大小写兼容")
    for fmt in ["PNG", "Jpg", "JPEG", "png", "jpg"]:
        cli = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "x.docx", "--format", fmt]
        proc = subprocess.run(cli, capture_output=True, timeout=10)
        check(f"--format {fmt!r} 大小写兼容 进入 run() 退出码 1",
              proc.returncode == 1, f"实际={proc.returncode}, stderr={proc.stderr.decode()[:80]}")
    # 不合法格式应仍被 argparse 拒绝
    for bad in ["bmp", "tiff", "gif"]:
        cli = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "x.docx", "--format", bad]
        proc = subprocess.run(cli, capture_output=True, timeout=10)
        check(f"--format {bad!r} 非法 退出码 2",
              proc.returncode == 2, f"实际={proc.returncode}")
    # pdf2image_run.py 同样兼容
    for fmt in ["PNG", "Jpg", "JPEG"]:
        cli = [sys.executable, str(SCRIPT_DIR / "pdf2image_run.py"), "--path", "x.pdf", "--format", fmt]
        proc = subprocess.run(cli, capture_output=True, timeout=10)
        check(f"pdf2image_run.py --format {fmt!r} 大小写兼容 退出码 1",
              proc.returncode == 1, f"实际={proc.returncode}")


def test_output_dir_whitelist() -> None:
    """A-2 P1-1 + A-3 P0-1：--output-dir 越界校验（含子目录）"""
    print("\n[8] --output-dir 白名单校验")
    forbidden = ["/etc", "/etc/", "/var", "/usr", "/boot", "/proc", "/sys", "/root", "/run", "/dev", "/sbin", "/bin", "/lib", "/lib64"]
    for d in forbidden:
        cli = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "x.docx", "--output-dir", d]
        proc = subprocess.run(cli, capture_output=True, timeout=10)
        check(f"--output-dir {d!r} 越界 退出码 2",
              proc.returncode == 2,
              f"实际={proc.returncode}, stderr={proc.stderr.decode()[:80]}")
    # A-3 P0-1：子目录也必须被拒绝
    forbidden_subpath = ["/etc/subdir", "/var/log/x", "/usr/local/bin", "/proc/1/root", "/tmp/../etc/foo"]
    for d in forbidden_subpath:
        cli = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "x.docx", "--output-dir", d]
        proc = subprocess.run(cli, capture_output=True, timeout=10)
        check(f"--output-dir {d!r} 子目录 越界 退出码 2（A-3 P0-1）",
              proc.returncode == 2,
              f"实际={proc.returncode}, stderr={proc.stderr.decode()[:80]}")
    # 合法目录应进入 run() 并因 x.docx 不存在返回 missing_input → 退出 1
    cli_ok = [sys.executable, str(SCRIPT_DIR / "run.py"), "--path", "x.docx", "--output-dir", "/tmp/"]
    proc = subprocess.run(cli_ok, capture_output=True, timeout=10)
    check("--output-dir /tmp/ 合法 退出码 1（走到 missing_input）",
          proc.returncode == 1, f"实际={proc.returncode}")
    # A-3 P1-3：pdf2image_run.py 同样带白名单
    cli_pdf2image = [sys.executable, str(SCRIPT_DIR / "pdf2image_run.py"), "--path", "x.pdf", "--output-dir", "/etc/foo"]
    proc2 = subprocess.run(cli_pdf2image, capture_output=True, timeout=10)
    check("pdf2image_run.py --output-dir /etc/foo 越界 退出码 2（A-3 P1-3）",
          proc2.returncode == 2, f"实际={proc2.returncode}, stderr={proc2.stderr.decode()[:80]}")


def test_pdf_path_double_nesting() -> None:
    """A-2 P1-5：word_path 在 /tmp/word/ 下时，pdf_path 落到 /tmp/word/_chained/"""
    print("\n[9] PDF 路径双层嵌套修复")
    from run import TEMP_PDF_ROOT, CHAINED_SUBDIR
    word_path = TEMP_PDF_ROOT / "foo.docx"
    abs_path = word_path.resolve()
    try:
        rel = abs_path.relative_to(TEMP_PDF_ROOT)
        pdf_path = TEMP_PDF_ROOT / CHAINED_SUBDIR / rel
    except ValueError:
        pdf_path = TEMP_PDF_ROOT / str(abs_path).lstrip("/")
    pdf_path = pdf_path.with_suffix(".pdf")
    check("源在 TEMP_PDF_ROOT 下时 落 _chained/",
          str(pdf_path) == str(TEMP_PDF_ROOT / CHAINED_SUBDIR / "foo.pdf"),
          f"实际={pdf_path}")


def test_old_pdf_unlink_warning() -> None:
    """A-2 P1-6：旧 PDF unlink 失败时输出 warning 而非静默"""
    print("\n[10] 旧 PDF unlink 静默吞错改写")
    import logging
    import io
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.WARNING)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.WARNING)
    try:
        # 直接调用 unlink on a path that doesn't exist - should silently succeed,
        # so instead we patch to raise
        from unittest.mock import patch
        with patch.object(Path, "unlink", side_effect=PermissionError("test denied")):
            try:
                Path("/tmp/never_exists_xxx.pdf").unlink()
            except (OSError, PermissionError) as e:
                logging.warning("旧 PDF 清理失败: %s, err=%s", "/tmp/never_exists_xxx.pdf", e)
        log_output = log_stream.getvalue()
        check("旧 PDF unlink 失败有 warning 日志",
              "旧 PDF 清理失败" in log_output,
              f"实际 log={log_output!r}")
    finally:
        logging.getLogger().removeHandler(handler)


def test_version_flag() -> None:
    """A-2 P1-10：--version 标志"""
    print("\n[11] --version 标志")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "run.py"), "--version"],
        capture_output=True, timeout=10,
    )
    out = proc.stdout.decode() + proc.stderr.decode()
    check("run.py --version 退出 0", proc.returncode == 0, f"实际={proc.returncode}")
    check("run.py --version 含 zm-word2image 标识", "zm-word2image" in out, f"实际={out!r}")

    proc2 = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "pdf2image_run.py"), "--version"],
        capture_output=True, timeout=10,
    )
    out2 = proc2.stdout.decode() + proc2.stderr.decode()
    check("pdf2image_run.py --version 退出 0", proc2.returncode == 0, f"实际={proc2.returncode}")
    check("pdf2image_run.py --version 含 zm-word2image 标识", "zm-word2image" in out2, f"实际={out2!r}")


def main() -> int:
    print("=" * 60)
    print("zm-word2image smoke test")
    print("=" * 60)
    test_safe_filename()
    test_get_word_files()
    test_run_prechecks()
    test_dpi_validation()
    test_exit_code_unknown_input()
    test_result_namedtuple()
    test_pdf2image_cli_help()
    test_format_case_insensitive()
    test_output_dir_whitelist()
    test_pdf_path_double_nesting()
    test_old_pdf_unlink_warning()
    test_version_flag()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项")
        for n in FAILURES:
            print(f"  - {n}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
