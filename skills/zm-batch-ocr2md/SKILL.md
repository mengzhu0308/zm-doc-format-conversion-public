---
name: zm-batch-ocr2md
description: >-
  将本地当前层图片目录或 JSON/TXT manifest 中的多张图像 OCR 提取为 Markdown。仅本地路径，不递归；不接受单张图片输入。默认 MCP 模式（minimax-coding-plan-mcp 优先，MCP 内部失败后降级 moonshot-vision），MCP 每批默认 30 张；支持 --provider 切换为远程 API 或本地 OCR（PaddleOCR / Chandra OCR 2）。配置目录独立使用 ~/.config/zm-batch-ocr2md/。
metadata:
  skill_mode: hybrid
compatibility:
  system_tools:
    - name: minimax-coding-plan-mcp
      call_command: minimax-coding-plan-mcp understand_image <image>
    - name: moonshot-vision
      call_command: moonshot-vision understand_image <image>
  runtime:
    - name: ocr
      call_command: conda run -n ocr python <script> [args]
      note: 默认环境名 ocr；如使用其他命名，可在 .local_env 中设置 OCR_CONDA_ENV=<env-name> 覆盖，paddle 与 chandra 两条调用路径都读取该变量（C-4）
  config_files:
    - path: ~/.config/zm-batch-ocr2md/.env
      description: 远程 API 模式配置
      required_fields: [API_BASE, API_KEY, API_MODEL]
    - path: ~/.config/zm-batch-ocr2md/.local_env
      description: 本地 OCR 模式配置
      required_fields: [LOCAL_ENGINE]
---

# zm-batch-ocr2md

## 核心合同

- 只接受两类批量输入：
  - 当前层图片目录，不递归扫描子目录。目录输入时默认进入**第一阶段**：将目录中的图片路径按组写入 JSON 分组文件（默认每组 10 张），同时生成 `progress.json` 进度跟踪文件，并输出续跑提示；**不直接处理图片**。
  - JSON/TXT manifest 清单。包括第一阶段生成的 batch JSON 分组文件，可直接作为**第二阶段**输入进行实际 OCR 处理。
- **两阶段严格分界**：第一阶段返回 `batch_prepared` 后，当前会话的任务即完成，绝不在当前会话中继续处理任何 batch。第二阶段（实际 OCR）必须由用户显式触发，方式有三：
  - **单会话续跑**：使用 `--resume <progress.json 或 batches 目录>`，在单一会话内自动顺序处理所有未完成的 batch。
  - **多会话并行**：按续跑提示为每个 batch 开新 Agent 会话，输入对应的 `batch_*.json` 清单并行执行。
  - **单批次处理**：直接 `--input batch_*.json` 处理单个批次。
- 不接受单张图片输入；单图 OCR 属于独立单文件流程。
- 支持图像格式：`.png`、`.jpg`、`.jpeg`、`.webp`。
- 目录默认输出到输入目录父目录下的 `<目录名>_ocr2md/`。
- manifest 默认输出到 manifest 所在目录下的 `<manifest-stem>_ocr2md/`。
- 所有 Markdown 扁平输出，不保留源目录层级；每处理完一张立即落盘。
- 支持三种 OCR 模式：`mcp`（默认）、`api`、`local`。
- MCP 模式每批默认 30 张；超过时脚本返回分批提示，AI Agent 应按批处理并即时落盘。
- `--provider` 一旦选定严格固定，不会跨 provider 自动降级。MCP 内部降级仅指 `minimax-coding-plan-mcp` -> `moonshot-vision`。
- 配置文件独立于单图版，位于 `~/.config/zm-batch-ocr2md/.env` 与 `~/.config/zm-batch-ocr2md/.local_env`。
- conda 环境名默认 `ocr`；如使用其他命名（如 `ocr-cpu` / `paddle-env`），可在 `.local_env` 中设置 `OCR_CONDA_ENV=<env-name>` 覆盖。SKILL.md frontmatter `compatibility.runtime[0].call_command` 默认写死 `-n ocr` 是为消费方提供"未覆盖时的初始模板"；实际执行以 `OCR_CONDA_ENV` 为准。
- 默认跳过已生成图像：若 `<输入名>_ocr2md/<stem>.md` 已存在且非空，直接返回 `success` + `skipped: true`，不发起 OCR 调用；可用 `--force` 强制重跑。

## Manifest 格式

JSON manifest 读取顶层 `absolute_paths` 数组；TXT manifest 逐行读取路径并跳过空行和 `#` 注释行。两者均会过滤不存在或非支持图像格式的路径，并保留原始顺序。

详情见 [references/manifest-format.md](references/manifest-format.md)。

## 脚本入口

```bash
SKILL_DIR="/absolute/path/to/zm-batch-ocr2md"

# 第一阶段：目录输入，生成分组 JSON + progress.json（只分组，不 OCR）
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --input /path/to/pages --provider mcp

# 第二阶段（单会话续跑）：自动处理所有未完成批次
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --resume /path/to/pages/batches/progress.json --provider mcp

# 第二阶段（单批次处理）
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --input /path/to/pages/batches/batch_001.json --provider mcp
```

公开参数：

| 参数 | 说明 |
|---|---|
| `--input DIRECTORY_OR_MANIFEST` | 必填，当前层图片目录或 JSON/TXT manifest |
| `--provider mcp|api|local` | OCR 模式，默认读取配置或使用 `mcp` |
| `--output-dir DIR` | 输出目录，默认 `<输入名>_ocr2md/` |
| `--json` | 输出 JSON 结果 |
| `--batch-size N` | MCP 模式每批最大图片数，默认 30 |
| `--group-size N` | 目录输入时，每个分组 JSON 文件包含的最大图片数，默认 10；设为 0 则直接处理 |
| `--manifest` | 将非标准扩展名输入显式按 manifest 解析 |
| `--env-file FILE` | 覆盖远程 API 配置文件路径 |
| `--local-env-file FILE` | 覆盖本地 OCR 配置文件路径 |
| `--resume PATH` | 续跑模式：传入 `progress.json` 文件路径或其所在目录，自动处理所有未完成批次 |

## 状态码

| 状态码 | 含义 |
|---|---|
| `batch_prepared` | 目录输入时第一阶段完成，已生成分组 JSON 文件、progress.json 和续跑提示 |
| `resume_completed` | `--resume` 续跑完成，处理了至少一个批次，退出码 0 |
| `all_completed` | `--resume` 时 progress.json 中所有批次已处理完成，无需处理，退出码 0 |
| `success` | 全部图像 OCR 成功，退出码 0 |
| `mcp_only` | 全部目标均需 AI Agent 调用 MCP 视觉工具接力处理，退出码 0（不是失败） |
| `partial` | 部分成功、失败或 success/mcp_required 混合，退出码 2 |
| `batch_input_required` | 输入是单张图片，不属于批量合同 |
| `missing_input` | 输入路径不存在 |
| `empty_input_dir` | 目录当前层没有有效图像 |
| `manifest_parse_failed` | JSON/TXT manifest 解析失败 |
| `manifest_empty` | manifest 过滤后无有效图像路径 |
| `unsupported_format` | 输入文件不是 manifest 且不是可解析对象 |
| `mcp_required` | 单个目标需 AI Agent 调用 MCP 视觉工具 |
| `openai_api_failed` | 远程 API 或 OpenAI 兼容本地服务调用失败 |
| `paddleocr_failed` / `paddleocr_import_failed` | PaddleOCR 推理或导入失败 |
| `chandra_inference_failed` / `chandra_import_failed` | Chandra OCR 2 推理或导入失败 |
| `output_dir_create_failed` / `save_failed` | 输出目录创建或 Markdown 保存失败 |

## 可复用资源

- `scripts/run.py`：批量 OCR 脚本，独立包含 MCP/API/local provider 实现。
- `assets/config.env.example`：远程 API 模式配置示例。
- `assets/config.local_env.example`：本地 OCR 模式配置示例。
- `references/`：manifest、MCP 降级、本地 OCR 后端与参数说明。

版本号以 `VERSION.yaml` 为单一真相来源；更新版本时同步 `agents/openai.yaml` 的 `skill_info.version`。
