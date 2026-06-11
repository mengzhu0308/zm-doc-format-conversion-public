---
name: zm-ppt2image
description: >-
  将本地 PPT/PPTX 逐页转为 PNG/JPG（LibreOffice 转为中间 PDF 后用 pdf2image 渲染），适用于拆页后交给 OCR、人工校对或后续图像流程。仅处理本地路径、不递归、不改写原始文件；可补充 LibreOffice UNO API 集成说明。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python <script> [args]
  system_tools:
    - name: libreoffice
      call_command: libreoffice --headless --convert-to pdf <file>
    - name: poppler-utils
      call_command: pdftoppm -v
  dependencies:
    - name: libreoffice
      kind: system
      version: ">=7.0"
      purpose: PPT/PPTX → PDF
    - name: poppler-utils
      kind: system
      version: ">=0.86"
      purpose: pdf2image 后端依赖（Linux 必装；macOS brew install poppler；Windows 由 pdf2image 自带）
    - name: pdf2image
      kind: python
      version: ">=1.16"
      purpose: PDF → 图片渲染
    - name: Pillow
      kind: python
      version: ">=9.0"
      purpose: pdf2image 输出图片保存
---

# zm-ppt2image

## AI 行为契约

本 skill 为 hybrid 模式，AI 必须明确区分"直接回答"和"调用脚本"两种行为，不得隐式猜测。

### AI 直接回答（不调用脚本）

1. 解释 UNO API / `--accept` / Agent 集成的可行性与边界
2. 解释失败状态码（如 `libreoffice_failed`、`import_failed`、`partial`）的成因
3. 给出跨平台安装命令（Linux / macOS / Windows）
4. 解释中间 PDF 在 `tempfile.gettempdir() / ppt/` 下的存放规则与追溯方法
5. 说明页数 → 编号宽度的命名规则

### AI 必须调用脚本（`scripts/run.py`）

1. 用户明确要求"将 PPT 转为图片"（单文件或批量目录）
2. 用户指定 `--output-dir` / `--format` / `--dpi` 等参数
3. 用户要求"以 JSON 格式输出"

### AI 禁止行为

- 不得自行修改 `scripts/run.py` 逻辑
- 不得绕过 `compatibility.runtime.call_command` 直接拼装安装路径
- 不得在用户未要求转换时擅自调用脚本

## 命令路径约定

- frontmatter `compatibility.runtime.call_command` 中的 `<script>` 占位符在**安装态**展开为 `<skill_root>/scripts/run.py`，其中 `<skill_root>` 是当前已加载 skill 的安装根目录（与 `project-install/` 实际部署路径一致，例如 `~/.agent-skills/.zm/zm-ppt2image/`）
- **源码态**（开发本 skill 源码时）从仓库根目录直接运行 `skills/zm-ppt2image/scripts/run.py`
- 调用时统一使用 `conda run -n agent-skills python <完整脚本路径> [args]`

## 核心合同

- 只接受本地路径，不接受 URL。
- 单文件输入：`.ppt` 或 `.pptx` 文件路径，输出到同目录或指定输出目录。
- 目录输入：扫描当前层所有 `.ppt/.pptx`，批量转换，不递归。
- 输出格式默认 PNG，可选 JPG（`--format` 参数）。
- 每页 PPT 转为一张独立图片，放在同名子目录下，命名格式 `image-{N}.{ext}`，编号零填充宽度由总页数决定（<10 页为 1 位，<100 页为 2 位，以此类推）。
- 不改写原始 PPT 文件；已存在的同名输出子目录会复用，同名图片会覆盖。
- `scripts/run.py` 只覆盖「PPT/PPTX → PDF → 图片」流程；UNO 服务模式仅作为知识说明，不是脚本参数。

## 转换流程

1. LibreOffice 将 PPT/PPTX 转为 PDF，中间 PDF 存于 `tempfile.gettempdir() / ppt / <原 PPT 完整相对路径>`（Linux/macOS 通常是 `/tmp/ppt/<原路径>/<原文件名>.pdf`，Windows 是 `%TEMP%\ppt\<原路径>\<原文件名>.pdf`）
2. pdf2image 按页渲染 PDF 为图片
3. 图片按命名规则输出到目标目录

依赖 `LibreOffice`、`pdf2image` Python 包和 `poppler` 系统库。PDF→图片逻辑内嵌于 `scripts/run.py`，无跨 skill 依赖。

中间 PDF 默认保留供追溯；可通过 `--keep-pdf false` 在渲染完成后立即删除，或通过 `--clean-tmp` 清理 7 天前的残留。

## 可复用资源

- `scripts/run.py`：负责输入预检、批量枚举、LibreOffice PPT→PDF、pdf2image PDF→图片和结构化结果输出。

## 运行前提

脚本在 `agent-skills` conda 环境中运行，需安装以下依赖：

- **Linux**：`sudo apt install -y libreoffice poppler-utils`
- **macOS**：从 [LibreOffice 官网](https://www.libreoffice.org/download/download/) 下载安装（命令名可能是 `libreoffice` 或 `soffice`）；`brew install poppler`
- **Windows**：从 LibreOffice 官网下载安装并加入 PATH；`pdf2image` 已包含 Windows 依赖，无需额外安装 poppler

`scripts/run.py` 会通过 `shutil.which` 自动检测 `libreoffice` 或 `soffice` 命令，并通过 `tempfile.gettempdir()` 自动选择平台对应的中间 PDF 目录（Linux/macOS 为 `/tmp/ppt/`，Windows 为 `%TEMP%\ppt\`）。

## UNO API 与 AI Agent 集成

LibreOffice 可通过 `--accept` 进入长期驻留的 UNO API 服务器模式（例如 `--accept="socket,host=127.0.0.1,port=2002;urp;"`）。外部程序或 AI Agent 用 UNO 协议连接后，可完成打开、创建、保存演示文稿，写入文本、插入图片，调用宏或脚本，导出支持格式，以及读取内容交给 LLM 的工作流。

这适合 Agent 自动生成或修改幻灯片、回写讲稿、插入图示，再统一导出 PDF/PPTX 等高级集成场景。当前 skill 不包含 UNO 客户端脚本，也不会把 `--accept` 透传给 `scripts/run.py`；UNO 自动化应将 LibreOffice 作为独立外部服务启动和管理。

详细协议说明、启动参数模板、Python `uno` 库连接示例与边界规范，见 [references/uno-integration.md](references/uno-integration.md)。

## 输入输出约定

| 输入类型 | 示例 | 输出 |
|---|---|---|
| 单文件 | `/path/demo.pptx` | `/path/demo/image-1.png`，`/path/demo/image-2.png`，... |
| 单文件 + 输出目录 | `--output-dir /out/` `/path/demo.pptx` | `/out/demo/image-1.png`，... |
| 目录 | `/path/ppts/` | `/path/ppts/demo1/image-1.png`，... |
| 目录 + 输出目录 | `--output-dir /out/` `/path/ppts/` | `/out/demo1/image-1.png`，... |

## 本地直跑

以下命令基于 `compatibility.runtime.call_command` 展开，`<skill_root>` 解析为安装根目录（如 `~/.agent-skills/.zm/zm-ppt2image/`）：

**命令模板**：`conda run -n agent-skills python "<skill_root>/scripts/run.py" [参数]`

| 场景 | 参数 |
|---|---|
| 单文件转 PNG（默认） | `--path <file.pptx>` |
| 单文件转 JPG | `--path <file.pptx> --format jpg` |
| 指定输出目录 | `--path <file.pptx> --output-dir <dir>` |
| 高清 DPI | `--path <file.pptx> --dpi 600` |
| 转换后立即删除中间 PDF | `--path <file.pptx> --no-keep-pdf` |
| 批量并发（`workers >= 2` 时启用 `ThreadPoolExecutor`） | `--path <dir/> --workers 4` |
| 以 JSON 格式输出结果 | `--path <file.pptx> --json` |
| 清理中间 PDF（默认 7 天） | `--clean-tmp` |
| 自定义清理阈值 | `--clean-tmp --clean-tmp-days 30` |
| 清理 LibreOffice 临时 profile 目录 | `--clean-profiles` |
| 查看帮助 | `--help` |

完整示例（保留 4 个常用）：

```bash
# 单文件转 PNG（默认）
conda run -n agent-skills python "<skill_root>/scripts/run.py" --path /path/demo.pptx

# 批量并发
conda run -n agent-skills python "<skill_root>/scripts/run.py" --path /path/ppts/ --workers 4

# 清理 30 天前的中间 PDF
conda run -n agent-skills python "<skill_root>/scripts/run.py" --clean-tmp --clean-tmp-days 30

# 查看帮助
conda run -n agent-skills python "<skill_root>/scripts/run.py" --help
```

### 源码态开发

开发本 skill 源码时，从仓库根目录直接运行：

```bash
conda run -n agent-skills python skills/zm-ppt2image/scripts/run.py --path /path/demo.pptx
```

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--path` | 输入 PPT/PPTX 文件或包含 PPT/PPTX 的目录（与 `--clean-tmp` / `--clean-profiles` 互斥） | - |
| `--output-dir` | 输出目录，不指定则写到源文件同目录 | 源文件同目录 |
| `--format` | 输出格式：`png` 或 `jpg` | png |
| `--dpi` | 图片 DPI，越高质量越好 | 300 |
| `--keep-pdf` / `--no-keep-pdf` | 转换成功后是否保留中间 PDF；`--no-keep-pdf` 时删除 PDF 后逐级清理空父目录（BooleanOptionalAction 配对） | `--keep-pdf` |
| `--clean-tmp` | 清理 `temp/ppt/` 下超过 `--clean-tmp-days` 天的中间 PDF（与 `--path` / `--clean-profiles` 互斥） | False |
| `--clean-profiles` | 清理 `temp/lo_profile_*/` 下超过 `--clean-tmp-days` 天的 LibreOffice 临时 profile 目录（与 `--path` / `--clean-tmp` 互斥） | False |
| `--clean-tmp-days` | 配合 `--clean-tmp` / `--clean-profiles` 使用；合法范围以 [scripts/run.py](scripts/run.py) 顶部 `CLEAN_TMP_DAYS_MIN` / `CLEAN_TMP_DAYS_MAX` 常量为准（避免负数/超大值误清所有中间 PDF） | `CLEAN_TMP_DAYS_DEFAULT` |
| `--workers` | 批量并发数（默认 1 串行；>=2 时启用 `ThreadPoolExecutor` 并发，PIL 并发安全由 `_pil_lock` 保护，LibreOffice profile 已隔离） | 1 |
| `--json` | 以 JSON 格式输出结果（供程序调用） | False |
| `--help` | 显示帮助 | - |

## 失败口径

| 状态码 | 含义 | 退出码 |
|---|---|---|
| `success` | 全部 PPT 转换成功 | 0 |
| `partial` | 部分 PPT 转换成功、部分失败（可读取 JSON `targets` 区分） | 2 |
| `missing_input` | 输入路径不存在 | 1 |
| `empty_input_dir` | 目录中没有 PPT/PPTX 文件 | 0 |
| `not_a_ppt` | 输入文件不是 `.ppt/.pptx` 后缀 | 0 |
| `import_failed` | pdf2image 或 Pillow 未安装；`details.missing_package` 指明具体包 | 11 |
| `libreoffice_failed` | LibreOffice 转换 PPT→PDF 失败；`details.error_type` 可为 `not_found` / `timeout` / `mkdir_failed` / `fonts_missing` / `convert_failed` / `no_pdf_output` / `ambiguous_pdf` | 12 |
| `conversion_failed` | pdf2image 渲染 PDF→图片失败 | 13 |
| `output_dir_create_failed` | 无法创建输出目录 | 1 |
| `save_failed` | 保存某页图片失败 | 14 |

## 注意事项

- 若用户提到「UNO API」「Agent 控制 LibreOffice」「外部程序连接 LibreOffice」，应解释 `--accept` 能力和 UNO 集成边界，但不要暗示当前脚本已直接支持该模式。