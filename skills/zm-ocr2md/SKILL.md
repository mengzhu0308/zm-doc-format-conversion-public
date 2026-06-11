---
name: zm-ocr2md
description: >-
  将单张本地图像（PNG/JPG/JPEG/WebP）OCR 提取为 Markdown。仅处理单个本地图像文件，不接受目录、manifest 清单或 URL。默认 MCP 模式（minimax-coding-plan-mcp 优先，MCP 内部失败后降级 moonshot-vision）；支持 --provider 切换为远程 API 或本地 OCR（PaddleOCR / Chandra OCR 2）。不因内容敏感度预判切换 provider，--provider 一旦选定严格固定。
metadata:
  skill_mode: hybrid
compatibility:
  system_tools:
    - name: minimax-coding-plan-mcp
      call_command: mcp__minimax-coding-plan-mcp__understand_image <prompt> <image_source>
    - name: moonshot-vision
      call_command: mcp__moonshot-vision__understand_image <image_source> <prompt>
  runtime:
    - name: ocr
      call_command: conda run -n ocr python "$SKILL_DIR/scripts/run.py" [args]
  config_files:
    - path: ~/.config/zm-ocr2md/.env
      description: 远程 API 模式配置
      required_fields: [API_KEY]
    - path: ~/.config/zm-ocr2md/.local_env
      description: 本地 OCR 模式配置
      required_fields: []
---

# zm-ocr2md

## 核心合同

- 只接受单张本地图像文件：`.png`、`.jpg`、`.jpeg`、`.webp`。
- 不接受目录、manifest 清单、URL 或其他文件格式；目录和清单类输入请改用 `zm-batch-ocr2md`。
- 默认输出为源图像同目录下的 `{安全文件名}.md`；可用 `--output-dir` 指定输出目录。
- 不改写原始图像文件。
- 支持三种 OCR 模式：`mcp`（默认）、`api`、`local`，由 `--provider` 参数选择。
- 禁止基于内容敏感度预切换 provider。`--provider` 一旦选定，执行过程中不会自动降级、切换或 fallback 到其他 provider。
- MCP 模式内的“降级”仅指 `minimax-coding-plan-mcp` -> `moonshot-vision` 这两个 MCP 工具之间的切换；如果 MCP 模式完全不可用，脚本返回 `mcp_required`，不会自动 fallback 到 `api` 或 `local`。

## OCR 模式

### MCP（默认）

脚本直跑会返回 `mcp_required`，由 AI Agent 调用视觉模型 `understand_image`：

| 顺序 | MCP 工具 | 调用形式 |
|---|---|---|
| 主选 | `minimax-coding-plan-mcp` | `mcp__minimax-coding-plan-mcp__understand_image(prompt, image_source)` |
| 降级 | `moonshot-vision` | `mcp__moonshot-vision__understand_image(image_source, prompt)` |

MCP 内部降级触发条件见 [references/mcp-degradation.md](references/mcp-degradation.md)。

### 远程 API

通过 `~/.config/zm-ocr2md/.env` 配置 OpenAI 兼容 API：

```bash
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --input /path/to/image.png --provider api
```

### 本地 OCR

通过 conda 环境 `ocr` 运行，支持 PaddleOCR（默认）和 Chandra OCR 2：

```bash
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --input /path/to/image.png --provider local
```

Chandra 后端和 PaddleOCR 参数见 [references/chandra-backends.md](references/chandra-backends.md) 与 [references/paddleocr-params.md](references/paddleocr-params.md)。

## 配置字段

### 远程 API 模式（`~/.config/zm-ocr2md/.env`）

| 字段 | 用途 | 默认值 | 必填 | 备注 |
|---|---|---|---|---|
| `API_BASE` | OpenAI 兼容 API base URL | `https://api.openai.com/v1` | 否 | 也用于 vLLM/Docker 本地后端 |
| `API_KEY` | API 密钥 | 空 | `--provider api` 模式下必填 | 空值时不发送 `Authorization` 头 |
| `API_MODEL` | 模型名 | `gpt-4o` | 否 | vLLM/Docker 后端通常用 `chandra-ocr-2` |
| `MAX_TOKENS` | 单次响应最大 token 数 | `8192` | 否 | 整数；空值或非法值回落默认 |
| `API_TIMEOUT` | API 请求超时（秒） | `120` | 否 | 整数；空值或非法值回落默认 |
| `OCR_PROMPT` | 远程 API 模式 OCR 提示词 | 中文 OCR 默认提示 | 否 | 自定义为更精细的公式/表格识别 |

完整示例见 [assets/config.env.example](assets/config.env.example)。

### 本地 OCR 模式（`~/.config/zm-ocr2md/.local_env`）

| 字段 | 用途 | 默认值 | 必填 | 备注 |
|---|---|---|---|---|
| `LOCAL_CACHE_DIR` | 模型缓存目录 | 空（用 PaddleOCR/HF 默认位置） | 否 | 留空走默认；PaddleOCR 默认 `~/.paddlex/official_models/`，Chandra HF 默认 `~/.cache/huggingface/hub/` |
| `LOCAL_ENGINE` | 本地 OCR 引擎 | `paddle` | 否 | `paddle` 或 `chandra` |
| `PADDLE_PARAMS` | PaddleOCR 启动参数 | `--use_textline_orientation=True --lang=ch` | 否 | 详见 [references/paddleocr-params.md](references/paddleocr-params.md) |
| `MAX_IMAGE_SIZE` | 长边超过此尺寸（像素）自动缩放 | `1000` | 否 | 整数；避免内存溢出 |
| `CHANDRA_BACKEND` | Chandra 后端 | `hf` | 否 | `hf` / `vllm` / `docker` |
| `CHANDRA_VLLM_ENDPOINT` | vLLM 后端 OpenAI 兼容服务地址 | `http://localhost:8000/v1` | 否 | vLLM ≥ 0.5.0 启动命令：`vllm serve ArliAI/chandra-ocr-2 --port 8000` |
| `CHANDRA_DOCKER_ENDPOINT` | Docker 后端服务地址 | `http://localhost:8501/v1` | 否 | 启动：`docker run -p 8501:8501 <chandra-image>` |
| `CHANDRA_TIMEOUT` | Chandra HF 推理超时（秒） | `600` | 否 | 整数；空值或非法值回落默认 |

完整示例见 [assets/config.local_env.example](assets/config.local_env.example)。

## 脚本入口

```bash
SKILL_DIR="/absolute/path/to/zm-ocr2md"
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --input /path/to/image.png --provider mcp
```

公开参数：

| 参数 | 说明 |
|---|---|
| `--input IMAGE` | 必填，单张本地图像文件 |
| `--provider mcp|api|local` | OCR 模式，默认读取配置或使用 `mcp` |
| `--output-dir DIR` | 输出目录，默认源图像同目录 |
| `--json` | 输出 JSON 结果 |
| `--env-file FILE` | 覆盖远程 API 配置文件路径 |
| `--local-env-file FILE` | 覆盖本地 OCR 配置文件路径 |

## 状态码

| 状态码 | 含义 | 退出码 |
|---|---|---|
| `success` | OCR 成功并写出 Markdown | 0 |
| `partial` | 单图未完全完成（聚合状态，含 mcp_required、各 provider 失败、save_failed、process_image 路径下的 output_dir_create_failed） | 2 |
| `mcp_required` | 单张目标需 AI Agent 调用 MCP 视觉工具 | 2 |
| `missing_input` | 输入路径不存在 | 1 |
| `non_single_input` | 输入是目录或清单类文件，不属于单图合同 | 1 |
| `unsupported_format` | 文件存在但不是支持的图像格式 | 1 |
| `openai_api_failed` | 远程 API 或 OpenAI 兼容本地服务调用失败 | 2 |
| `paddleocr_failed` | PaddleOCR 推理失败 | 2 |
| `paddleocr_import_failed` | PaddleOCR 导入失败 | 2 |
| `chandra_inference_failed` | Chandra OCR 2 推理失败 | 2 |
| `chandra_import_failed` | Chandra OCR 2 导入失败 | 2 |
| `output_dir_create_failed` | 输出目录创建失败（run() 自身走 1；process_image 路径下被聚合为 `partial` 走 2） | 1 / 2 |
| `save_failed` | Markdown 保存失败 | 2 |

退出码契约由 `scripts/run.py` 的 `main()` 统一维护：0 = 成功，1 = 输入/输出前置失败，2 = 处理中失败但可恢复（partial）。`partial` 与 `mcp_required` 同为退出码 2，调用方可用 `--json` 输出中的 `result_status` 进一步区分。`output_dir_create_failed` 在两种产生路径下退出码不同：run() 自身（用户指定 `--output-dir` 但目录无法创建）退出码 1；process_image 内部（先于 provider 调用前）退出码 2。

## 可复用资源

- `scripts/run.py`：单图 OCR 脚本，包含 MCP/API/local provider 实现；`main()` 负责参数解析与退出码映射，`run()` 返回 JSON 友好的结果字典（顶层 `result_status` / `summary` / `targets` / `files_created`，每个 `target` 含 `source` / `status` / `message` / `output_files` / `details`）。
- `assets/config.env.example`：远程 API 模式配置示例，列出 `API_BASE` / `API_KEY` / `API_MODEL` / `MAX_TOKENS` / `API_TIMEOUT` / `OCR_PROMPT` 等可选项。
- `assets/config.local_env.example`：本地 OCR 模式配置示例，列出 `LOCAL_ENGINE` / `PADDLE_PARAMS` / `MAX_IMAGE_SIZE` / `CHANDRA_BACKEND` / `CHANDRA_TIMEOUT` 等可选项。
- `references/`：按需查看的 MCP、本地 OCR 参数和后端细节。

版本号以 `VERSION.yaml` 为单一真相来源；更新版本时同步 `agents/openai.yaml` 的 `skill_info.version`。
