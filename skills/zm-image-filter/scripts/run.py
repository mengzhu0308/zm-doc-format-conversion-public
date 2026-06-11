#!/usr/bin/env python3
from __future__ import annotations

"""
zm-image-filter: 基于感知哈希(dHash)过滤内容相同的图片
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import NamedTuple


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class Result(NamedTuple):
    status: str
    message: str
    files_kept: list[str]
    files_skipped: list[str]
    details: dict


def _natural_key(name: str) -> list:
    """自然排序键：把文件名按数字片段拆开，数字转 int 后比较。"""
    parts = re.split(r"(\d+)", name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _non_negative_int(value: str) -> int:
    """argparse type: 非负整数。"""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"需要整数，得到 {value!r}") from e
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"不能为负数: {value}")
    return ivalue


def _hash_size(value: str) -> int:
    """argparse type: hash_size 在 1..32 之间（hash_size*hash_size 上限 1024 位）。"""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"需要整数，得到 {value!r}") from e
    if ivalue < 1 or ivalue > 32:
        raise argparse.ArgumentTypeError(f"必须 >= 1 且 <= 32: {value}")
    return ivalue


def _resolve_lanczos():
    """兼容 Pillow 9.1 前后版本的 LANCZOS 重采样。"""
    from PIL import Image
    return getattr(Image, "Resampling", Image).LANCZOS  # type: ignore[attr-defined]


def _compute_dhash_pil(image_path: Path, hash_size: int) -> str:
    """纯 PIL 实现 dHash，返回 hash_size x hash_size 位二进制字符串。"""
    from PIL import Image

    resampling = _resolve_lanczos()
    with Image.open(image_path) as img:
        img = img.convert("L").resize(
            (hash_size + 1, hash_size), resampling
        )
        pixels = list(img.getdata())

    hash_bits = []
    stride = hash_size + 1
    for row in range(hash_size):
        base = row * stride
        for col in range(hash_size):
            left = pixels[base + col]
            right = pixels[base + col + 1]
            hash_bits.append("1" if left > right else "0")
    return "".join(hash_bits)


class _ImageHashWrapper:
    """统一 imagehash.ImageHash 和纯 PIL 字符串哈希的包装器。"""

    def __init__(self, raw):
        self.raw = raw

    def __sub__(self, other) -> int:
        if hasattr(self.raw, "__sub__"):
            return self.raw - other.raw
        return sum(c1 != c2 for c1, c2 in zip(self.raw, other.raw))


# 优先尝试 imagehash；首次 compute_dhash 调用时按实际 hash_size 做 smoke test；
# 若 smoke test 失败则回退纯 PIL 实现
_IMAGEHASH_IMPORTED = False
try:
    import imagehash  # type: ignore[import-unresolved, import-not-found]
    _IMAGEHASH_IMPORTED = True
except Exception:
    pass

# 按 hash_size 维度的 imagehash 可用性缓存，避免每次 compute_dhash 都重跑 smoke test
_IMAGEHASH_AVAILABLE_FOR: dict[int, bool] = {}


def _check_imagehash(hash_size: int) -> bool:
    """按用户实际 hash_size 验证 imagehash.dhash 是否可用。"""
    if not _IMAGEHASH_IMPORTED:
        return False
    if hash_size in _IMAGEHASH_AVAILABLE_FOR:
        return _IMAGEHASH_AVAILABLE_FOR[hash_size]
    try:
        from PIL import Image as _PILImage
        _smoke = _PILImage.new("L", (16, 16), 0)
        _ = imagehash.dhash(_smoke, hash_size=hash_size)  # type: ignore[possibly-unbound]
        _IMAGEHASH_AVAILABLE_FOR[hash_size] = True
        del _smoke
        return True
    except Exception:
        _IMAGEHASH_AVAILABLE_FOR[hash_size] = False
        return False


def compute_dhash(image_path: Path, hash_size: int = 8):
    """计算图片的 dHash（差值哈希），返回统一包装器。"""
    if _check_imagehash(hash_size):
        from PIL import Image
        with Image.open(image_path) as img:
            return _ImageHashWrapper(imagehash.dhash(img, hash_size=hash_size))  # type: ignore[possibly-unbound]
    return _ImageHashWrapper(_compute_dhash_pil(image_path, hash_size))


def _print_progress(
    i: int,
    total: int,
    kept: int,
    skipped: int,
    start_time: float,
    last_progress_time: float,
    quiet: bool,
) -> float:
    """打印 [progress] 行；quiet 时直接返回。返回更新后的 last_progress_time。"""
    if quiet:
        return last_progress_time
    now = time.time()
    pct = (i + 1) / total * 100
    if i == 0 or (i + 1) % max(1, total // 10) == 0 or (now - last_progress_time) >= 5:
        elapsed = now - start_time
        eta = (elapsed / (i + 1)) * (total - i - 1) if i > 0 else 0
        print(
            f"[progress] {i + 1}/{total} "
            f"({pct:.1f}%) | 保留 {kept} | 跳过 {skipped} | "
            f"耗时 {elapsed:.1f}秒 | 预计剩余 {eta:.0f}秒",
            file=sys.stderr,
            flush=True,
        )
        return now
    return last_progress_time


def filter_images(
    input_dir: Path,
    output_dir: Path,
    threshold: int,
    hash_size: int,
    dry_run: bool = False,
    quiet: bool = False,
    overwrite: bool = False,
) -> Result:
    image_files = sorted(
        [
            f for f in input_dir.iterdir()
            if f.suffix.lower() in IMAGE_EXTENSIONS
            and f.is_file()
            and not f.name.startswith(".")
        ],
        key=lambda p: _natural_key(p.name),
    )

    if not image_files:
        return Result(
            status="empty_input_dir",
            message="目录中没有受支持的图片文件",
            files_kept=[],
            files_skipped=[],
            details={"input_dir": str(input_dir)},
        )

    if not quiet:
        mode = "预览模式" if dry_run else "正式执行"
        print(
            f"[info] 开始处理: {input_dir} | 共 {len(image_files)} 张图片 | "
            f"阈值: {threshold} | 哈希尺寸: {hash_size}x{hash_size} | {mode}",
            file=sys.stderr,
            flush=True,
        )

    hashes: list = []
    kept: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    failed: list[str] = []
    start_time = time.time()
    last_progress_time = 0.0

    for i, img_path in enumerate(image_files):
        try:
            h = compute_dhash(img_path, hash_size)
        except Exception as e:
            failed.append(str(img_path))
            if not quiet:
                print(
                    f"[warn] 计算哈希失败,跳过: {img_path.name} - {e}",
                    file=sys.stderr,
                    flush=True,
                )
            last_progress_time = _print_progress(
                i, len(image_files), len(kept), len(skipped),
                start_time, last_progress_time, quiet,
            )
            continue

        is_duplicate = False
        for existing_hash, _ in hashes:
            if (h - existing_hash) <= threshold:
                skipped.append(str(img_path))
                is_duplicate = True
                break

        if not is_duplicate:
            hashes.append((h, img_path))
            dest = output_dir / img_path.name
            if dry_run:
                # dry-run 模式：files_kept 统一用源路径（与 real-run 对齐），
                # 目标冲突时不再误把"未复制"的文件计入 kept
                if dest.exists() and not overwrite:
                    conflicts.append(str(img_path))
                else:
                    kept.append(str(img_path))
            elif dest.exists() and not overwrite:
                conflicts.append(str(img_path))
                if not quiet:
                    print(
                        f"[warn] 目标已存在,跳过: {img_path.name} "
                        f"（使用 --overwrite 覆盖）",
                        file=sys.stderr,
                        flush=True,
                    )
            else:
                try:
                    try:
                        output_dir.mkdir(parents=True, exist_ok=True)
                    except OSError as e:
                        # 把目录创建失败显式上抛，由外层统一报错
                        raise OSError(f"无法创建输出目录 {output_dir}: {e}") from e
                    shutil.copy2(img_path, dest)
                    # files_kept 统一为源路径（与 dry-run 对齐；目标目录在 details.output_dir 体现）
                    kept.append(str(img_path))
                except Exception as e:
                    if not quiet:
                        print(
                            f"[warn] 复制失败,跳过: {img_path.name} - {e}",
                            file=sys.stderr,
                            flush=True,
                        )
                    # 复制失败的图也计入 failed 并从 hashes 移除（无条件 pop：
                    # 本轮刚 push 的就是这张图，`is` 比较会被对象身份判定
                    # 跳过——之前的死代码会导致失败图留在 hashes 中污染去重状态）
                    hashes.pop()
                    failed.append(str(img_path))
                    continue

        last_progress_time = _print_progress(
            i, len(image_files), len(kept), len(skipped),
            start_time, last_progress_time, quiet,
        )

    elapsed = time.time() - start_time

    if not quiet:
        print(
            f"[info] 完成: 保留 {len(kept)} 张 | 跳过 {len(skipped)} 张 | "
            f"冲突 {len(conflicts)} 张 | 失败 {len(failed)} 张 | "
            f"耗时 {elapsed:.2f} 秒",
            file=sys.stderr,
            flush=True,
        )

    if dry_run:
        note = "（实际未创建）" if not output_dir.exists() else ""
        summary_msg = (
            f"[dry-run] 预计保留 {len(kept)} 张, 跳过 {len(skipped)} 张"
            + (f", 目标冲突 {len(conflicts)} 张" if conflicts else "")
            + (f", 失败 {len(failed)} 张" if failed else "")
            + f", 输出到 {output_dir}/{note}"
        )
    else:
        summary_msg = (
            f"过滤完成,保留 {len(kept)} 张,跳过 {len(skipped)} 张"
            + (f",目标冲突 {len(conflicts)} 张" if conflicts else "")
            + (f",失败 {len(failed)} 张" if failed else "")
        )

    details: dict = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "total_images": len(image_files),
        "kept": len(kept),
        "skipped": len(skipped),
        "conflicts": len(conflicts),
        "failed": len(failed),
        "threshold": threshold,
        "hash_size": hash_size,
        "dry_run": dry_run,
        "elapsed_sec": round(elapsed, 2),
    }
    if conflicts:
        details["overwrite"] = overwrite
    if failed:
        details["files_failed"] = failed
    # files_source 在 dry-run 和 real-run 都写入，与两种模式对齐语义；
    # size guard：超过 1000 张图时不写完整列表，避免 --json 模式 OOM 或 stdout 刷爆
    if len(image_files) <= 1000:
        details["files_source"] = [str(p) for p in image_files]
    else:
        # 截断：保留前 50 + 末尾 50，加 files_source_truncated 标记
        details["files_source_truncated"] = True
        details["files_source"] = (
            [str(p) for p in image_files[:50]]
            + [f"... (+{len(image_files) - 100} truncated) ..."]
            + [str(p) for p in image_files[-50:]]
        )

    return Result(
        status="success",
        message=summary_msg,
        files_kept=kept,
        files_skipped=skipped,
        details=details,
    )


def run(
    input_path: str,
    output_dir: str | None,
    threshold: int,
    hash_size: int,
    dry_run: bool = False,
    quiet: bool = False,
    overwrite: bool = False,
) -> dict:
    # raw 路径（保留用户输入形态，规范化 .. 但不解析符号链接），用于默认 out_dir 的父目录推导
    # 用 os.path.abspath 而非 Path.absolute()：前者会做 normpath 消解 `..`，后者不会
    input_p_raw = Path(os.path.abspath(os.path.expanduser(input_path)))
    # resolved 路径（解析符号链接），用于 exists/is_dir 检查
    input_p = input_p_raw.resolve()

    if not input_p.exists():
        return {
            "result_status": "missing_input",
            "summary": "输入路径不存在",
            "files_kept": [],
            "files_skipped": [],
            "details": {"input_dir": str(input_p_raw)},
        }

    if not input_p.is_dir():
        return {
            "result_status": "not_a_directory",
            "summary": "输入路径不是目录",
            "files_kept": [],
            "files_skipped": [],
            "details": {"input_dir": str(input_p_raw)},
        }

    if output_dir:
        out_dir = Path(output_dir).expanduser().absolute()
    else:
        # 默认 out_dir 使用 raw 父目录，避免符号链接把输出目录放到非预期的解析后父目录
        out_dir = input_p_raw.parent / f"{input_p_raw.name}_filter"

    # 防止输出目录与输入目录相同或其父目录
    input_resolved = input_p.resolve()
    out_resolved = out_dir.resolve()
    if out_resolved == input_resolved or out_resolved in input_resolved.parents:
        return {
            "result_status": "invalid_output_dir",
            "summary": "输出目录不能与输入目录相同或为其父目录",
            "files_kept": [],
            "files_skipped": [],
            "details": {
                "input_dir": str(input_resolved),
                "output_dir": str(out_resolved),
            },
        }

    # 提前 mkdir 并显式捕获 OSError，让 output_dir_create_failed 真正可达
    out_dir_created_by_us = False
    if not dry_run:
        out_dir_created_by_us = not out_dir.exists()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {
                "result_status": "output_dir_create_failed",
                "summary": f"无法创建输出目录: {e}",
                "files_kept": [],
                "files_skipped": [],
                "details": {
                    "input_dir": str(input_resolved),
                    "output_dir": str(out_resolved),
                    "error": str(e),
                },
            }

    result = filter_images(
        input_p, out_dir, threshold, hash_size, dry_run, quiet, overwrite
    )

    # real-run + 我们刚创建的 output_dir + 一张图都没成功保留时，
    # 清理空目录避免留下垃圾（仅当 output_dir 之前不存在且当前为空）
    if (
        not dry_run
        and out_dir_created_by_us
        and not result.files_kept
        and out_dir.exists()
    ):
        try:
            out_dir.rmdir()  # 仅当空目录才能成功，非空抛 OSError 被吞
        except OSError:
            pass

    return {
        "result_status": result.status,
        "summary": result.message,
        "files_kept": result.files_kept,
        "files_skipped": result.files_skipped,
        "details": result.details,
    }


def main():
    parser = argparse.ArgumentParser(
        description="基于感知哈希(dHash)过滤内容相同的图片,保留不重复图片到 _filter 目录。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --input /path/imgs/                       # 过滤目录,输出到 /path/imgs_filter/
  %(prog)s --input /path/imgs/ --dry-run             # 预览模式,只估算不复制
  %(prog)s --input /path/imgs/ --threshold 3         # 更严格的去重(汉明距离<=3)
  %(prog)s --input /path/imgs/ --output-dir /out/    # 指定输出目录
  %(prog)s --input /path/imgs/ --json --quiet        # JSON 输出供程序调用
        """,
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="显示 skill 版本后退出",
    )
    parser.add_argument(
        "--input",
        required=False,
        default=None,
        help="输入图片目录（必选，--version 时可省略）",
    )
    parser.add_argument(
        "--output-dir",
        help="输出目录（默认: 输入目录同级创建 {dirname}_filter）",
    )
    parser.add_argument(
        "--threshold",
        type=_non_negative_int,
        default=5,
        help="汉明距离阈值,小于等于此值视为相同图片（默认 5, 不能为负）",
    )
    parser.add_argument(
        "--hash-size",
        type=_hash_size,
        default=8,
        help="dHash 尺寸,生成 hash_size x hash_size 位哈希（默认 8, 必须 >= 1）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式:只估算保留/跳过数量,不实际复制文件",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖输出目录中已存在的同名文件（默认跳过）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式:不输出进度信息到 stderr",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供程序调用）",
    )

    args = parser.parse_args()

    if args.version:
        # 从 VERSION.yaml 读取版本号；读不到时回退到 "unknown"
        try:
            import yaml  # type: ignore[import-unresolved, import-not-found]
            version_file = Path(__file__).resolve().parent.parent / "VERSION.yaml"
            if version_file.exists():
                info = yaml.safe_load(version_file.read_text(encoding="utf-8"))
                version = (info or {}).get("skill_info", {}).get("version", "unknown")
            else:
                version = "unknown"
        except Exception:
            version = "unknown"
        print(f"zm-image-filter {version}")
        sys.exit(0)

    if not args.input:
        # status 名重命名为 `missing_arg_input`（参数层）与运行时的
        # `missing_input`（路径层）分离；退出码统一为 2
        if args.json:
            err = {
                "result_status": "missing_arg_input",
                "summary": "缺少必选参数 --input",
                "files_kept": [],
                "files_skipped": [],
                "details": {},
            }
            print(json.dumps(err, ensure_ascii=False))
        else:
            print("error: 缺少必选参数 --input", file=sys.stderr)
        sys.exit(2)

    result = run(
        args.input, args.output_dir, args.threshold, args.hash_size,
        args.dry_run, args.quiet, args.overwrite,
    )

    if args.json:
        # --json 模式下把 details.output_dir 改为相对 input_dir 的相对路径，
        # 避免把本地绝对路径泄露到 CI 日志或共享 JSON。跨盘符等异常时回退到原值
        try:
            in_dir = result.get("details", {}).get("input_dir", "")
            out_dir = result.get("details", {}).get("output_dir", "")
            if in_dir and out_dir:
                rel_out = os.path.relpath(out_dir, in_dir)
                if not rel_out.startswith(".."):
                    result["details"]["output_dir"] = rel_out
        except (ValueError, OSError):
            pass
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-image-filter 过滤结果")
        print(f"{'='*50}")
        print(f"状态: {result['result_status']}")
        print(f"汇总: {result['summary']}")
        if result.get("files_kept"):
            print(f"\n保留文件: {len(result['files_kept'])} 个")
            for f in result["files_kept"][:5]:
                print(f"  {f}")
            if len(result["files_kept"]) > 5:
                print(f"  ... 共 {len(result['files_kept'])} 个")
        if result.get("files_skipped"):
            print(f"\n跳过文件: {len(result['files_skipped'])} 个")
        d = result.get("details", {})
        if d.get("conflicts"):
            print(f"\n目标冲突: {d['conflicts']} 个（使用 --overwrite 覆盖）")
        if d.get("elapsed_sec") is not None:
            print(f"\n耗时: {d['elapsed_sec']} 秒")

    if result["result_status"] in (
        "missing_input", "not_a_directory", "output_dir_create_failed",
        "empty_input_dir", "invalid_output_dir",
    ):
        sys.exit(1)
    elif result["result_status"] == "missing_arg_input":
        # 参数层缺 --input 由前面 if 块已经处理（exit 2），这里兜底
        sys.exit(2)
    elif result["result_status"] == "success":
        # 部分失败（计算失败或复制失败）时返回 2，便于 CI 区分"完全成功"与"部分成功"
        failed_count = result.get("details", {}).get("failed", 0)
        if failed_count and failed_count > 0:
            sys.exit(2)
        sys.exit(0)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
