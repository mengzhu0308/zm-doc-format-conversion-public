---
name: zm-extract-images
description: >-
  从本地 PDF、Word (.docx)、PowerPoint (.pptx) 文档中提取内嵌图片资源，保存到源文件同目录下的 `文件名_assets/` 目录中。当用户提到"提取图片"、"导出图片"、"导出资源"、"从 PDF/Word/PPT 中提取图片"或需要将文档中的图片单独导出时触发。仅处理本地路径；不改写原始文档；不递归扫描子目录。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python <script> [args]
---

# zm-extract-images

## 核心合同

- 只接受本地路径，不接受 URL。
- 单文件输入：PDF、Word (.docx) 或 PowerPoint (.pptx) 文件路径。
- 目录输入：扫描当前层所有支持的文档类型，批量提取，不递归。
- 提取图片到源文件同目录下的 `原文件名_assets/` 目录中。
- 图片保持原始格式（PNG、JPG、JPEG、GIF、BMP、WebP 等）。
- 当 `_assets/` 中存在图片时，同步生成 `index.md` 索引文件，记录每张图片的来源位置（页码/幻灯片编号）；无图片时省略 `index.md`。
- 不改写原始文档文件。
- 默认会复用已存在的同名 `_assets` 目录；若图片同名，脚本会按当前结果覆盖写入。

## 支持的格式

| 格式 | 说明 |
|------|------|
| PDF (.pdf) | 通过 PyMuPDF (fitz) 提取内嵌图片 |
| Word (.docx) | 通过解压 ZIP 提取 `word/media/` 目录中的图片 |
| PowerPoint (.pptx) | 通过解压 ZIP 提取 `ppt/media/` 目录中的图片 |

**不支持**：`.doc` / `.ppt`（旧版二进制 Office 格式）、`.dotx` / `.dot` / `.potx` / `.ppsx`（其他 OOXML 变体）；如需支持请先转换为 `.docx` / `.pptx`。

## 可复用资源

- `scripts/run.py`
  负责输入预检、文件类型识别、图片提取、图片保存和结构化结果输出。

## 运行前提

Python ≥ 3.10（脚本使用 `str | None` 等 PEP 604 联合类型与 PEP 585 内建泛型；低版本会 SyntaxError）。

脚本默认按 `compatibility.runtime.call_command` 使用 `agent-skills` conda 环境运行。Python 依赖与 `pip install` 命令的完整版本范围与逐项说明见 [README "环境与配置"段](README.md)。

## 输入输出约定

| 输入类型 | 示例 | 图片输出 |
|---|---|---|
| 单文件 | `/path/file.pdf`（或 `.docx`、`.pptx`） | `/path/file_assets/image-{编号}.ext` |
| 目录 | `/path/docs/` | `/path/docs/file1_assets/`、`/path/docs/file2_assets/`、... |

## 提取逻辑

- **PDF**：PyMuPDF 逐页扫描 `page.get_images()`，通过 `doc.extract_image(xref)` 提取并保存到 `_assets/`
- **Word/PPT**：作为 ZIP 解压，读取 `word/media/` 或 `ppt/media/` 中的图片文件

## 本地直跑

```bash
# 单文件提取（将 .pdf 替换为 .docx / .pptx 以处理对应格式）
conda run -n agent-skills python scripts/run.py --path /path/demo.pdf

# 批量提取目录
conda run -n agent-skills python scripts/run.py --path /path/docs/

# 审阅输出详情
conda run -n agent-skills python scripts/run.py --path /path/demo.pdf --verbose
```

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--path` | 输入文档文件或包含文档的目录（必选） | - |
| `--output-dir` | 输出目录，图片会写到 `{output_dir}/{原文件名}_assets/` | 源文件同目录 |
| `--on-conflict` | 当输出目录已有同名文件时的策略；当前仅支持 `overwrite` | `overwrite` |
| `--json` | 以 JSON 格式输出结果（供程序调用） | False |
| `--verbose` | 显示详细处理信息 | False |
| `--help` | 显示帮助 | - |

## 覆盖策略

- 默认行为：`overwrite`，即**已存在的同名图片会被直接覆盖**；脚本不会区分"本次产物"与"用户手动放进 `_assets/` 的非本次产物"，因此用户添加的额外文件（`notes.md`、`thumbnail.png` 等）不会被脚本主动删除，但与本次生成同名的图片会被覆盖写入。
- 当前仅实现 `overwrite`；`skip` / `rename` 为预留接口（通过 `--on-conflict` 暴露），后续按需补齐。
- 批量 + `--output-dir` 模式下的"统一目录"语义：实际仍按文档分子目录（`{output_dir}/{basename}_assets/`）；如需真正打平到同一目录，请在外部脚本处理或后续版本提供 `--flat` 选项。

## 失败口径

| 状态码 | 含义 |
|---|---|
| `missing_input` | 输入路径不存在 |
| `empty_input_dir` | 目录中没有支持的文档文件 |
| `not_supported` | 输入文件不是 PDF/DOCX/PPTX |
| `import_failed` | 所需库未安装（PyMuPDF/python-docx/python-pptx） |
| `extraction_failed` | 提取图片时发生错误（如 ZIP 损坏、PyMuPDF 读 PDF 失败） |
| `success` | 提取成功 |
| `partial_success` | 批量模式下：至少一个文件成功，但有文件失败（结果含 `failed_files`） |

### 批量结果附加字段

- `failed_files`：仅在 `partial_success` 时存在；每条结构为 `{ "file": basename, "path": 绝对路径, "status": 状态码, "message": 错误信息 }`
- `files_processed` / `files_failed` / `total_images_extracted`：批量结果汇总计数

## 索引文件 (index.md)

当 `_assets/` 目录中存在至少一张图片时，脚本会在该目录生成 `index.md`，记录文件名、来源位置和尺寸（PDF）：

- Word 的"来源"标注为"Word 内容"（不精确到页）
- PPT 的"来源"显示为对应幻灯片编号（如"幻灯片 1"）
- Word/PPT 不记录图片尺寸
- 文档中无任何内嵌图片时，**不会**生成 `index.md`，也不会创建空的 `_assets/` 目录

## 输出文件命名规则

- 每个文档生成同名 `_assets/` 目录
- 统一格式 `image-{编号}.{扩展名}`，编号按图片总数动态零填充（<10→1位 / 10–99→2位 / ≥100→3位，以此类推）
- 完整规则与示例见 [README "功能概述" 段](README.md)

## 注意事项

- PDF 通过 xref 去重；Word/PPT 不去重
- 特殊格式（EMF、WMF）可能被 PDF 内嵌，脚本会尝试转换或跳过
- 批量处理时逐个文档提取，便于汇总进度