---
name: zm-video2image
description: >-
  从本地视频抽帧提取 PNG/JPG。当用户需要提取帧、截取视频画面、视频转图片序列、抽帧、提取关键帧，或提到 video to images、frame extraction、视频转图片、视频截图时触发。支持单文件或文件夹批量输入（仅当前层视频文件，不递归）。长视频（超过 10 分钟）建议先用 `--dry-run` 预览输出规模。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" [args]
---

# zm-video2image

## 核心合同

- 只接受本地路径，不接受 URL。
- 单文件输入：受支持的视频文件路径，输出到同目录或指定输出目录。
- 目录输入：扫描当前层所有受支持的视频文件，批量抽帧，不递归。
- 输出格式默认 PNG，可选 JPG。
- 每张提取的帧保存为独立图片，子目录命名规则：如果输出目录与 video 父目录相同（如单文件输入且未指定 `--output-dir`、或目录输入且未指定 `--output-dir`），则使用裸主名 `原主名/frame-N.扩展名`；否则（即指定了与 video 父目录不同的 `--output-dir`）追加父目录前缀，写作 `<父目录>__<原主名>/frame-N.扩展名`，避免不同父目录同名视频互相覆盖。编号采用固定宽度零填充，宽度根据预计提取帧数动态决定（如少于 10 帧时为 `demo/frame-1.png`，10–99 帧时为 `demo/frame-01.png`）。**v2 命名变更**：从 v1.x 的 `frame_NNNN.ext` 改为 `frame-NN.ext`（连字符替代下划线），下游消费方请同步更新。
- 不改写原始视频文件。
- 默认会复用已存在的同名输出子目录；若帧图片同名，脚本会按当前结果覆盖写入。
- 批量处理不同子目录的同名视频时，输出子目录会自动追加父目录前缀（`<父目录>__<stem>`），避免互相覆盖。
- `--output-dir` 必须在输入路径的同级、子目录或祖先目录内，禁止向无关子树写入。
- `--interval-sec` 与 `--interval-frame` 互斥；都未指定时默认每秒一帧；两者都必须为正数。
- 依赖 `opencv-python-headless` Python 包。`.ts` / `.m2ts` 容器需要系统已安装 ffmpeg 并被 OpenCV 链接，否则可能 `open_failed`。

## skill_mode 说明

`metadata.skill_mode: hybrid` 表示本 skill 的运行链路同时包含硬编码流程与 AI 推理两部分：

- **硬编码（脚本决定）**：路径校验、OpenCV 抽帧与回退、图片落盘、退出码、命名格式、互斥校验。
- **AI 推理（消费方决定）**：是否触发本 skill（命中 description 触发词后）、抽帧间隔与默认值、长视频是否先 `--dry-run` 预览、单文件 vs 批量模式选择、自定义 `--output-dir` 路径。输出帧编号宽度（`width = len(str(estimated_extract))`）由脚本硬编码决定；v1 命名格式在 v2.0.0 已删除，无可选项。
- 消费方在改写本 skill 时只应调整 AI 推理相关文案，硬编码行为须与本文件契约保持一致。

## 不适用场景

- **网络流 / RTSP / RTMP / HLS**：只接受本地文件路径，不接受 URL；如需抓取网络流，请先用其他工具下载到本地。
- **加密 / DRM 视频**：OpenCV 不会自动解密，读取会失败。
- **字幕流 / 音轨抽取**：本 skill 仅抽视频帧，不抽字幕或音频。
- **实时流式增量处理**：每次运行是离线的批处理，不支持持续监听输入。
- **极长视频（> 数小时）按默认间隔**：可能产生数万帧、磁盘吃紧；必须先 `--dry-run` 估算并与用户确认是否增大间隔。
- **无 ffmpeg 后端时的 TS 类容器**：`.ts` / `.m2ts` 在 OpenCV 没链接 ffmpeg 时会 `open_failed`，需先 `apt/brew install ffmpeg` 并重装 opencv。
- **严重损坏或关键帧缺失的视频**：脚本仍会运行但 `details.read_failure_position` 报 `head` 或 `tail`，`details.frames_extracted` 可能为 0；请改用修复后的源文件，或先用 `ffprobe` 检查关键帧分布。
- **不支持的容器且无 ffmpeg 回退**：若 OpenCV 无法 seek 到目标帧（`details.seek_mismatch_count > 0`），抽帧结果可能错位；建议先转封装为 `.mp4` 再处理。

## 可复用资源

- `scripts/run.py`
  负责输入预检、批量枚举、OpenCV 视频读取、按间隔抽帧、图片落盘和结构化结果输出。

## 运行前提

脚本在 `agent-skills` Python 环境中运行，依赖 `opencv-python`。未安装时执行：

```bash
conda run -n agent-skills pip install opencv-python-headless
```

## 输入输出约定

| 输入类型 | 示例 | 输出 |
|---|---|---|
| 单文件 | `/path/demo.mp4` | `/path/demo/frame-1.png`，`/path/demo/frame-2.png`，...（少于 10 帧时）或 `frame-01.png`（10–99 帧时）等 |
| 单文件 + 输出目录 | `--output-dir /out/` `/path/demo.mp4` | `/out/path__demo/frame-1.png`，...（带父目录前缀防冲突） |
| 目录 | `/path/videos/` | `/path/videos/demo1/frame-1.png`，`/path/videos/demo2/frame-1.png`，... |
| 目录 + 输出目录 | `--output-dir /out/` `/path/videos/` | `/out/videos__demo1/frame-1.png`，...（带父目录前缀防冲突） |

输出格式通过 `--format png` 或 `--format jpg` 指定，默认 PNG。

抽帧间隔通过 `--interval-sec`（按时间，如每 5 秒一帧）或 `--interval-frame`（按帧数，如每 30 帧一帧）指定，两者互斥；都未指定时默认每秒一帧。

## 默认流程

1. 验证 `--path` 是否存在，区分单文件与目录。
2. 校验 `--interval-sec` / `--interval-frame` 为正数；若两者都显式给定则报错。
3. **长视频预估**：时长超过 10 分钟或不确定输出规模时，先 `--dry-run` 预览帧数、分辨率和输出目录，确认后再执行。
4. 目录输入仅扫描当前层视频文件，不递归。
5. 默认输出到源文件同目录；提供 `--output-dir` 时必须仍在输入路径同级、子目录或祖先目录内，并写入指定目录。
6. 单文件 + 默认输出时，输出子目录为裸 `<主名>`（与本节表格一致）；批量模式或自定义 `--output-dir` 时，输出子目录为 `<父目录>__<主名>` 以防不同父目录同名视频互相覆盖。
7. 处理中定期向 stderr 输出进度（`--quiet` 关闭），末尾帧读取失败时会在 message 中追加提示，最后汇总结果。

## 本地直跑

安装态调用（推荐，使用 `compatibility.runtime.call_command`）：

```bash
# 单视频（默认每秒一帧，PNG）
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/demo.mp4

# 调整抽帧密度、格式或输出目录
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/demo.mp4 --interval-sec 5
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/demo.mp4 --interval-frame 30
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/demo.mp4 --format jpg --output-dir /out/

# 批量转换目录
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/videos/ --output-dir /out/

# 预览模式（不写入，只估算规模）
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/demo.mp4 --dry-run

# 查看帮助
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --help

# 查看版本
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --version
```

源码态调用（仅在被测 skill 源码本身时使用）：

```bash
conda run -n agent-skills python skills/zm-video2image/scripts/run.py --path /path/demo.mp4
```

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--path` | 输入视频文件或包含视频的目录（必选） | - |
| `--output-dir` | 输出目录，必须在 `--path` 同级、子目录或祖先目录内；不指定则写到源文件同目录 | 源文件同目录 |
| `--format` | 输出格式：`png` 或 `jpg` | png |
| `--interval-sec` | 按时间间隔抽帧：每 N 秒一帧，必须为正数 | 1.0（当 `--interval-frame` 未指定时） |
| `--interval-frame` | 按帧数间隔抽帧：每 N 帧一帧，必须为正整数；与 `--interval-sec` 互斥 | - |
| `--dry-run` | 预览模式：只估算输出帧数和目录，不实际写入文件 | False |
| `--quiet` | 静默模式：不输出进度信息到 stderr | False |
| `--json` | 以 JSON 格式输出结果（供程序调用） | False |
| `--version` | 打印 skill 版本 | - |
| `--help` | 显示帮助 | - |

## 失败口径

| 状态码 | 退出码 | 含义 |
|---|---|---|
| `missing_input` | 1 | 输入路径不存在 |
| `not_a_video` | 1 | 输入文件不是受支持的视频格式 |
| `empty_input_dir` | 1 | 目录中没有受支持的视频文件 |
| `import_failed` | 1 | OpenCV (cv2) 未安装 |
| `open_failed` | 1 | 无法打开视频文件（格式不支持、文件损坏或主名仅含控制字符） |
| `output_dir_create_failed` | 1 | 无法创建输出目录，或 `--output-dir` 越界 |
| `save_failed` | 1 | 保存某帧图片失败 |
| `partial` | 2 | 批量处理中部分视频失败 |
| `success` | 0 | 抽帧成功 |

## 输出文件命名规则

- 每视频一个 `<父目录>__<主名>` 子目录，图片放在子目录内，格式为 `frame-{编号}.{扩展名}`
- 编号采用固定宽度零填充，宽度由预计提取帧数决定（如 50 帧时宽度为 2，得到 `frame-01.png` … `frame-50.png`）

## 支持的视频格式

`.mp4`、`.avi`、`.mov`、`.mkv`、`.flv`、`.wmv`、`.webm`、`.m4v`、`.3gp`、`.ts`、`.m2ts`

## 注意事项

- 默认每秒一帧；`--interval-sec` 与 `--interval-frame` 互斥，必须二选一，且必须为正数。
- JPG 质量 95%，PNG 无损。
- **长视频**：超过 10 分钟按默认间隔可能产出数千帧（90 分钟约 5400 帧）。建议先 `--dry-run` 预览，再与用户确认是否增大间隔。
- **进度输出**：脚本定期向 stderr 报告进度（帧数、百分比、速度、ETA）。调用方应配置足够超时，或用 `--quiet` 关闭。
- **首帧或末尾帧丢失**：若视频首帧读取失败（`cap.read()` 在第 0 帧返回 False），message 报"视频首帧读取失败"，`details.read_failure_position = "head"`；末尾帧失败则报"末尾 N 帧读取失败"，`details.read_failure_position = "tail"`，并记录 `frames_short`。
- **fps 不可读兜底**：若 OpenCV 读不到 fps（容器异常或视频损坏），脚本会兜底为 30.0 并在 `details.fps_fallback = true` 标记，message 也提示"FPS 不可读"。
- **路径越界保护**：`--output-dir` 必须在 `--path` 同级、子目录或祖先目录内，否则返回 `output_dir_create_failed`。
