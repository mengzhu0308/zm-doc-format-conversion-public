---
name: zm-batch-translate-from-files
description: >-
  批量翻译本地 `.md` / `.txt` 文件。接受当前层目录或 JSON/TXT 清单两种互斥输入，不接受单个待翻译文件、URL 或递归扫描。支持 quick/normal/refined 三档模式、EXTEND.md 偏好、术语表约束与长文 Markdown 分块。EXTEND.md 为可选配置；候选路径依次为 `./.zm-skills/zm-batch-translate-from-files/EXTEND.md`（项目内）、`$XDG_CONFIG_HOME/zm-skills/zm-batch-translate-from-files/EXTEND.md`（XDG）、`~/.zm-skills/zm-batch-translate-from-files/EXTEND.md`（用户目录）；命中首个存在的文件，未配置时用安全默认值。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: python3
      call_command: python3 $SKILL_DIR/scripts/main.py <subcommand> [args]
  config_files:
    - path: ./.zm-skills/zm-batch-translate-from-files/EXTEND.md
      description: 可选批量翻译偏好配置（项目内候选）
    - path: $XDG_CONFIG_HOME/zm-skills/zm-batch-translate-from-files/EXTEND.md
      description: 可选批量翻译偏好配置（XDG 候选）
    - path: ~/.zm-skills/zm-batch-translate-from-files/EXTEND.md
      description: 可选批量翻译偏好配置（用户目录候选）
---

# zm-batch-translate-from-files

## 核心合同

- 只接受本地路径，不接受 URL 或网页抓取。
- 两种输入互斥：
  - `resolve --path <directory>`：扫描目录当前层 `.md` / `.txt` 文件，不递归。当文件数超过 `--group-size`（默认 10）时，自动进入第一阶段：生成 JSON 分组文件和续跑提示，不直接翻译。当文件数不超过分组大小或 `--group-size` 为 0 时，直接返回完整预检结果。
  - `resolve --manifest <manifest>`：读取 JSON 或 TXT 清单。包括第一阶段生成的 batch JSON 分组文件，可直接作为第二阶段输入进行实际翻译。
- 不接受单个待翻译文件；单文件翻译请使用 `zm-translate-from-files`。
- JSON 清单必须使用顶层 `absolute_paths` 数组；TXT 清单每行一个绝对路径，忽略空行和 `#` 注释。
- 清单保留原始顺序；目录输入按字典序排序（`sorted(input_path.iterdir())`），含以 `.` 开头但后缀为 `.md` / `.txt` 的文件（隐藏文件不自动排除）；需要排除隐藏文件或指定顺序时改用 JSON / TXT 清单。
- TXT 清单中的非绝对路径在解析阶段即被拒收，错误信息含具体行号。
- 默认每个源文件旁生成 `{stem}-{primary_lang}/translation.md`；`primary_lang` 取 `--to`（默认 `zh-CN`）连字符前的 ISO 639 代码（如 `zh-CN` → `zh`、`en-US` → `en`）。显式 `--output-dir` 时集中到指定输出根目录下的同名翻译子目录。
- 只生成翻译产物与中间文件，不改写原始源文件。
- 已存在的输出目录不会直接覆盖；写入前必须先调用 `python3 scripts/main.py backup --output-dir <path>` 把现有目录搬移到 `<name>.backup-YYYYMMDD-HHMMSS`，再开始本轮翻译。
- 个别目标失败时继续处理其余 `status=ready` 目标；最终报告 `ok` / `partial` / `error` 与成功、失败、跳过汇总。
- 脚本只负责确定性预检、偏好解析和分块；真正的分析、翻译、审校与润色由 agent 按本 skill 工作流逐文件执行。

## 可复用资源

- `scripts/translation_preferences.py`：查找并解析本 skill 的 `EXTEND.md`，输出目标语言、模式、受众、风格、分块阈值与术语表。
- `scripts/resolve_targets.py`：目录/清单输入预检、去重、输出目录冲突检查、输出目录与备份路径规划。
- `scripts/chunk_markdown.py`：提取 frontmatter、按 Markdown 块边界切分长文并落盘 `chunks/`。
- `scripts/main.py`：统一 CLI 入口，收口 `prefs`、`resolve`、`chunk` 三类确定性动作。

## 偏好文件

`EXTEND.md` 为**可选配置**，通过 prompt 显式路径或 `--extend` 传入；未提供时使用安全默认值。

支持的键（默认值）：

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
2. **预检输入**（`scripts/main.py resolve --path <目录>` 或 `scripts/main.py resolve --manifest <清单>`）。
   - **目录输入**：若 ready 文件数 **超过** `--group-size`（默认 10），`result_status=batch_prepared`，**立即停止**，不进入后续翻译步骤。此时只输出分组 JSON 文件与自然语言续跑提示，由用户开启新会话处理各批次。
   - **目录输入**：若 ready 文件数 **未超过** `--group-size`，或 `--group-size` 为 0，继续后续步骤。
   - **清单输入**（含 batch JSON）：继续后续步骤。
3. 仅处理 `status=ready` 的目标；失败和跳过项写入最终报告。
4. 按清单原始顺序或目录排序逐文件翻译。
5. 按模式翻译：
   - `quick`：直译
   - `normal`：分析 → 翻译
   - `refined`：分析 → 草稿 → 审校 → 修订 → 润色
6. 单个文件词数达到 `chunk_threshold` 时先切块（`scripts/main.py chunk`）。
7. 合并术语约束生成译文：内置术语表 + `EXTEND.md` 术语表 + 用户显式要求。
8. 最终报告：`result_status`、成功/失败/跳过数、目标语言、模式、受众、风格、分块情况、术语表使用情况、备份路径与失败原因。

## 批处理（`batch_prepared`）

目录输入的两阶段流程：

1. **第一阶段（分批）**：`resolve --path` 扫描目录后，若 ready 文件数超过 `--group-size`（默认 10），返回 `result_status=batch_prepared`，同时生成分组 JSON 文件和自然语言续跑提示。此阶段不执行任何翻译，仅输出可直接粘贴到 AI 会话中执行的自然语言提示。
2. **第二阶段（并行处理）**：为每个批次开启独立会话，粘贴对应的 `resume_prompts[i]` 中的自然语言提示执行翻译。各批次互不依赖、顺序无要求，可并行运行以加速整体进度。批次全部完成后，翻译产物已分布在各自输出目录中，无需额外合并步骤。

## 子模块 `__main__` 入口

`scripts/main.py` 是统一 CLI 入口；`scripts/resolve_targets.py`、`scripts/translation_preferences.py`、`scripts/chunk_markdown.py` 自带的 `__main__` 入口仅供单元测试与独立调试保留，README 与 SKILL.md 的所有示例只走 `main.py`。

详细步骤见 `references/workflow.md`。

## 本地辅助命令

开发态与安装态命令示例统一收录在 `README.md` 备选用法；本节不再重复命令清单。安装态推荐使用 frontmatter `compatibility.runtime.call_command` 展开的真实命令。

## 失败口径

- `missing_input`：清单中的输入路径不存在。
- `file_input_not_supported`：`--path` 收到单个文件；请改用 `zm-translate-from-files`。
- `unsupported_input`：输入不是支持的普通 `.md` 或 `.txt` 文件。
- `unsafe_symlink_input`：输入目录、清单或目标文件是符号链接。
- `unsafe_output_path`：输出目录或指定的输出根目录是符号链接、被非目录路径占用，或多个输入解析到同一输出目录。
- `manifest_missing`：清单路径不存在。
- `manifest_parse_failed`：清单不是合法 JSON/TXT 格式，或 JSON 缺少顶层 `absolute_paths` 数组。
- `batch_prepared`：目录输入时第一阶段完成，已生成分组 JSON 文件和续跑提示（非失败状态）。
- `manifest_empty`：目录当前层或清单没有可处理路径。
- `manifest_relative_path`：清单条目不是绝对路径。
- `duplicate_input`：重复路径已跳过，首次出现的路径保留。
- `chunk_failed`：分块脚本无法读取文件，或无法写入目标 `chunks/`。

## 参考

- 需要确认翻译模式与中间产物时，读取 `references/workflow.md`。
- 需要确认 `EXTEND.md` 键结构时，读取 `references/extend-schema.md`。
- 需要直接复用英中术语时，读取 `references/glossary-en-zh.md`。
