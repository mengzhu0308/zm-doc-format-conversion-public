# `zm-translate-from-files` 工作流

## 模式总览

- `quick`：直译，适合短文本与快速浏览。
- `normal`：分析 → 翻译，适合文章、博客与一般说明文档。
- `refined`：分析 → 草稿 → 审校 → 修订 → 润色，适合交付或发布的译文。

## 产物约定

输出目录至少包含 `translation.md`。

- `normal` 额外保留 `01-analysis.md`、`02-prompt.md`
- `refined` 额外保留 `03-draft.md`、`04-critique.md`、`05-revision.md`
- 长文分块时保留 `chunks/frontmatter.md`、`chunks/chunk-01.md` 等

## 推荐步骤

1. 读取 `EXTEND.md` 与安全默认值。
   - CLI：`python3 scripts/main.py prefs [--extend /path/to/EXTEND.md] --pretty`
2. 预检输入、规划输出目录与备份目录。
   - CLI：`python3 scripts/main.py resolve --path <file> [--to <lang>] [--output-dir <dir>] --pretty`
3. 判断目标语言、受众、风格与术语约束（来自 `prefs` 输出）。
4. 达到阈值时先切块并沉淀 `02-prompt.md`。
   - CLI：`python3 scripts/main.py chunk --path <file> --output-dir <dir> --max-words <n> --pretty`
5. 输出首轮译文或草稿（agent 行为）。
6. `refined` 模式下对照原文做准确性与自然度审校（agent 行为）。
7. 修订并生成最终的 `translation.md`（agent 行为）。

## 审校重点

- 事实、数字、日期、专有名词不漂移。
- 长句改成目标语言自然句式，不保留源语语序。
- 技术词、产品名、章节标题前后一致。
- Markdown 结构不乱；链接、代码块、表格不丢失。

## 正常升级到精翻

用户先 `normal` 后要求"继续润色"时，直接读取现有的 `01-analysis.md`、`02-prompt.md`、`translation.md` 或 `03-draft.md`，从审校和修订继续，不重新走前处理。
