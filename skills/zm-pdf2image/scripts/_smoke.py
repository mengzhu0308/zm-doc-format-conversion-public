#!/usr/bin/env python3
"""
zm-pdf2image 最小自检脚本。

覆盖四类典型输入（不依赖真实 PDF fixture）：
- missing_input：路径不存在
- not_a_pdf：传非 PDF 后缀
- empty_input_dir：空目录
- corrupt_pdf：扩展名是 PDF 但内容损坏

可选：环境同时具备 pdf2image + Pillow 时，跑 5 页成功 / 0 页跳过的 mock 验证。

执行：
    python scripts/_smoke.py
退出码：0 = 全部通过；非 0 = 有断言失败。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent
RUN_SCRIPT = SKILL_DIR / "run.py"


def run_cli(args: list[str]) -> tuple[int, str, str]:
    """同步执行 scripts/run.py；返回 (退出码, stdout, stderr)。"""
    result = subprocess.run(
        [sys.executable, str(RUN_SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


_passed: list[str] = []
_failed: list[tuple[str, str]] = []
_skipped: list[tuple[str, str]] = []

# P2-2：可选日志文件，所有 PASS/FAIL/SKIP 同时写到 stdout + log file
_log_file: "object | None" = None  # 实际为 TextIO，但 stub 用 object 规避严格类型


def _log_write(line: str) -> None:
    print(line)
    f = _log_file
    if f is not None:
        f.write(line + "\n")  # type: ignore[attr-defined]
        f.flush()  # type: ignore[attr-defined]


def record_pass(name: str, detail: str = "") -> None:
    _log_write(f"  PASS  {name}")
    _passed.append(name)


def record_fail(name: str, detail: str = "") -> None:
    suffix = f"  ({detail})" if detail else ""
    _log_write(f"  FAIL  {name}{suffix}")
    _failed.append((name, detail))


def record_skip(name: str, reason: str = "") -> None:
    suffix = f"  ({reason})" if reason else ""
    _log_write(f"  SKIP  {name}{suffix}")
    _skipped.append((name, reason))


def parse_json_output(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        return {"_parse_error": str(e), "_stdout": stdout}


def case_missing_input() -> None:
    print("[missing_input] 路径不存在")
    code, out, _ = run_cli(["--path", "/no/such/path/zzz_9999_smoke", "--json"])
    data = parse_json_output(out)
    record_pass("exit_code == 1") if code == 1 else record_fail("exit_code == 1", f"got {code}")
    actual = data.get("result_status")
    record_pass("status == missing_input") if actual == "missing_input" else record_fail(
        "status == missing_input", f"got {actual!r}")


def case_not_a_pdf() -> None:
    print("[not_a_pdf] 传 .txt 后缀")
    with tempfile.TemporaryDirectory() as td:
        not_pdf = Path(td) / "demo.txt"
        not_pdf.write_text("not a pdf at all", encoding="utf-8")
        code, out, _ = run_cli(["--path", str(not_pdf), "--json"])
        data = parse_json_output(out)
        record_pass("exit_code == 1") if code == 1 else record_fail("exit_code == 1", f"got {code}")
        actual = data.get("result_status")
        record_pass("status == not_a_pdf") if actual == "not_a_pdf" else record_fail(
            "status == not_a_pdf", f"got {actual!r}")


def case_empty_input_dir() -> None:
    print("[empty_input_dir] 空目录")
    with tempfile.TemporaryDirectory() as td:
        code, out, _ = run_cli(["--path", td, "--json"])
        data = parse_json_output(out)
        record_pass("exit_code == 1") if code == 1 else record_fail("exit_code == 1", f"got {code}")
        actual = data.get("result_status")
        record_pass("status == empty_input_dir") if actual == "empty_input_dir" else record_fail(
            "status == empty_input_dir", f"got {actual!r}")


def case_corrupt_pdf() -> None:
    print("[corrupt_pdf] 扩展名为 .pdf 但内容损坏")
    # pdf2image 未装时损坏 PDF 走不到真实 convert_from_path，跳过整 case
    try:
        import pdf2image  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        record_skip("case_corrupt_pdf", "pdf2image 未安装（需在 agent-skills 环境跑）")
        return
    with tempfile.TemporaryDirectory() as td:
        corrupt = Path(td) / "broken.pdf"
        # 看起来像 PDF 头但没有任何有效对象
        corrupt.write_bytes(b"%PDF-1.4\n%corrupt payload\x00\x01\x02not-a-pdf\n")
        code, out, _ = run_cli(["--path", str(corrupt), "--json"])
        data = parse_json_output(out)
        # 损坏 PDF 走 convert_from_path 抛错：
        # - 有 poppler 时归为 conversion_failed → 退出码 2 (partial)
        # - 无 poppler 时归为 poppler_missing → 退出码 1
        # 两种都算"转换失败已捕获"，接受 1 或 2
        record_pass("exit_code in {1, 2}") if code in {1, 2} else record_fail(
            "exit_code in {1, 2}", f"got {code}")
        targets = data.get("targets") or []
        target_statuses = [t.get("status") for t in targets]
        is_failure = any(
            s in {"conversion_failed", "poppler_missing", "save_failed", "import_failed"}
            for s in target_statuses
        )
        record_pass("target status is failure") if is_failure else record_fail(
            "target status is failure", f"got {target_statuses!r}")


def case_permission_denied() -> None:
    """目录无读权限；Windows 或 root 下无法稳定复现时跳过。"""
    print("[permission_denied] 目录无读权限")
    if sys.platform == "win32":
        record_skip("case_permission_denied", "Windows 平台无法用 chmod 稳定复现")
        return
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        record_skip("case_permission_denied", "root 用户可绕过文件权限")
        return
    with tempfile.TemporaryDirectory() as td:
        protected = Path(td) / "protected"
        protected.mkdir()
        protected.chmod(0o000)
        try:
            code, out, _ = run_cli(["--path", str(protected), "--json"])
            data = parse_json_output(out)
            record_pass("exit_code == 1") if code == 1 else record_fail("exit_code == 1", f"got {code}")
            actual = data.get("result_status")
            ok = actual in {"permission_denied", "empty_input_dir"}
            record_pass("status is permission/empty") if ok else record_fail(
                "status is permission/empty", f"got {actual!r}")
        finally:
            protected.chmod(0o755)


def case_mocked_success_and_zero_page() -> None:
    """不依赖 poppler：在同进程 monkeypatch `run._convert_from_path`，直接调用 `run.run()`。

    原方案用 subprocess.run 启动子进程跑 CLI，monkeypatch 不会跨进程传递；改为
    直接调用 `run.run()`（公共 API），跑 5 页成功 + 0 页跳过两个分支。
    需要 Pillow 生成 fake image；pdf2image 真实实现不依赖（因为我们替换了
    `_convert_from_path`）。
    """
    print("[mocked 5 page + 0 page]  in-process monkeypatch of run._convert_from_path")
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        record_skip("mocked 5 page + 0 page", "Pillow 未安装")
        return

    # 直接 import run 模块以访问 _convert_from_path 与 run()
    import importlib.util
    spec = importlib.util.spec_from_file_location("zm_pdf2image_run", RUN_SCRIPT)
    assert spec and spec.loader
    run_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_mod)

    # 保存原 _convert_from_path 引用，函数返回时复原
    original_cfp = run_mod._convert_from_path  # type: ignore[attr-defined]

    class _FakeImage:
        def __init__(self, idx: int) -> None:
            self.idx = idx

        def save(self, path: str, format: str) -> None:
            img = Image.new("RGB", (10, 10), color=0xFFFFFF)
            img.save(path, format=format)

    def make_cfp(n: int):
        def fake_convert(*_args, **_kwargs):
            return [_FakeImage(i) for i in range(1, n + 1)]
        return fake_convert

    try:
        with tempfile.TemporaryDirectory() as td:
            fake_pdf = Path(td) / "demo.pdf"
            fake_pdf.write_bytes(b"%PDF-1.4\n")

            # 5 页：monkeypatch 注入 5 页 fake image
            run_mod._convert_from_path = lambda: make_cfp(5)  # type: ignore[attr-defined]
            out_dir = Path(td) / "out_5"
            result = run_mod.run(str(fake_pdf), str(out_dir), "png", 300, False)
            record_pass("5-page result_status == success") if result["result_status"] == "success" else record_fail(
                "5-page result_status == success", f"got {result['result_status']!r}")
            nfiles = len(result.get("files_created", []))
            record_pass("5-page files_created == 5") if nfiles == 5 else record_fail(
                "5-page files_created == 5", f"got {nfiles}")
            sub = out_dir / "demo"
            record_pass("5-page subdir exists") if sub.is_dir() else record_fail(
                "5-page subdir exists", f"path={sub}")
            pngs = list(sub.glob("image-*.png"))
            record_pass("5-page subdir has 5 files") if len(pngs) == 5 else record_fail(
                "5-page subdir has 5 files", f"got {len(pngs)}")
            # 验证命名宽度：5 页 < 10 → width=1 → image-1.png .. image-5.png
            if pngs:
                names = sorted(p.name for p in pngs)
                expected = [f"image-{i}.png" for i in range(1, 6)]
                record_pass("5-page naming width=1") if names == expected else record_fail(
                    "5-page naming width=1", f"got {names!r}")

            # 0 页：monkeypatch 注入空列表
            run_mod._convert_from_path = lambda: make_cfp(0)  # type: ignore[attr-defined]
            out_dir0 = Path(td) / "out_0"
            result0 = run_mod.run(str(fake_pdf), str(out_dir0), "png", 300, False)
            record_pass("0-page result_status == success") if result0["result_status"] == "success" else record_fail(
                "0-page result_status == success", f"got {result0['result_status']!r}")
            nfiles0 = len(result0.get("files_created", []))
            record_pass("0-page files_created == 0") if nfiles0 == 0 else record_fail(
                "0-page files_created == 0", f"got {nfiles0}")
            sub0 = out_dir0 / "demo"
            record_pass("0-page subdir NOT created") if not sub0.exists() else record_fail(
                "0-page subdir NOT created", f"path={sub0}")
    finally:
        run_mod._convert_from_path = original_cfp  # type: ignore[attr-defined]


def main() -> None:
    global _log_file  # noqa: PLW0603
    import argparse
    parser = argparse.ArgumentParser(description="zm-pdf2image 最小自检")
    parser.add_argument(
        "--log-file",
        default=None,
        help="P2-2：把 PASS/FAIL/SKIP 同时写到指定日志文件",
    )
    args = parser.parse_args()

    if args.log_file:
        _log_file = open(args.log_file, "w", encoding="utf-8")
    try:
        cases = [
            case_missing_input,
            case_not_a_pdf,
            case_empty_input_dir,
            case_corrupt_pdf,
            case_permission_denied,
            case_mocked_success_and_zero_page,
        ]
        for c in cases:
            try:
                c()
            except Exception as e:  # noqa: BLE001
                record_fail(f"{c.__name__} 自身异常", str(e))

        passed = len(_passed)
        failed = len(_failed)
        skipped = len(_skipped)
        summary = f"\n=== smoke summary: {passed} passed, {failed} failed, {skipped} skipped ==="
        _log_write(summary)
        sys.exit(0 if failed == 0 else 1)
    finally:
        if _log_file is not None:
            _log_file.close()  # type: ignore[attr-defined]
            _log_file = None


if __name__ == "__main__":
    main()
