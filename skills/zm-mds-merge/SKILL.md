---
name: zm-mds-merge
description: >-
  将本地多个 Markdown 文件按标题结构合并为统一文档。
  支持 frontmatter 合并、标题层级归一化、相邻标题去重，以及 PDF/图片页面对齐。
  仅处理本地路径；不改写原始源文件；不递归扫描子目录。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: python3 "${SKILL_DIR}/scripts/run.py" [args]
---

# zm-mds-merge

## 核心契约

- 仅接受本地路径。
- 输入模式（三者互斥）：
  - `--paths`：一个或多个 `.md` 文件路径。
  - `--path`：扫描目录当前层所有 `.md` 文件，按文件名排序。
  - `--manifest`：`.json`（含 `paths` 数组）或 `.txt`（每行一个路径）清单文件。
- 输出：单个 `.md` 文件。`--paths` 默认输出到首个源文件同目录的 `merged.md`；`--path` 默认输出 `<目录名>_merged.md` 到父目录；`--manifest` 默认输出 `<清单名>_merged.md` 到清单同目录。`--output` / `--output-dir` 可覆盖默认路径。
- 不改写原始源文件；默认覆盖同名输出文件。

## 可复用资源

- `scripts/run.py`：输入预检、Markdown 解析、frontmatter 合并、标题归一化、去重、格式化与输出。

## 输入输出约定

| 输入类型 | 示例 | 输出 |
|---|---|---|
| 多文件 | `--paths a.md b.md c.md` | 首个源文件同目录的 `merged.md` |
| 目录 | `--path /path/docs/` | `/path/docs_merged.md` |
| 清单 | `--manifest /path/list.json` | 清单同目录的 `list_merged.md` |

`--output` 可覆盖为任意完整输出路径；`--output-dir` 可覆盖输出目录（文件名为 `merged.md`）。

## 默认流程

1. 解析与预检：区分 `--paths` / `--path` / `--manifest` 三种模式，验证路径存在且为 `.md`，排除符号链接风险。
2. 读取与合并 frontmatter：收集所有文件的 frontmatter 键值，冲突时以首文件为准；全无 frontmatter 则输出也不带。
3. 标题层级归一化：若文件正文以一级标题开头则保留为文档标题，否则插入文件名标题；将文件内最小标题层级统一提升为二级（`##`）。
4. 对齐标记插入（若启用 `--align-source`）：按源文件名提取页码，根据 `--align-mode` 在正文前插入页码标记和/或图片引用（`marker` / `image-ref` / `both`）。
   - 页码提取：优先从源 Markdown 文件名（不含扩展名）抽取首个连续数字串作为页码；如 `image-3.md → 3`、`page-01.md → 1`、`ch2.md → 2`。若文件名不含可识别的数字，则回退到该文件在输入列表中的 1-based 位置作为页码。若 `--align-source` 指向图片目录且 `--align-mode` 选 `image-ref`/`both`，还会在 marker 后追加对应图片的相对 Markdown 引用（找不到图片时降级为仅 marker，不中断合并）。
5. 组装输出：去重相邻文件的边界重复标题，统一换行符与空行，文件间插入 `---` 分隔符（或 `--separator` 指定），写入输出文件。
6. 返回结构化结果：含源文件列表、输出路径、frontmatter 合并情况、标题统计等。

## 解析边界与已知忽略

- 围栏代码块（` ``` `/`~~~`）：所有标题解析、归一化、去重与 frontmatter 提取都先按行扫描并跳过围栏内行，避免被代码注释或示例标题污染。
- Markdown 引用块（`> `）：行首为 `> ` 的内容不会被识别为标题（`find_first_heading_line` 与 `parse_headings` 的 `^(#{1,6})\s+` 正则要求行首即 `#`）；但其内容文本仍参与相邻标题去重的文本比较。
- HTML 注释与 HTML 标题：`<h1>`–`<h6>` 标签同样被识别为标题（与 Markdown `#`–`######` 等价）；围栏代码块内的 `<h*>` 标签不会被误识别。
- YAML frontmatter：仅识别行首 `---\n` 起始与 `\n---\n` 结束的合法 frontmatter 块；块内支持空格或 tab 缩进；块标量（`|` / `>`）及其中空行被原样保留。空 frontmatter 块（`---\n---\n`）被视为无 frontmatter。
- 空源文件：纯空白或零字节源文件按"无 frontmatter / 无标题"处理，正文以空字符串参与合并；可通过 `--continue-on-error skip` 之外的逻辑跳过（详见失败口径表 `read_failed`）。
- 缩进围栏代码块：列表项等场景下的缩进围栏（行首有空格后再接 `` ``` `` 或 `~~~`）当前不被识别为围栏；其中的 `#` 注释可能被误统计为标题。建议避免在待合并文件中使用缩进围栏，或确保缩进围栏内不含 `#` 开头的行。
- 软链策略：`--paths` 严格拒绝符号链接；`--manifest` / `--path` / `--align-source` 沿用 `Path.resolve()` 解析，行为差异见 `scripts/run.py`。
- 相邻标题去重边界：去重仅针对 Markdown `#` 标题；若相邻文件边界处前一文件以 HTML `<h*>` 标题结尾、下一文件以 Markdown `#` 标题开头（文本相同），暂不去重。

## 本地直跑

```bash
# 合并多个文件
python3 "${SKILL_DIR}/scripts/run.py" --paths ch1.md ch2.md ch3.md

# 目录 + 指定输出
python3 "${SKILL_DIR}/scripts/run.py" --path docs/ --output result.md

# 清单 + 自定义分隔符
python3 "${SKILL_DIR}/scripts/run.py" --manifest files.txt --separator "\n\n***\n\n"

# 对齐 PDF 提取的图片
python3 "${SKILL_DIR}/scripts/run.py" --path ocr_results/ --align-source pdf_images/ --align-mode both

# JSON 格式输出（CI 集成）
python3 "${SKILL_DIR}/scripts/run.py" --paths ch1.md ch2.md --json

# 详细处理信息（调试页码 / 对齐 / frontmatter）
python3 "${SKILL_DIR}/scripts/run.py" --paths ch1.md ch2.md --verbose

# 跳过失败文件继续合并（批量 OCR 结果场景）
python3 "${SKILL_DIR}/scripts/run.py" --path ocr_results/ --continue-on-error skip
```

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--paths` | 一个或多个 `.md` 文件路径 | - |
| `--path` | 输入目录，扫描当前层 `.md` | - |
| `--manifest` | 清单文件（`.json` 含 `paths` 数组，或 `.txt` 每行一个路径） | - |
| `--output` | 输出文件完整路径 | 按输入类型推导 |
| `--output-dir` | 输出目录 | 按输入类型推导 |
| `--separator` | 文件间分隔符 | `\n\n---\n\n` |
| `--no-frontmatter` | 禁止输出合并后的 frontmatter | False |
| `--frontmatter-strategy` | `first-wins`（首文件为准,空值仍会胜出）/ `first-non-empty`（首文件该 key 为空时由后续非空值覆盖;**注意**首文件若有非空占位,即使后续文件给出更准确值,也会被该非空值压制） | `first-wins` |
| `--align-source` | 对齐源（PDF 或图片目录） | 无 |
| `--align-mode` | `marker`（页码）/ `image-ref`（图片，仅图片目录时生效）/ `both`（PDF 对齐源时不支持） | `both` |
| `--continue-on-error` | 源文件读取失败时:`off` 立即返回 `read_failed`(默认);`skip` 跳过失败文件继续合并,成功/失败清单写入 `details.succeeded` / `details.failed` | `off` |
| `--json` | JSON 格式输出结果 | False |
| `--verbose` / `-v` | 详细处理信息 | False |
| `--version` | 输出版本号（从 `VERSION.yaml` 读取后退出） | - |
| `--help` | 显示帮助 | - |

## 失败口径

| 状态码 | 含义 | 退出码 |
|---|---|---|
| `missing_input` | 输入路径不存在 | `1` |
| `empty_input_dir` | 目录无 `.md` 文件 | `1` |
| `unsupported_format` | 输入非 `.md` / `.markdown` | `1` |
| `manifest_parse_failed` | 清单文件解析失败 | `1` |
| `manifest_empty` | 清单无有效 `.md` 路径 | `1` |
| `output_dir_create_failed` | 无法创建输出目录 | `1` |
| `save_failed` | 保存输出文件失败 | `1` |
| `read_failed` | 读取源文件失败 | `1`（`--continue-on-error skip` 时降级为 `2`） |
| `align_source_not_found` | 对齐源路径不存在 | `1` |
| `align_source_invalid_type` | 对齐源类型与 `--align-mode` 不兼容（如 PDF 配 `image-ref` / `both`） | `1` |
| `manifest_not_found` | 清单文件（`--manifest`）不存在 | `1` |
| `symlink_not_supported` | 输入路径（`--paths`）是符号链接 | `1` |
| `partial` | 部分文件失败但合并成功（仅在 `--continue-on-error skip` 下出现） | `2` |
| `success` | 合并成功 | `0` |

退出码约定：成功 `0`，任意失败 `1`，部分失败但合并成功为 `2`（`partial`）。
