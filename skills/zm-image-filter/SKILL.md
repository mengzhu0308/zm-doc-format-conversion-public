---
name: zm-image-filter
description: >-
  基于感知哈希（dHash）过滤内容相同或高度相似的图片。当用户需要删除重复图片、去重、过滤相同帧、从视频抽帧结果中去除重复画面，或提到 image deduplication、图片去重、重复图片过滤时，务必使用此 skill。只处理本地目录中的图片文件，不删除原始图片，而是将不重复的图片复制到 `_filter` 目录。支持 PNG/JPG/WebP/BMP/GIF 格式。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python <script> [args]
---

# zm-image-filter

## 核心合同

- 只接受本地目录路径，不接受 URL 或单文件。
- 不删除、不修改原始图片，仅通过复制方式将不重复图片保存到输出目录。
- 默认输出目录为输入目录同级的 `{dirname}_filter`。
- 支持的图片格式：`.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp`、`.gif`。
- 仅扫描输入目录的当前层，不递归子目录。
- 依赖 `Pillow` Python 包（通常已预装）。

## 去重原理

采用**感知哈希（dHash）**判断图片相似度：缩放并灰度化后，逐行比较相邻像素亮度生成 64 位哈希。两张图片哈希的**汉明距离**（不同位数）**<= threshold** 时视为相同，只保留首张。dHash 对轻微光照变化、压缩失真有一定容忍度，适合视频抽帧去重。

## 可复用资源

- `scripts/run.py`
  负责输入预检、图片枚举、dHash 计算、汉明距离比较、文件复制和结构化结果输出。

## 运行前提

依赖 `Pillow`。未安装时：`conda run -n agent-skills pip install Pillow`

> **设计取舍**：模块导入时会保留 `imagehash` 句柄（不立即跑 smoke test），改在首次 `compute_dhash` 调用时按用户实际 `--hash-size` 验证 `imagehash.dhash` 可用性。这样做的代价是：第一次 `compute_dhash` 多承担一次 16x16 灰度图 smoke test（约 30ms）；好处是 smoke test 与真实参数对齐，避免 `--hash-size 12` 时还在用 `hash_size=8` 测。这是从 A-1「模块导入时跑固定 hash_size=8 smoke test」调整到 A-3「按需、按实际 hash_size 验证」的折中。

> **Pillow 缺失的 fallback 行为**：脚本不会立即报错。每张图片在 `compute_dhash` 内部会抛 `ImportError`，被外层捕获后计入 `details.files_failed`；整体 status 仍为 `success`，`failed` 等于图片总数，退出码返回 2。`SKILL.md` 与 `README.md` 均采用同一段说明，避免两侧文档漂移。

## 输入输出约定

| 输入类型 | 示例 | 输出 |
|---|---|---|
| 目录 | `/path/imgs/` | `/path/imgs_filter/`（保留的不重复图片） |
| 目录 + 指定输出 | `--input /path/imgs/ --output-dir /out/` | `/out/`（保留的不重复图片） |

## 默认流程

1. 验证 `--input` 是否存在且为目录。
2. 扫描目录当前层所有受支持的图片文件，按文件名排序。
3. 对每张图片计算 dHash。
4. 按顺序遍历，将当前图片的哈希与已保留的所有哈希比较汉明距离。
5. 若汉明距离 **> threshold**，视为新图片，复制到输出目录并加入保留列表。
6. 若汉明距离 **<= threshold**，视为重复，跳过不复制。
7. 输出保留/跳过数量、文件列表和耗时统计。

## 本地直跑

使用前请先 `export SKILL_DIR=<skill 根目录绝对路径>`，例如：

```bash
# 基本用法
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --input /path/imgs/

# 预览模式（只估算，不复制）
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --input /path/imgs/ --dry-run

# 指定输出目录
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --input /path/imgs/ --output-dir /out/

# JSON 输出供程序调用
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --input /path/imgs/ --json --quiet

# 查看全部参数
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --help
```

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--input` | 输入图片目录（必选） | - |
| `--output-dir` | 输出目录；省略则在输入目录同级创建 `{dirname}_filter` | 自动推导 |
| `--threshold` | 汉明距离阈值，≤ 此值视为重复；不能为负 | 5 |
| `--hash-size` | dHash 尺寸（`hash_size x hash_size` 位）；必须 ≥ 1 且 ≤ 32 | 8 |
| `--dry-run` | 预览：只估算保留/跳过数量，不复制 | False |
| `--overwrite` | 允许覆盖输出目录中已存在的同名文件；默认跳过并计入冲突 | False |
| `--quiet` | 静默：不输出进度到 stderr | False |
| `--json` | JSON 格式输出结果 | False |
| `--help` | 显示帮助 | - |

## 失败口径

| 状态码 | 退出码 | 含义 |
|---|---|---|
| `missing_arg_input` | 2 | 缺少必选参数 `--input`（参数层） |
| `missing_input` | 1 | 输入路径不存在（路径层） |
| `not_a_directory` | 1 | 输入路径不是目录 |
| `empty_input_dir` | 1 | 目录中没有受支持的图片文件 |
| `invalid_output_dir` | 1 | 输出目录与输入目录相同或为其父目录 |
| `output_dir_create_failed` | 1 | 无法创建输出目录 |
| `success`（含 `failed > 0`） | 2 | 过滤完成但有部分图片计算/复制失败 |
| `success`（`failed == 0`） | 0 | 全部图片处理成功 |

> Pillow (PIL) 缺失时不会有专门的 `import_failed` 状态码；此时每张图都会落到 `details.files_failed`，整体 status 仍为 `success`、`failed` 等于图片总数、退出码 2。请见下方"运行前提"段对 fallback 路径的说明。

## 阈值选择建议

| 场景 | 推荐阈值 |
|---|---|
| 视频抽帧去重 | 3~5（默认） |
| 幻灯片/截图去重 | 5~8 |
| 照片去重 | 8~12 |
| 严格去重 | 0~2 |

越小越严格。先用 `--dry-run` 预览再调整。

> **纯色 / 大面积相同背景的图片**：dHash 基于亮度差异生成哈希，纯色图片灰度化后整图同亮度，哈希几乎全 0，多张纯色图片会被误判为同一张。建议对此类图片人工筛选，或先用真实场景图验证阈值。

## 注意事项

- **不删除原始图片**：只复制过滤，原目录不受影响。
- **保留顺序**：按文件名自然排序遍历，保留首张，后续重复跳过；自然排序将 `frame_1 / frame_2 / frame_10` 视为递增顺序。
- **仅扫描当前层**：不递归子目录；多层目录建议分批调用。
- **隐藏文件**：以 `.` 开头的文件名不参与扫描。
- **同名文件默认跳过**：输出目录中已有同名文件时默认计入冲突并跳过；如需覆盖请显式加 `--overwrite`。
- **大目录建议先 `--dry-run`**：预览保留/跳过比例后再正式执行。
- **dry-run 不会创建输出目录**：仅返回估算结果，输出目录在真正复制时才会被创建；脚本 message 末尾会标注「（实际未创建）」。
- **哈希冲突**：默认 64 位 dHash 冲突概率极低；如需更高精度可增大 `--hash-size`，但最大不超过 32。
- **Pillow 版本**：兼容 Pillow 9.1 前后两套 `LANCZOS` 命名空间（>= 9.1 使用 `Image.Resampling.LANCZOS`，< 9.1 使用 `Image.LANCZOS`）。
- **输出目录不可与输入目录相同或为其父目录**：脚本会在执行前显式拒绝（`invalid_output_dir`），避免循环复制或 `SameFileError`。
- **损坏/无法解码的图片不会静默丢失**：会打印 `[warn] 计算哈希失败`，并出现在 `details.files_failed` 与退出码 2 中，便于 CI 区分。
- **符号链接输入目录**：默认输出目录基于解析后的父目录生成，跨设备的符号链接可能让输出落到非预期位置。