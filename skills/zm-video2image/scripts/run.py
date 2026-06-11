#!/usr/bin/env python3
"""
zm-video2image: 从视频中抽帧提取图片(PNG/JPG) - 优化版,直接Seek到目标帧
"""

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

from _skill_version import __version__ as SKILL_VERSION


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".m4v", ".3gp", ".ts", ".m2ts"}

# B 轮 P1-1：与 zm-word2image A-3 P0 对齐，禁止向系统目录写入。
# 即使用户把 /etc/videos/ 当作 --path、把 /etc/ 当作 --output-dir
# （满足 ancestor 关系），仍应被拒收。
FORBIDDEN_OUTPUT_DIR_PREFIXES = (
    "/etc", "/var", "/usr", "/boot", "/proc", "/sys", "/root",
    "/run", "/dev", "/sbin", "/bin", "/lib", "/lib64",
)

FAILURE_STATUSES = {
    "missing_input",
    "not_a_video",
    "empty_input_dir",
    "import_failed",
    "open_failed",
    "output_dir_create_failed",
    "save_failed",
}


class Result(NamedTuple):
    status: str
    message: str
    files_created: list[str]
    details: dict


def safe_filename(name: str) -> str:
    cleaned = "".join(c for c in name if unicodedata.category(c)[0] != "C" or c in ("_", "-")).strip()
    if not cleaned:
        raise ValueError(f"视频文件主名仅含非法控制字符,无法派生安全子目录名: {name!r}")
    return cleaned


def get_video_files(path: str) -> list[Path]:
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            return [p]
        else:
            return []
    elif p.is_dir():
        return sorted([f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS and f.is_file()])
    else:
        return []


def _output_subdir_name(video_path: Path, out_dir: Path | None = None) -> str:
    """派生输出子目录名。

    规则:
    - 单文件 + 默认输出(out_dir == video_path.parent):返回裸 stem,与 SKILL.md 描述一致
    - 其他场景(自定义 --output-dir、批量模式):追加父目录前缀防冲突
    """
    stem = safe_filename(video_path.stem)
    parent = video_path.parent.name

    # 单文件默认输出:退化到裸 stem,贴合 SKILL.md 契约
    if out_dir is not None:
        try:
            if out_dir.resolve() == video_path.parent.resolve():
                return stem
        except (OSError, RuntimeError):
            pass

    if parent and parent not in (".", "/") and not stem.startswith(f"{parent}__"):
        return f"{parent}__{stem}"
    return stem


def _validate_output_dir(out_dir: Path, input_path: Path) -> str | None:
    """校验输出目录禁止穿越到无关子树,也禁止写入系统目录。

    接受:out 等于 input、out 是 input 的子目录、out 是 input 的祖先、out 与 input 同级。
    拒绝:完全无关路径(如 /etc/foo)、或命中 FORBIDDEN_OUTPUT_DIR_PREFIXES 的任意祖先。
    返回错误消息或 None
    """
    try:
        out_resolved = out_dir.resolve()
        input_resolved = input_path.resolve()
    except (OSError, RuntimeError) as e:
        return f"无法解析输出目录路径: {e}"

    # B 轮 P1-1：系统目录黑名单。ancestor 关系也救不了 /etc/ /var/ 等。
    # 解析后的 out 路径若其前缀任一节点落在黑名单内,即拒收。
    forbidden_hit = _match_forbidden_prefix(out_resolved)
    if forbidden_hit is not None:
        return (
            f"输出目录 {out_resolved} 命中系统目录黑名单 ({forbidden_hit}),"
            "禁止向系统目录写入。请改用 ~/、./、/tmp/ 等用户级或临时目录。"
        )

    if out_resolved == input_resolved:
        return f"输出目录 {out_resolved} 与输入路径 {input_resolved} 相同,会污染源目录"

    try:
        out_resolved.relative_to(input_resolved)
        return None
    except ValueError:
        pass

    try:
        input_resolved.relative_to(out_resolved)
        return None
    except ValueError:
        pass

    if out_resolved.parent == input_resolved.parent:
        return None

    return (
        f"输出目录 {out_resolved} 与输入路径 {input_resolved} 不在同一子树,"
        "禁止向无关目录写入。请重新指定 --output-dir(必须与 --path 同级、"
        "是 --path 的子目录,或是 --path 的祖先)。"
    )


def _match_forbidden_prefix(out_resolved: Path) -> str | None:
    """检查 out_resolved 的任意祖先节点是否落在 FORBIDDEN_OUTPUT_DIR_PREFIXES 内。

    例如 /etc/foo 返回 "/etc",/var/log/x 返回 "/var",/usr/local/bin 返回 "/usr"。
    """
    for prefix in FORBIDDEN_OUTPUT_DIR_PREFIXES:
        # 拼接 out_resolved 的各层祖先,任一祖先等于 prefix 即命中
        for parent in out_resolved.parents:
            if str(parent) == prefix:
                return prefix
        # out_resolved 自身等于 prefix 的情况(罕见,但要兜住)
        if str(out_resolved) == prefix:
            return prefix
    return None


def extract_frames(
    video_path: Path,
    output_dir: Path,
    img_format: str,
    interval_sec: float | None,
    interval_frame: int | None,
    dry_run: bool = False,
    quiet: bool = False,
) -> Result:
    try:
        import cv2
    except ImportError:
        return Result(
            status="import_failed",
            message="OpenCV (cv2) 未安装,请执行: pip install opencv-python-headless",
            files_created=[],
            details={},
        )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        return Result(
            status="open_failed",
            message=f"无法打开视频文件: {video_path.name}",
            files_created=[],
            details={"video": str(video_path)},
        )

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_fallback = False
    if fps <= 0:
        fps = 30.0
        fps_fallback = True
    if total_frames <= 0:
        total_frames = 0

    if interval_frame is not None and interval_frame > 0:
        frame_step = interval_frame
    elif interval_sec is not None and interval_sec > 0:
        frame_step = max(1, int(round(fps * interval_sec)))
    else:
        frame_step = max(1, int(round(fps)))

    target_indices = list(range(0, total_frames, frame_step)) if total_frames > 0 else []
    estimated_extract = len(target_indices)

    width = len(str(estimated_extract)) if estimated_extract > 0 else 1

    try:
        sub_name = _output_subdir_name(video_path, output_dir)
    except ValueError as e:
        cap.release()
        return Result(
            status="open_failed",
            message=str(e),
            files_created=[],
            details={"video": str(video_path)},
        )
    ext = "jpg" if img_format == "jpg" else "png"
    video_dir = output_dir / sub_name

    if not quiet:
        print(
            f"[info] 开始处理: {video_path.name} | "
            f"分辨率: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} | "
            f"FPS: {fps:.2f} | 总帧数: {total_frames} | "
            f"抽帧间隔: 每 {frame_step} 帧 | 预计输出: {estimated_extract} 帧",
            file=sys.stderr,
            flush=True,
        )

    if dry_run:
        cap.release()
        duration_approx = total_frames / fps if fps > 0 else 0
        return Result(
            status="success",
            message=f"[dry-run] 预计抽取 {estimated_extract} 帧,输出到 {video_dir}/",
            files_created=[],
            details={
                "video": str(video_path),
                "frames_extracted": estimated_extract,
                "frames_expected": estimated_extract,
                "frames_short": 0,
                "total_frames": total_frames,
                "fps": fps,
                "frame_step": frame_step,
                "duration_sec": round(duration_approx, 2) if not fps_fallback else None,
                "duration_unknown": fps_fallback,
                "output_dir": str(video_dir),
                "dry_run": True,
                "read_failure_position": "none",
                "fps_fallback": fps_fallback,
                "seek_mismatch_count": 0,
            },
        )

    try:
        video_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        cap.release()
        return Result(
            status="output_dir_create_failed",
            message=f"无法创建输出子目录 {video_dir}: {e}",
            files_created=[],
            details={"video": str(video_path), "output_dir": str(video_dir)},
        )

    created = []
    saved_count = 0
    read_failures = 0
    seek_mismatch_count = 0
    start_time = time.time()
    last_progress_time = 0

    for i, target_idx in enumerate(target_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
        # 部分容器对精确帧 seek 支持不完整:若 set 后实际位置偏离 target_idx,
        # 记录一次 mismatch 但仍尝试读帧;消费方可通过 details.seek_mismatch_count 知晓
        actual_pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        if actual_pos != target_idx:
            seek_mismatch_count += 1
        ret, frame = cap.read()
        if not ret:
            read_failures += 1
            break

        saved_count += 1
        frame_name = f"frame-{saved_count:0{width}d}.{ext}"
        out_path = video_dir / frame_name
        try:
            if img_format == "jpg":
                ok = cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            else:
                ok = cv2.imwrite(str(out_path), frame)
        except Exception as e:
            cap.release()
            return Result(
                status="save_failed",
                message=f"保存第 {saved_count} 帧失败: {e}",
                files_created=created,
                details={"video": str(video_path), "frame": saved_count, "seek_mismatch_count": seek_mismatch_count},
            )
        if not ok:
            cap.release()
            return Result(
                status="save_failed",
                message=f"保存第 {saved_count} 帧失败: cv2.imwrite 返回 False",
                files_created=created,
                details={"video": str(video_path), "frame": saved_count, "output_path": str(out_path), "seek_mismatch_count": seek_mismatch_count},
            )
        created.append(str(out_path))

        if not quiet:
            now = time.time()
            pct = (i + 1) / len(target_indices) * 100 if target_indices else 0
            if i == 0 or (i + 1) % max(1, len(target_indices) // 10) == 0 or (now - last_progress_time) >= 5:
                elapsed = now - start_time
                eta = (elapsed / (i + 1)) * (len(target_indices) - i - 1) if i > 0 else 0
                print(
                    f"[progress] 已保存 {saved_count}/{estimated_extract} 帧 "
                    f"({pct:.1f}%) | 耗时 {elapsed:.1f}秒 | 预计剩余 {eta:.0f}秒",
                    file=sys.stderr,
                    flush=True,
                )
                last_progress_time = now

    cap.release()
    elapsed = time.time() - start_time

    duration_approx = total_frames / fps if fps > 0 else 0
    duration_unknown = fps_fallback

    short_msg = ""
    read_failure_position = "none"
    if read_failures > 0 or saved_count < estimated_extract:
        if saved_count == 0 and read_failures > 0:
            # 首帧读失败,继续循环没有意义;frames_short 置 0,与 message "共 0 帧" 对齐
            short_msg = " 视频首帧读取失败,共 0 帧"
            read_failure_position = "head"
        else:
            # 中间或末尾帧丢失
            short_msg = f" 末尾 {estimated_extract - saved_count} 帧读取失败"
            read_failure_position = "tail"

    if not quiet:
        suffix = f" | 抽帧速度 {saved_count / elapsed:.1f} 帧/秒" if elapsed > 0 else ""
        print(
            f"[info] 完成: {video_path.name} | 抽取 {saved_count} 帧 | "
            f"耗时 {elapsed:.1f} 秒{suffix}{short_msg}",
            file=sys.stderr,
            flush=True,
        )

    # frames_short:仅在"尝试抽帧但未抽到"时统计;head 失败(saved_count=0)置 0,
    # 与 message "共 0 帧" 语义一致;消费方应同时读 read_failure_position 判断根因
    frames_short = 0 if read_failure_position == "head" else max(0, estimated_extract - saved_count)

    return Result(
        status="success",
        message=f"抽帧成功,共 {saved_count} 帧(视频约 {duration_approx:.1f} 秒,总帧数 {total_frames}){short_msg}",
        files_created=created,
        details={
            "video": str(video_path),
            "frames_extracted": saved_count,
            "frames_expected": estimated_extract,
            "frames_short": frames_short,
            "total_frames": total_frames,
            "fps": fps,
            "frame_step": frame_step,
            "duration_sec": round(duration_approx, 2) if not duration_unknown else None,
            "duration_unknown": duration_unknown,
            "elapsed_sec": round(elapsed, 2),
            "processing_fps": round(saved_count / elapsed, 2) if elapsed > 0 else 0,
            "read_failure_position": read_failure_position,
            "fps_fallback": fps_fallback,
            "seek_mismatch_count": seek_mismatch_count,
        },
    )


def _positive_float(value: str) -> float:
    try:
        v = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是正数,得到 {value!r}")
    if v <= 0:
        raise argparse.ArgumentTypeError(f"必须是正数,得到 {value}")
    return v


def _positive_int(value: str) -> int:
    try:
        v = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是正整数,得到 {value!r}")
    if v <= 0:
        raise argparse.ArgumentTypeError(f"必须是正整数,得到 {value}")
    return v


def run(
    path: str,
    output_dir: str | None,
    img_format: str,
    interval_sec: float | None,
    interval_frame: int | None,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict:
    path_p = Path(path).resolve()

    if not path_p.exists():
        return {"result_status": "missing_input", "summary": "输入路径不存在", "targets": []}

    video_files = get_video_files(path)

    if path_p.is_file() and path_p.suffix.lower() not in VIDEO_EXTENSIONS:
        return {
            "result_status": "not_a_video",
            "summary": f"输入文件不是受支持的视频格式。支持: {', '.join(sorted(VIDEO_EXTENSIONS))}",
            "targets": [],
        }

    if path_p.is_dir() and not video_files:
        return {"result_status": "empty_input_dir", "summary": "目录中没有受支持的视频文件", "targets": []}

    if output_dir:
        out_dir = Path(output_dir).resolve()
        validation_error = _validate_output_dir(out_dir, path_p)
        if validation_error is not None:
            return {
                "result_status": "output_dir_create_failed",
                "summary": validation_error,
                "targets": [],
            }
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"result_status": "output_dir_create_failed", "summary": f"无法创建输出目录: {e}", "targets": []}
    elif path_p.is_file():
        out_dir = path_p.parent
    else:
        out_dir = path_p

    targets = []
    all_created = []
    total_extracted = 0
    total_elapsed = 0.0

    for video_file in video_files:
        result = extract_frames(video_file, out_dir, img_format, interval_sec, interval_frame, dry_run, quiet)
        targets.append({
            "source": str(video_file),
            "status": result.status,
            "message": result.message,
            "output_files": result.files_created,
            "details": result.details,
        })
        all_created.extend(result.files_created)
        total_extracted += result.details.get("frames_extracted", 0)
        total_elapsed += result.details.get("elapsed_sec", 0)

    success_count = sum(1 for t in targets if t["status"] == "success")
    failed_count = len(targets) - success_count

    summary_parts = []
    if dry_run:
        summary_parts.append(f"[dry-run] 预计处理 {len(video_files)} 个视频,共提取 {total_extracted} 帧")
    else:
        if success_count > 0:
            summary_parts.append(f"成功处理 {success_count} 个视频,共提取 {total_extracted} 帧")
        if failed_count > 0:
            summary_parts.append(f"失败 {failed_count} 个")
        if all_created:
            summary_parts.append(f"输出文件: {len(all_created)} 个")
        if total_elapsed > 0:
            summary_parts.append(f"总耗时: {total_elapsed:.1f} 秒")

    return {
        "result_status": "success" if failed_count == 0 else "partial",
        "summary": ";".join(summary_parts) if summary_parts else "完成",
        "targets": targets,
        "files_created": all_created,
        "total_extracted": total_extracted,
        "dry_run": dry_run,
    }


def main():
    parser = argparse.ArgumentParser(
        description="从视频中抽帧提取图片(PNG/JPG),支持单文件和批量目录转换。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --path demo.mp4                       # 单视频,默认每秒一帧,输出 PNG
  %(prog)s --path demo.mp4 --interval-sec 5      # 每 5 秒一帧
  %(prog)s --path demo.mp4 --interval-frame 30   # 每 30 帧一帧
  %(prog)s --path demo.mp4 --format jpg          # 输出 JPG
  %(prog)s --path demo.mp4 --dry-run             # 仅估算,不实际写入
  %(prog)s --path videos/                        # 批量转换目录
  %(prog)s --path videos/ --output-dir out/      # 指定输出目录
        """,
    )
    parser.add_argument("--path", required=True, help="输入视频文件或包含视频的目录(必选)")
    parser.add_argument("--output-dir", help="输出目录(默认写回源文件同目录,且必须在输入路径同级或子目录内,避免向无关位置写入)")
    parser.add_argument("--format", choices=["png", "jpg"], default="png", help="输出图片格式(默认 png)")
    parser.add_argument(
        "--interval-sec",
        type=_positive_float,
        default=None,
        help="按时间间隔抽帧:每 N 秒提取一帧,必须为正数(默认 1.0);与 --interval-frame 互斥",
    )
    parser.add_argument(
        "--interval-frame",
        type=_positive_int,
        default=None,
        help="按帧数间隔抽帧:每 N 帧提取一帧,必须为正整数;与 --interval-sec 互斥",
    )
    parser.add_argument("--dry-run", action="store_true", help="预览模式:只估算输出帧数和目录,不实际写入文件")
    parser.add_argument("--quiet", action="store_true", help="静默模式:不输出进度信息到 stderr")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出结果(供程序调用)")
    parser.add_argument("--version", action="version", version=f"zm-video2image {SKILL_VERSION}")
    args = parser.parse_args()

    if args.interval_sec is not None and args.interval_frame is not None:
        parser.error("--interval-sec 与 --interval-frame 互斥,只能指定其中一个")

    interval_sec = args.interval_sec
    interval_frame = args.interval_frame
    if interval_sec is None and interval_frame is None:
        interval_sec = 1.0

    result = run(args.path, args.output_dir, args.format, interval_sec, interval_frame, args.dry_run, args.quiet)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"zm-video2image 抽帧结果")
        print(f"{'='*50}")
        print(f"状态: {result['result_status']}")
        print(f"汇总: {result['summary']}")
        if result.get("targets"):
            print(f"\n详细:")
            for t in result["targets"]:
                print(f"  来源: {t['source']}")
                print(f"  状态: {t['status']} - {t['message']}")
                if t["output_files"]:
                    shown = t['output_files'][0]
                    extra = f" 等 {len(t['output_files'])} 个" if len(t["output_files"]) > 1 else ""
                    print(f"  输出: {shown}{extra}")
                print()
        if result.get("files_created"):
            print(f"共生成 {len(result['files_created'])} 个图片文件")

    if result["result_status"] in FAILURE_STATUSES:
        sys.exit(1)
    elif result["result_status"] == "partial":
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
