# MCP 模式内部降级链路

> 0.2.0 起 MCP 模式新增 `moonshot-vision` 作为 MCP 内部降级备选；0.2.0 之前 MCP 模式无降级链路。当前链路：`minimax-coding-plan-mcp`（主选）→ `moonshot-vision`（降级）。本降级**仅限定在 MCP 工具链内部**，**不会跨 provider 自动降级到 `api` / `local`**。

## 降级链路顺序

| 顺序 | MCP 工具 | 调用形式 |
|---|---|---|
| 主选 | `minimax-coding-plan-mcp` | `mcp__minimax-coding-plan-mcp__understand_image(prompt, image_source)` |
| 降级 | `moonshot-vision` | `mcp__moonshot-vision__understand_image(image_source, prompt)` |

AI Agent 在 MCP 模式下应严格按上述顺序调用；提示词中若显式指定主选，以提示词为准。

## 降级触发条件（任一即触发）

降级到 `moonshot-vision` 仅在以下任一条件成立时发生：

1. **主选 MCP 工具不可用**：未挂载、未授权、连接失败
2. **主选返回错误状态**：超时、限流（429 / 5xx）、内部错误
3. **主选返回空内容或非法响应**：响应体为空、JSON 解析失败、内容字段缺失

普通内容错误（如 OCR 识别错误）不触发降级 —— AI Agent 应直接处理或按用户偏好重试。

## 不触发降级的边界

以下情况**不会**触发 `mcp → mcp` 内部降级，也不会触发 `mcp → api/local` 跨 provider 降级：

- 主选返回的 OCR 内容看起来"不准确"
- 图像包含敏感内容（银行流水、证件、医疗记录等）
- 主选调用耗时较长但未超时
- 用户对结果不满意

`--provider` 一旦通过命令行或配置指定，执行过程中严格固定：

- `mcp` 模式：仅在上述 3 个降级条件内自动切换 MCP 工具
- `api` 模式：不切换到 MCP 或 local
- `local` 模式：不切换到 MCP 或 api
- 跨 provider 切换必须用户显式重新指定 `--provider`

## MCP 完全不可用时的行为

当 `mcp` 模式完全不可用（例如两个 MCP 工具均不可用或均失败）时，脚本统一返回 `mcp_required` 状态码，**不会**自动 fallback 到 `api` 或 `local`。AI Agent 应：

1. 停止后续 OCR 处理
2. 显式提示用户：MCP 模式不可用，需要切换 provider
3. 由用户显式指定 `--provider api` 或 `--provider local` 后重启处理

## 与 `--provider` 严格固定条款的关系

`--provider` 严格固定是 P0 级别的核心合同：

> `--provider` 参数一旦通过命令行或配置指定，执行过程中严格固定，不会自动降级、切换或 fallback 到其他 provider。MCP 模式内的"降级"仅指 `minimax-coding-plan-mcp` → `moonshot-vision` 这两个 MCP 工具之间的切换。

MCP 内部的 `mcp → mcp` 降级是同一 provider 内的子链路切换；跨 provider（`mcp → api`、`mcp → local`）切换必须由用户显式指定。

## 常见误区

| 误区 | 正确理解 |
|---|---|
| "MCP 模式会自动 fallback 到 local OCR" | 错。MCP 模式完全不可用时只返回 `mcp_required` |
| "银行流水图像应该用 local OCR" | 错。provider 选择与图像内容敏感度无关 |
| "MCP 模式失败越多越应该切到 local" | 错。失败计数不触发跨 provider 降级 |
| "提示词指定 provider 后脚本可以静默切换" | 错。`--provider` 严格固定，跨 provider 切换必须显式指定 |
