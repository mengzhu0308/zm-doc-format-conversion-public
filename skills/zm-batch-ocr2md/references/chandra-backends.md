# Chandra OCR 2 后端差异

> `local` 模式下，Chandra OCR 2 支持三种部署后端：`hf`（默认）、`vllm`、`docker`。本文档描述三种后端的部署差异、适用场景与配置项。

## 后端对比

| 后端 | 部署形式 | 启动开销 | 单实例吞吐 | 多实例支持 | 离线运行 |
|---|---|---|---|---|---|
| `hf` | 本地 transformers 加载 | 中（首次下载） | 中 | 不支持 | 缓存存在即可 |
| `vllm` | vLLM OpenAI 兼容服务 | 高（需预启动） | 高 | 支持并发 | 取决于配置 |
| `docker` | Docker 容器内置服务 | 中 | 中 | 取决于容器编排 | 取决于镜像 |

## HF 后端（默认）

- 通过 `conda run -n ocr chandra --method hf <INPUT_PATH> <OUTPUT_PATH>` 调用
- HF 模型（HuggingFace repo 名）：当前默认 `datalab-to/chandra-ocr-2`（已本地缓存）
- 缓存策略：本地缓存存在时强制离线，不存在时允许联网下载
- 适用场景：单用户、单任务、CPU/GPU 通用
- 实际 CLI 支持的 method 仅有 `hf` 与 `vllm`；`docker` 后端不走 chandra CLI，而是用 OpenAI 兼容 API 直接调远程端点

> 模型名语境区分（C-3）：HF 路径使用的是 HuggingFace repo 名（`datalab-to/chandra-ocr-2`），由 chandra CLI 内部解析；vLLM / Docker 路径走 OpenAI 兼容协议，`model` 字段是服务侧 `--served-model-name` 暴露的标识（默认 `chandra-ocr-2`），与 HF repo 名不可互换，配置文件 `CHANDRA_VLLM_MODEL` / `CHANDRA_DOCKER_MODEL` 控制的就是该字段。

配置：

```bash
LOCAL_ENGINE=chandra
CHANDRA_BACKEND=hf
```

## vLLM 后端

- 通过本地 vLLM 服务（OpenAI 兼容协议）调用
- 默认端点：`http://localhost:8000/v1`
- 启动：`vllm serve datalab-to/chandra-ocr-2 --port 8000`
- 适用场景：高并发批量任务、GPU 集群部署

配置：

```bash
LOCAL_ENGINE=chandra
CHANDRA_BACKEND=vllm
CHANDRA_VLLM_ENDPOINT=http://localhost:8000/v1
# vLLM 服务对外暴露的模型名（默认 chandra-ocr-2；按你的 vLLM 启动配置调整）
CHANDRA_VLLM_MODEL=chandra-ocr-2
```

## Docker 后端

- 通过 Docker 容器内置的 Chandra 服务调用
- 默认端点：`http://localhost:8501/v1`
- 启动：`docker run -p 8501:8501 <chandra-image>`
- 适用场景：环境隔离、可重现部署、CID 友好

配置：

```bash
LOCAL_ENGINE=chandra
CHANDRA_BACKEND=docker
CHANDRA_DOCKER_ENDPOINT=http://localhost:8501/v1
# Docker 容器服务的模型名（默认 chandra-ocr-2；按容器实际配置调整）
CHANDRA_DOCKER_MODEL=chandra-ocr-2
```

## 模型名覆盖

如 vLLM / Docker 服务对外暴露的模型名与默认 `chandra-ocr-2` 不一致，可用 `CHANDRA_VLLM_MODEL` / `CHANDRA_DOCKER_MODEL` 分别覆盖两个后端在 OpenAI 兼容协议中发送的 `model` 字段。

## 选择建议

| 场景 | 推荐后端 |
|---|---|
| 单张图或小批量（<10 张） | `hf` |
| 大批量（>100 张）单 GPU | `vllm` |
| 跨平台部署 / CI 集成 | `docker` |
| 严格离线（无外网） | `hf`（缓存已就位） |
| 多 GPU 并发 | `vllm` |

## 故障排查

| 错误信息 | 原因 | 处置 |
|---|---|---|
| `chandra_import_failed` | 虚拟环境 `ocr` 未安装 `chandra-ocr-2` | `conda run -n ocr pip install chandra-ocr-2` |
| `chandra_inference_failed`（hf） | 模型未下载或缓存损坏 | 检查 `~/.cache/huggingface/` 是否完整 |
| `chandra_inference_failed`（vllm/docker） | 后端服务未启动或端点不可达 | `curl <endpoint>/v1/models` 验证服务 |
| 连接超时 | 端点配置错误 | 检查 `CHANDRA_VLLM_ENDPOINT` / `CHANDRA_DOCKER_ENDPOINT` |
