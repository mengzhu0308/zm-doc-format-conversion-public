# `zm-batch-translate-from-files` 工作流

> 详细步骤见 [`SKILL.md`](../SKILL.md) 的"默认流程"与"批处理（`batch_prepared`）"。本文档补充"模式总览 / 产物约定 / 审校重点 / 分批并行流程"。

## 模式总览

- `quick`：直译，适合短文本与快速浏览。
- `normal`：分析 → 翻译，适合文章、博客与一般说明文档。
- `refined`：分析 → 草稿 → 审校 → 修订 → 润色，适合交付或发布的译文。

## 产物约定

输出目录至少包含 `translation.md`。

- `normal` 额外保留 `01-analysis.md`、`02-prompt.md`
- `refined` 额外保留 `03-draft.md`、`04-critique.md`、`05-revision.md`
- 长文分块时保留 `chunks/frontmatter.md`、`chunks/chunk-01.md` 等
- 已存在的输出目录必须在写入前通过 `python3 scripts/main.py backup --output-dir <path>` 搬移到 `<name>.backup-YYYYMMDD-HHMMSS/`

## 审校重点

- 事实、数字、日期、专有名词不漂移。
- 长句改成目标语言自然句式，不保留源语语序。
- 技术词、产品名、章节标题前后一致。
- Markdown 结构不乱；链接、代码块、表格不丢失。

## 正常升级到精翻

用户先 `normal` 后要求"继续润色"时，直接读取现有的 `01-analysis.md`、`02-prompt.md`、`translation.md` 或 `03-draft.md`，从审校和修订继续，不重新走前处理。

## 分批并行流程

目录输入且文件数超过 `--group-size`（默认 10）时，工作流分为两阶段：

1. **第一阶段（分批）**：`resolve --path` 生成分组 JSON 文件（`batches/batch_*.json`）和自然语言续跑提示。此阶段不执行翻译，仅输出可直接粘贴到 AI 会话中执行的自然语言提示。
2. **第二阶段（并行处理）**：为每个批次开启独立会话，粘贴对应的自然语言续跑提示。各批次互不依赖，可在多个会话中并行执行。所有批次完成后，翻译产物已分布在各自输出目录中，无需额外合并。
