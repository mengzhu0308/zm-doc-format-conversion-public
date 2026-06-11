# Chandra OCR 2 后端差异

> `local` 模式下，Chandra OCR 2 支持三种部署后端：`hf`（默认）、`vllm`、`docker`。本文档描述三种后端的部署差异、适用场景与配置项。

## 后端对比

| 后端 | 部署形式 | 启动开销 | 单实例吞吐 | 多实例支持 | 离线运行 |
|---|---|---|---|---|---|
| `hf` | 本地 transformers 加载 | 中（首次下载） | 中 | 不支持 | 缓存存在即可 |
| `vllm` | vLLM OpenAI 兼容服务 | 高（需预启动） | 高 | 支持并发 | 取决于配置 |
| `docker` | Docker 容器内置服务 | 中 | 中 | 取决于容器编排 | 取决于镜像 |

## HF 后端（默认）

- 通过 `chandra --method hf` 命令直接调用
- 模型：当前默认 `ArliAI/chandra-ocr-2`（已本地缓存，HF 缓存目录命名 `models--ArliAI--chandra-ocr-2`）
- 缓存策略：本地缓存存在时强制离线，不存在时允许联网下载
- 适用场景：单用户、单任务、CPU/GPU 通用

配置：

```bash
LOCAL_ENGINE=chandra
CHANDRA_BACKEND=hf
```

## vLLM 后端

- 通过本地 vLLM 服务（OpenAI 兼容协议）调用
- 默认端点：`http://localhost:8000/v1`
- 启动（vLLM ≥ 0.5.0）：`vllm serve ArliAI/chandra-ocr-2 --port 8000`
- 启动（vLLM < 0.5.0，老命令）：`python -m vllm.entrypoints.openai.api_server --model ArliAI/chandra-ocr-2 --port 8000`
- 适用场景：高并发任务、GPU 集群部署

配置：

```bash
LOCAL_ENGINE=chandra
CHANDRA_BACKEND=vllm
CHANDRA_VLLM_ENDPOINT=http://localhost:8000/v1
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
```

## 选择建议

| 场景 | 推荐后端 |
|---|---|
| 单张图 | `hf` |
| 高吞吐单 GPU | `vllm` |
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
