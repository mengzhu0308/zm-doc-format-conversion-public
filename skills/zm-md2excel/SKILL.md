---
name: zm-md2excel
description: >-
  从本地 Markdown 文件中提取表格并保存为 xlsx 或 csv。当用户提到"md转excel"、"markdown表格转xlsx"、"提取md中的表格"、"把markdown表格导出成csv"、"批量转换md表格"或需要将 Markdown 文件中的表格提取为电子表格格式时触发。仅处理本地路径；不递归扫描子目录；不接受 URL；不改写原始文档。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" [args]
    - name: pure-python
      call_command: python3 "$SKILL_DIR/scripts/run.py" [args]
---

# zm-md2excel

## 核心合同

- 只接受本地路径，不接受 URL。
- 单文件输入：`.md` 或 `.markdown` 文件路径。
- 目录输入：扫描当前层所有 `.md` 与 `.markdown` 文件，批量转换，不递归。
- 单文件输出：与源文件同目录、同名，仅扩展名变为 `.xlsx` 或 `.csv`。
- 目录批量输出：在输入目录的父目录新建 `<目录名>_md2excel` 目录，结果存放于此。
- 显式 `--output-dir` 时，输出文件名前缀加源目录名（`{源目录名}__{原文件名}.{ext}`）以避免跨目录同名文件冲突。
- 多表格文件：xlsx 输出时每表一个 sheet；csv 输出时每表一个独立文件（`_table1.csv`、`_table2.csv`）。
- 不改写原始 Markdown 文件。
- 默认覆盖已存在的同名输出文件。
- **数字以文本格式保存**：xlsx 输出时所有单元格设 `number_format='@'`，防止大数字（如身份证号）因 Excel 精度限制而丢失；csv 输出时纯数字前自动添加制表符前缀，使 Excel 打开 CSV 时仍识别为文本。
- 围栏代码块（` ```...``` `）内的伪表格会被自动剥离，不会被识别为真实表格。

## 可复用资源

- `scripts/run.py`：输入预检、表格提取、格式转换与结果输出。

## 运行前提

默认通过 `conda run -n agent-skills` 运行。若无该环境，至少安装：

```bash
pip install "pandas>=2.0" "openpyxl>=3.1"
```

## 输入输出约定

| 输入类型 | 示例 | 输出 |
|---|---|---|
| 单文件 | `/path/report.md` | `/path/report.xlsx` |
| 单文件 (csv) | `/path/report.md` | `/path/report.csv` |
| 多表格单文件 | `/path/data.md` | `/path/data_table1.csv`、`/path/data_table2.csv` |
| 目录 | `/path/docs/` | `/path/docs_md2excel/file1.xlsx`、`/path/docs_md2excel/file2.xlsx`、... |

## 提取逻辑

读取 Markdown 全文，通过正则提取标准 `|` 表格块，逐表解析后按 `--format` 保存为 xlsx（pandas + openpyxl）或 csv（标准库）。

## 本地直跑

`$SKILL_DIR` 指向当前 skill 根目录；安装态通常在 `~/.agent-skills/.zm/zm-md2excel/`。

```bash
# 单文件转 xlsx
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/report.md

# 批量转换目录
conda run -n agent-skills python "$SKILL_DIR/scripts/run.py" --path /path/docs/
```

其他参数（`--format csv`、`--output-dir`、`--verbose` 等）参见下方参数表。

## 脚本参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--path` | 输入 Markdown 文件或包含 `.md` 的目录（必选） | - |
| `--format` | 输出格式：`xlsx` 或 `csv` | `xlsx` |
| `--output-dir` | 输出目录，单文件默认与源文件同目录，目录输入默认在父目录新建 `<目录名>_md2excel` | - |
| `--json` | 以 JSON 格式输出结果（供程序调用） | False |
| `--verbose` / `-v` | 显示详细处理信息 | False |
| `--help` | 显示帮助 | - |

## 失败口径

| 状态码 | 含义 |
|---|---|
| `missing_input` | 输入路径不存在 |
| `empty_input_dir` | 目录中没有 `.md` 或 `.markdown` 文件 |
| `not_supported` | 输入文件不是 Markdown |
| `read_failed` | 读取输入文件失败（权限或 I/O 错误） |
| `no_tables` | Markdown 中未找到表格 |
| `dependency_missing` | 缺少必需依赖（如 `pandas`/`openpyxl`），结构化返回 `required` 字段 |
| `save_failed` | 保存输出文件失败 |
| `success` | 转换成功 |

## 注意事项

- 仅支持标准 Markdown 表格语法（`|` 分隔），不支持 HTML `<table>`。
- 单元格内若包含 `|` 字符可能导致解析异常。
- 表格对齐标记（`:---:`、`:---`、`---:`）会被忽略。
- 围栏代码块（` ```...``` `）**成对闭合时**，块内伪表格会被自动忽略，不会污染输出；**未闭合围栏**（如 AI 截断、复制粘贴丢失末尾的 ``` 行）会被视为代码块延续到文件末尾并整体剥离，避免静默污染。剥离前会归一化行尾（`\r\n` / `\r` → `\n`），CRLF 输入也能正确处理。
- 表格行允许前导空白（4 空格缩进、列表项嵌套等场景也能识别）。
- 非 UTF-8 编码的源文件会以 `errors='replace'` 方式读取，不会抛 `UnicodeDecodeError`。
- 单文件建议 <50 MB；超大文件会一次性读入内存，可能 OOM。脚本未做流式解析，超大 md 不在本 skill 设计目标内。
- csv 模式以 Excel 兼容为目标：纯数字单元会被 csv 模块的 `QUOTE_MINIMAL` 自动用双引号包裹，Excel 打开仍按文本识别；`csv.reader` / `pandas.read_csv` 会自动 unquote 得到原始字符串，无 `\t` 脏数据。
- `--verbose` / `-v` 启用后会把详细日志写入 stderr，不影响 `--json` 协议输出。
