---
name: zm-translate-from-files
description: >-
  将单个本地 `.md` / `.txt` 文件翻译为目标语言。支持 quick/normal/refined 三档模式、EXTEND.md 偏好、术语表约束与长文 Markdown 分块。拒绝目录、清单文件和 URL 输入，不改写源文件。EXTEND.md 为可选配置，默认按 cwd → XDG → 用户目录顺序查找，未配置时用安全默认值。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: python3
      call_command: python3 "$SKILL_DIR/scripts/main.py" <subcommand> [args]
  config_files:
    - path: ~/.zm-skills/zm-translate-from-files/EXTEND.md
      description: 可选翻译偏好配置
---

# zm-translate-from-files

## 核心合同

- 只接受本地路径，不接受 URL 或网页抓取。
- 只接受单个 `.md` 或 `.txt` 文件；目录和清单文件输入交给 `zm-batch-translate-from-files`。
  - 「清单文件」指包含多个文件路径的 YAML / JSON / 纯文本清单文件，单个文件路径用逗号拼接的字符串也按清单处理并拒绝。
- 默认把 `/path/demo.md` 的翻译结果写到 `/path/demo-zh_CN/translation.md`；若用户显式指定独立输出目录，则写到该目录下的同名翻译子目录。
- 只生成翻译产物与中间文件，不改写原始源文件。
- 已存在的输出目录不会直接覆盖；先备份为 `{name}.backup-YYYYMMDD-HHMMSS`，再开始本轮翻译。
- 脚本只负责确定性预检、偏好解析和分块；真正的分析、翻译、审校与润色由 agent 按本 skill 工作流执行。

## 可复用资源

- `scripts/translation_preferences.py`：查找并解析 `EXTEND.md`，输出目标语言、模式、受众、风格、分块阈值与术语表。
- `scripts/resolve_targets.py`：单文件输入预检、输出目录与备份路径规划。
- `scripts/chunk_markdown.py`：提取 frontmatter、按 Markdown 块边界切分长文并落盘 `chunks/`；可作为 pure 转换脚本独立调用（要求显式 `--output-dir` 与 `--max-words`）。
- `scripts/main.py`：统一 CLI 入口，收口 `prefs`、`resolve`、`chunk` 三类确定性动作；**`main.py` 是推荐入口**，会自动读 EXTEND.md 偏好、`<cwd>/<stem>-chunks/` 回退与 warnings 透传。

## 偏好文件

> **默认值真相来源**：[`scripts/translation_preferences.py`](scripts/translation_preferences.py) 中的 `DEFAULT_*` 常量是默认值的唯一真相来源；本节下表与 [`references/extend-schema.md`](references/extend-schema.md) 表格均为该脚本常量的快照，便于人工阅读而不参与运行时计算。如发现三者不一致，以脚本为准。

`EXTEND.md` 为**可选配置**，通过 prompt 显式路径或 `--extend` 传入；未提供时按以下顺序自动查找，第一个命中的 `EXTEND.md` 即生效：

1. `cwd` 下的 `./.zm-skills/zm-translate-from-files/EXTEND.md`（项目级覆盖，cwd 可通过 `--cwd` 显式指定）
2. XDG 目录下的 `$XDG_CONFIG_HOME/zm-skills/zm-translate-from-files/EXTEND.md`（未设置 `XDG_CONFIG_HOME` 时回退到 `~/.config`）
3. 用户目录下的 `~/.zm-skills/zm-translate-from-files/EXTEND.md`

未命中任何位置时不报错，直接使用安全默认值。

支持的键（默认值，脚本为真相来源）：

- `target_language`（`zh-CN`）
- `default_mode`（`normal`）
- `audience`（`general`）
- `style`（`natural`）
- `chunk_threshold`（`4000`）
- `chunk_max_words`（`5000`）
- `default_output_dir`（空）
- `glossary_files`（空）

完整键结构与示例见 `references/extend-schema.md`。

## 默认流程

1. 读取 `EXTEND.md` 与安全默认值（`scripts/main.py prefs`）。
2. 预检单个文件并规划输出目录（`scripts/main.py resolve --path <文件> [--to <语言>] [--output-dir <目录>]`）；非 `status=ready` 时停在该步。
3. 按模式翻译：
   - `quick`：直译
   - `normal`：分析 → 翻译
   - `refined`：分析 → 草稿 → 审校 → 修订 → 润色
4. 词数达到 `chunk_threshold` 时先切块（`scripts/main.py chunk --output-dir <dir> --max-words <n>`）。
   - 未指定 `--output-dir` 时回退到 `<cwd>/<stem>-chunks/` 并向 stderr 写 warning，避免污染源文件同目录。
5. 输出产物 + 最终报告（成功/失败/跳过数、目标语言、模式、受众、风格、是否分块、是否用术语表、是否备份既有输出、源文件未被改写）。
   - `translation.md`
   - `01-analysis.md` / `02-prompt.md`（`normal` / `refined`）
   - `03-draft.md` / `04-critique.md` / `05-revision.md`（`refined`）
   - `chunks/`（长文分块时）

### 目标语言

`--to` / `EXTEND.md` 中的 `target_language` 接受任意 BCP 47 语言标签，例如 `zh-CN`、`zh-Hans-CN`、`en`、`en-US-oxendict`、`fr`。脚本会自动按 BCP 47 规范化主标签小写、4 字母脚本标签首字母大写（Hans/Hant/Latn）、2 字母地区标签大写（CN/US/TW），并把分隔符 `-` 替换为 `_` 作为输出目录后缀，避免 `zh-Hans-CN` 与 `zh-Hant-TW` 落入同一目录。空值、纯空白或未知形态直接报错 `invalid_target_language`。

详细步骤见 `references/workflow.md`。

## 翻译原则

- 优先自然表达，不逐句硬译。
- 事实、数据、日期、专有名词与原文一致。
- 保留 Markdown 结构、链接、代码块、表格与 frontmatter 语义。
- 术语前后一致；生僻术语首次出现可补简短说明。
- `normal` 完成后若用户要求”继续润色”，直接沿用已有草稿进入审校/润色，不重做预检。

## 长文分块

- `quick` 不强制分块；文本过长时提醒术语漂移风险。
- `normal` / `refined` 达到阈值后先切块，基于统一分析和术语表做分块草稿。
- `02-prompt.md` 沉淀共享上下文；各块翻译共用，不每块重新发明术语。
- 合并 `chunks/` 后再全篇审校，不做局部检查即宣称完成。

## 本地辅助命令

```bash
# 读取偏好
python3 scripts/main.py prefs [--extend /path/to/EXTEND.md] --pretty

# 预检输入与输出目录
python3 scripts/main.py resolve --path /absolute/path/to/demo.md --to zh-CN [--output-dir /out] [--extend /path/to/EXTEND.md] --pretty

# 切分长文
python3 scripts/main.py chunk --path /absolute/path/to/demo.md --output-dir /absolute/path/to/demo-zh_CN --max-words 4500 --pretty
```

## 失败口径

完整错误码（`missing_input` / `directory_input_not_supported` / `unsupported_input` / `unsafe_symlink_input` / `unsafe_output_path` / `unsafe_output_ancestor` / `invalid_target_language` / `unsupported_encoding` / `permission_denied` / `invalid_max_words` / `chunk_failed`）的定义与处理建议见 [`references/failure-codes.md`](references/failure-codes.md)。

## 参考

- 需要确认翻译模式与中间产物时，读取 `references/workflow.md`。
- 需要确认 `EXTEND.md` 键结构时，读取 `references/extend-schema.md`。
- 需要直接复用英中术语时，读取 `references/glossary-en-zh.md`。
