# zm-batch-ocr2md 参考文档

本目录把 `SKILL.md` 中较长的技术细节拆分为独立参考文档，供实现、调试、集成时按需查阅。`SKILL.md` 仍是核心合同，遇到与本文档冲突时以 `SKILL.md` 为准。

| 文档 | 适用范围 |
|---|---|
| [manifest-format.md](manifest-format.md) | `.json` / `.txt` 清单输入的格式、过滤规则、状态码与样例 |
| [mcp-degradation.md](mcp-degradation.md) | MCP 模式内部降级链路、触发条件、边界与误区 |
| [chandra-backends.md](chandra-backends.md) | Chandra OCR 2 的 HF / vLLM / Docker 三种后端部署差异 |
| [paddleocr-params.md](paddleocr-params.md) | PaddleOCR 引擎常用参数、默认值与调参建议 |
