# PaddleOCR 常用调参

> `local` 模式下，PaddleOCR 是默认引擎（CPU 友好、中文支持最好）。本文档整理 `PADDLE_PARAMS` 环境变量常用参数、默认值与调参建议。

## 配置位置

`~/.config/zm-batch-ocr2md/.local_env`：

```bash
LOCAL_ENGINE=paddle
PADDLE_PARAMS=--use_textline_orientation=True --lang=ch
```

`PADDLE_PARAMS` 会在脚本内被拆分为 `argparse`-style 参数列表后传入 PaddleOCR。

## 常用参数

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--lang` | `ch` / `en` / `fr` / ... | `ch` | OCR 识别语种；中文选 `ch` |
| `--use_textline_orientation` | `True` / `False` | `False` | 启用文本行方向分类（旋转 / 倾斜文本更准） |
| `--use_doc_orientation_classify` | `True` / `False` | `False` | 启用文档方向分类（整页旋转 90°/180°/270°） |
| `--use_doc_unwarping` | `True` / `False` | `False` | 启用文档形变校正（拍照扫描） |
| `--use_seal_recognition` | `True` / `False` | `False` | 启用印章识别（财务、合同类文档） |
| `--use_table_recognition` | `True` / `False` | `False` | 启用表格识别与结构化输出 |
| `--device` | `cpu` / `gpu` / `gpu:0` | `cpu` | 推理设备；GPU 需先安装 `paddlepaddle-gpu` |
| `--enable_mkldnn` | `True` / `False` | `True`（CPU） | 启用 MKL-DNN 加速（Intel CPU 推荐） |
| `--cpu_threads` | 整数 | 10 | CPU 推理线程数 |

## 调参建议

### 中文扫描件

```bash
PADDLE_PARAMS=--use_textline_orientation=True --lang=ch
```

### 拍照文档（含倾斜 / 形变）

```bash
PADDLE_PARAMS=--use_doc_orientation_classify=True --use_doc_unwarping=True --use_textline_orientation=True --lang=ch
```

### 财务 / 合同文档（含印章 / 表格）

```bash
PADDLE_PARAMS=--use_textline_orientation=True --use_seal_recognition=True --use_table_recognition=True --lang=ch
```

### GPU 加速

先切换 `paddlepaddle` 到 GPU 版本：

```bash
conda run -n ocr pip install paddlepaddle-gpu
```

再启用：

```bash
PADDLE_PARAMS=--device=gpu --lang=ch
```

## 性能与精度取舍

| 取舍 | 建议 |
|---|---|
| 速度优先（少量图） | 关闭所有可选模块，仅保留 `--lang=ch` |
| 精度优先（重要文档） | 启用 `--use_textline_orientation=True`，需要时再加表格 / 印章 |
| 大批量（>100 张） | 关闭非必要模块，CPU 模式调低 `--cpu_threads` 防止内存竞争 |
| 模糊或低分辨率 | 启用 `--use_doc_unwarping=True` + `--use_textline_orientation=True` |

## 故障排查

| 错误 | 原因 | 处置 |
|---|---|---|
| `paddleocr_import_failed` | conda 环境 `ocr` 未安装 paddleocr | `conda run -n ocr pip install paddleocr paddlepaddle` |
| `paddleocr_failed` | 推理过程报错 | 检查图像格式、降低并发、调小 `--cpu_threads` |
| 中文识别为空 | 漏掉 `--lang=ch` | 显式声明 `--lang=ch` |
| 旋转 90° 文本乱识别 | 未启用文本行方向分类 | 加 `--use_textline_orientation=True` |
