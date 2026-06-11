---
name: zm-pdf2image
description: >-
  将本地 PDF `.pdf` 文件或本地目录当前层内的多个 `.pdf` 批量转换为 PNG 或 JPG 图片。适用于先把 PDF 拆成逐页图片，再交给 OCR、人工校对或后续图像流程。仅处理本地路径；不会改写原始 PDF；不递归扫描子目录。
metadata:
  skill_mode: hybrid
compatibility:
  runtime:
    - name: agent-skills
      call_command: conda run -n agent-skills python <script> [args]
  system_tools:
    - name: poppler-utils
      call_command: pdftoppm [args]
---

# zm-pdf2image

## 核心合同

- 只接受本地路径，不接受 URL。
- 输入文件必须以 `.pdf` 结尾（不区分大小写）；其他后缀一律 `not_a_pdf` 拒绝。
- 单文件或目录输入；目录只扫描当前层 `.pdf`，不递归。
- 输出格式默认 PNG，可选 JPG；每页转为一张独立图片。
- 输出到源文件同目录，或 `--output-dir` 指定目录；命名规则详见下文『输出文件命名规则』。
- 不改写原始 PDF。
- 默认按 `MediaBox` 全幅渲染（`use_cropbox=False`），避免丢内容；如需按 `CropBox` 裁切，传入 `--cropbox`。
- 单页保存中途失败会回滚已写入的图片与空目录。
- 依赖 `pdf2image` + `Pillow` Python 包和 `poppler` 系统库。

## 可复用资源

- `scripts/run.py`：输入预检、批量转换、图片落盘、结构化结果输出。
- `scripts/_smoke.py`：最小自检脚本，验证 5 页 / 0 页 / 损坏 / 非 PDF 四类典型输入。

## 运行前提

脚本在 `agent-skills` python 环境中运行，已预装 `pdf2image` 和 `Pillow`。Linux 系统需额外安装 poppler：

```bash
sudo apt install -y poppler-utils
```

macOS：

```bash
brew install poppler
```

Windows：`pdf2image` 自带 Windows 依赖，无需额外安装。

安装后可用 `pdftoppm -v` 验证 poppler 是否可用。

## 输入输出约定

| 输入 | 输出 |
|---|---|
| 单文件 `/path/demo.pdf` | 同目录 `demo/image-N.png`，或 `--output-dir` 指定目录 |
| 目录 `/path/pdfs/` | 每个 PDF 同名子目录下的 `image-N.png` |

输出格式通过 `--format png` 或 `--format jpg` 指定，默认 PNG。命名规则详见下文『输出文件命名规则』。

## 默认流程

1. 验证 `--path` 存在，判断是单文件还是目录。
2. 目录输入只扫描当前层 `.pdf`，不递归。
3. 确定输出根目录：默认源文件同目录，或 `--output-dir` 指定目录。
4. 每个 PDF 创建/复用同名子目录，调用 `pdf2image.convert_from_path()` 按页渲染（默认 `use_cropbox=False`，`--timeout` 默认 60s）。
5. 逐页写入 `image-N.{格式}`，汇总成功数、总页数和输出路径。
6. 0 页 PDF 不创建子目录；中途保存失败时回滚已写文件与空目录。

## 开发源码

> 以下命令从仓库源码根目录（`skills/zm-pdf2image/`）执行；安装态请参考 [README.md](README.md) 备选用法（基于 `$SKILL_DIR/scripts/run.py`）。

```bash
# 单文件
conda run -n agent-skills python scripts/run.py --path /path/demo.pdf

# 批量目录
conda run -n agent-skills python scripts/run.py --path /path/pdfs/
```

可通过 `--format jpg`、`--output-dir /out/`、`--dpi 200`、`--cropbox`、`--timeout 120` 等参数调整。

## 脚本参数

| 参数 | 说明 |
|---|---|
| `--path` | 输入 PDF 文件或目录（必选） |
| `--output-dir` | 输出目录，默认源文件同目录 |
| `--format` | 输出格式：`png` 或 `jpg`，默认 `png` |
| `--dpi` | 图片 DPI，默认 300，必须为正整数 |
| `--cropbox` | 按 PDF `CropBox` 裁切；默认按 `MediaBox` 全幅渲染 |
| `--timeout` | 单 PDF 转换超时（秒），默认 60，必须为正整数 |
| `--json` | 以 JSON 输出结果 |
| `--verbose` / `-v` | 显示详细日志 |
| `--help` | 显示帮助 |

## 失败口径

| 状态码 | 退出码 | 含义 |
|---|---|---|
| `success` | 0 | 转换成功 |
| `partial` | 2 | 部分成功部分失败 |
| `missing_input` | 1 | 输入路径不存在 |
| `empty_input_dir` | 1 | 目录中没有 `.pdf` |
| `permission_denied` | 1 | 输入文件或目录无读权限 |
| `not_a_pdf` | 1 | 输入文件不是 `.pdf` |
| `import_failed` | 1 | `pdf2image` 或 `Pillow` 未安装 |
| `poppler_missing` | 1 | 系统 poppler 未安装或不可用 |
| `conversion_failed` | 1 | 转换失败（PDF 损坏 / poppler 异常以外） |
| `timeout` | 1 | 转换超时（单 PDF 超过 `--timeout` 秒） |
| `output_dir_create_failed` | 1 | 无法创建输出目录 |
| `save_failed` | 1 | 保存图片失败（已自动回滚） |
| `invalid_output_format` | 1 | 输出格式不在 `png` / `jpg` 之中 |
| `invalid_dpi` | 1 | DPI 不是正整数 |

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 全部成功 |
| `1` | 任意失败状态（除 `partial` 外的所有失败，以及 `missing_input` / `not_a_pdf` / `empty_input_dir` / `import_failed` / `poppler_missing` / `conversion_failed` / `timeout` / `save_failed` / `output_dir_create_failed` / `permission_denied` / `invalid_output_format` / `invalid_dpi`） |
| `2` | `partial`（部分成功部分失败） |

## 输出文件命名规则

- 每 PDF 一个同名子目录。
- 命名格式：`image-{编号}.{扩展名}`，编号固定宽度零填充，宽度按总页数动态决定（<10 页宽度 1，<100 页宽度 2，以此类推）。
- 同名子目录会被复用，文件覆盖写入。
- 中途保存失败会回滚已写入的图片与空子目录，避免半成品污染。
- 0 页 PDF 不创建同名子目录（视为成功跳过）。

## 自检

最小自检脚本，验证四类典型输入：

```bash
conda run -n agent-skills python scripts/_smoke.py
```

## 注意事项

- 高 DPI 提升质量但增加文件体积和转换时间。
- 大量 PDF 批量转换时，逐个处理以便汇总进度。
- 默认按 `MediaBox` 渲染；只在用户显式传 `--cropbox` 时才按 `CropBox` 裁切。
