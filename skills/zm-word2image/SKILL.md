---
name: zm-word2image
description: >-
  将本地 Word `.doc` / `.docx` 文件或目录批量转换为 PNG/JPG 图片。两步串联：LibreOffice Word→PDF（存到 /tmp/word/）+ 内嵌 pdf2image 脚本 PDF→逐页图片。仅处理本地路径；不递归子目录；不改写原始 Word 文件。同时提供 LibreOffice UNO API 高级集成说明。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" [args]
  system_tools:
    - name: libreoffice
      call_command: libreoffice --headless --convert-to pdf <word_file>
    - name: poppler-utils
      call_command: pdftoppm -v
---

# zm-word2image

## 核心合同

- 只接受本地路径，不接受 URL。
- 单文件：`.doc` / `.docx` 路径，输出到同目录或 `--output-dir`。
- 目录：扫描当前层所有 `.doc` / `.docx`，批量转换，不递归。
- 两步转换：
  1. LibreOffice Word→PDF，存到 `/tmp/word/<原目录结构>/<文件名>.pdf`
  2. 内嵌 `pdf2image_run.py` 将 PDF 拆为逐页图片
- 输出格式默认 PNG，可选 JPG。
- 每页一张图片，命名 `原文件名/image-N.扩展名`，编号零填充，宽度按总页数动态决定。
- 不改写原始 Word 文件；默认复用同名输出子目录，**同名图片会自动追加 8 位随机后缀，避免覆盖**。
- `scripts/run.py` 只覆盖 Word→PDF→图片流程；LibreOffice `--accept` / UNO 服务模式仅作高级集成知识说明，不是当前脚本参数。

## 可复用资源

- 内部共享：`scripts/_common.py`（`Result` NamedTuple、`safe_filename`、`read_skill_version`、`validate_output_dir`、`FORBIDDEN_OUTPUT_DIRS`）
- `scripts/run.py`：输入预检、Word→PDF 转换、调用 `pdf2image_run.py`、图片落盘与结果输出。
- `scripts/pdf2image_run.py`：内嵌 PDF→图片逻辑，bundled 在本 skill，不依赖外部 skill。

## 运行前提

脚本在 `agent-skills` conda 环境中运行。Linux 额外安装：

```bash
sudo apt install -y libreoffice poppler-utils
```

`pdf2image_run.py` 依赖 `pdf2image` 和 `Pillow`，已随环境预装。

## UNO API 与 AI Agent 集成

详见 [references/uno-api.md](references/uno-api.md)。`--accept` 是 LibreOffice 自身的启动参数，不是当前 `scripts/run.py` 的 CLI 参数；当前 skill 走「CLI 短任务」模式，不维护长连接，也不暴露 UNO 客户端。

## 输入输出约定

| 输入类型 | 示例 | 中间 PDF | 最终图片输出 |
|---|---|---|---|
| 单文件 | `/path/demo.docx` | `/tmp/word/path/demo.pdf` | `/path/demo/image-1.png`, ... |
| 单文件 + 输出目录 | `--output-dir /out/` `/path/demo.docx` | `/tmp/word/path/demo.pdf` | `/out/demo/image-1.png`, ... |
| 目录 | `/path/docs/` | `/tmp/word/path/...` | `/path/docs/demo1/image-1.png`, ... |
| 目录 + 输出目录 | `--output-dir /out/` `/path/docs/` | `/tmp/word/path/...` | `/out/demo1/image-1.png`, ... |

目录映射：`/path/to/` → `/tmp/word/path/to/`。输出格式 `--format png` 或 `--format jpg`，默认 PNG。

## 默认流程

1. 验证 `--path`，判断单文件或目录。
2. 目录输入只扫描当前层 `.doc` / `.docx`，不递归；以 `.` 开头的隐藏文件会被跳过。
3. 对每个 Word 文件：
   a. 计算 PDF 目标路径：`/tmp/word/<相对路径>/<原文件名>.pdf`
   b. `libreoffice --headless --convert-to pdf --outdir <目标目录> <源文件>`
   c. 验证 PDF 生成成功
   d. 子进程调用 `pdf2image_run.py` 按页渲染
4. 逐页写入 `image-N.png` / `image-N.jpg`（N 为零填充编号），汇总成功数、总页数和输出路径。

## 本地直跑

仓库根目录下，把 `/PATH/TO 替换` 为实际路径后可直接运行：

```bash
# 单文件
conda run -n agent-skills python skills/zm-word2image/scripts/run.py --path /path/demo.docx

# 指定格式、输出目录、DPI
conda run -n agent-skills python skills/zm-word2image/scripts/run.py --path /path/demo.docx --format jpg --output-dir /out/ --dpi 300

# 批量转换目录
conda run -n agent-skills python skills/zm-word2image/scripts/run.py --path /path/docs/

# 审阅模式使用最终状态
conda run -n agent-skills python skills/zm-word2image/scripts/run.py --path /path/demo.docx --final-state

# 转换后清理中间 PDF
conda run -n agent-skills python skills/zm-word2image/scripts/run.py --path /path/demo.docx --clean-tmp

# 查看帮助
conda run -n agent-skills python skills/zm-word2image/scripts/run.py --help
```

`$SKILL_DIR` 解析规则：

- 安装态（如 `~/.agent-skills/.zm/zm-word2image/`）：`$SKILL_DIR` 直接是 skill 根目录。
- 仓库根：使用 `skills/zm-word2image/` 相对路径前缀。
- 已激活 conda 环境：可省略 `conda run -n agent-skills`，直接 `python3 ...`。

## 开发态冒烟

无真实 Word 输入也能跑最小化回归：

```bash
python3 skills/zm-word2image/scripts/smoke.py
```

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--path` | 输入 Word 文件或目录（必选） | - |
| `--output-dir` | 输出目录，不指定则写到源文件同目录；不允许写入系统敏感目录（`/etc/`、`/var/`、`/usr/`、`/boot/`、`/proc/`、`/sys/`、`/root/`、`/run/`、`/dev/`、`/sbin/`、`/bin/`、`/lib/`、`/lib64/`） | 源文件同目录 |
| `--format` | 输出格式：`png` 或 `jpg` | png |
| `--dpi` | 图片 DPI（范围 50–2400） | 300 |
| `--final-state` | 以最终状态转换（接受所有修订痕迹） | False |
| `--clean-tmp` | 转换完成后删除本次产生的 `/tmp/word/` 中间 PDF | False |
| `--json` | 以 JSON 格式输出结果 | False |
| `--version` | 输出版本号后退出 | - |
| `--help` | 显示帮助 | - |

## 失败口径

| 状态码 | 含义 |
|---|---|
| `missing_input` | 输入路径不存在 |
| `empty_input_dir` | 目录中没有 Word 文件 |
| `not_a_word` | 输入文件不是 `.doc` / `.docx` |
| `libreoffice_not_found` | 未找到 LibreOffice 或 `--version` 探测失败（见下方说明） |
| `libreoffice_conversion_failed` | LibreOffice 转换失败 |
| `conversion_failed` | pdf2image 转换失败（也包括 `not_a_pdf` / `import_failed` 等子状态） |
| `output_dir_create_failed` | 无法创建输出目录（`run.py` 与 `pdf2image_run.py` 都会触发） |
| `save_failed` | 保存某页图片失败 |
| `import_failed` | `pdf2image_run.py` 子进程中 `pdf2image` 模块未导入（被 `run.py` 包装后透传为 `conversion_failed`） |
| `not_a_pdf` | 独立调用 `pdf2image_run.py` 时输入不是 PDF（被 `run.py` 包装为 `conversion_failed`） |
| `partial` | 批量处理中部分文件成功（仍计入结果，但 `result_status` 为 `partial`） |
| `success` | 全部转换成功 |

**`libreoffice_not_found` 语义说明**：包含「可执行文件不存在」与「`--version` 探测异常」两种场景；两者共用同一状态码，但 `message` 区分（`未找到 LibreOffice` vs `LibreOffice 检查失败（无法获取版本）`）。

**批量结果文本格式**：`summary` 字段形如 `成功转换 N 个 Word，共 M 页；失败 K 个；输出文件: M 个`（按出现顺序拼接；`success` 时只出现第一段；`partial` 时至少两段；`fail` 全失败时只有 `失败 K 个` 段）。

**进程退出码**：`success` → 0；`partial`（部分文件失败） → 2；其他任何错误状态 → 1。

## 输出文件命名规则

每 Word 一个同名子目录，图片命名 `image-{编号}.{扩展名}`，编号零填充，宽度按总页数动态决定：

- < 10 页：宽度 1（`image-1.png`）
- 10–99 页：宽度 2（`image-01.png`）
- 100–999 页：宽度 3（`image-001.png`）
- 依此类推

示例：`report/image-1.png`（5 页），`report/image-01.png`（50 页），`report/image-001.png`（500 页）。

## 中间 PDF 清理

PDF 默认保存在 `/tmp/word/`，图片转换完成后不自动删除。

- 单次清理本次产生的中间 PDF：加 `--clean-tmp`。
- 全量清理：`rm -rf /tmp/word/`。

## 注意事项

- `.doc` 和 `.docx` 均支持。
- 高 DPI 提升质量但增加体积和转换时间。
- 大量文件批量转换时逐个处理以便汇总进度。
- LibreOffice 首次调用可能较慢（1–3 秒）。
- 审阅模式 Word 默认保留修订痕迹；需使用最终状态请加 `--final-state`（通过 LibreOffice `--infilter=...SHOWCHANGES=0` 加载时即视为"接受所有修订"，保存为 PDF 时不再含修订痕迹）。
- 目录输入只扫描当前层，以 `.` 开头的隐藏文件（LibreOffice 锁文件 `.~lock.*` 等）以及软链接指向非 Word 文件均会被跳过。
- 若源 Word 文件本身在 `/tmp/word/` 内（少见），中间 PDF 会落到 `/tmp/word/_chained/` 子目录，避免路径双层嵌套。
- 本 skill 不需要模板/图标/字体等 `assets/`；如后续需要可单独新增 `assets/`。
- 若用户提到"UNO API""Agent 控制 LibreOffice""外部程序连接 LibreOffice"，应解释 `--accept` 能力和 UNO 集成边界，但不要暗示当前脚本已直接支持该模式。
