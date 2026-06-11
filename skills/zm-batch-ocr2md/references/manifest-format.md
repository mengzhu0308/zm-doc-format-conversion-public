# 清单文件输入格式

> `zm-batch-ocr2md` 支持将 `.json` 或 `.txt` 清单文件作为 `--input` 传入。本文档描述清单的格式细则、过滤规则、输出目录约定与失败状态码。

## 支持的清单格式

| 扩展名 | 顶层结构 | 解析方式 |
|---|---|---|
| `.json` | `{"absolute_paths": ["/abs/path1", "/abs/path2", ...]}` | `parse_manifest_json()` |
| `.txt`  | 每行一条绝对路径，`#` 开头为注释 | `parse_manifest_txt()` |

两种格式均会被自动过滤：

- 不存在的路径
- 路径指向目录而非文件
- 非支持的图像格式（仅保留 `.png` / `.jpg` / `.jpeg` / `.webp`）
- 空行（仅 `.txt`）
- 重复路径（保留首次出现顺序，后续重复项静默跳过）

过滤后保留原始顺序，路径全部失效时返回 `manifest_empty`。

## JSON 清单

- 顶层必须是 JSON object，且包含 `absolute_paths` 数组字段
- 数组元素必须为字符串
- 解析失败（缺字段、JSON 格式错误、类型不符）返回 `manifest_parse_failed`

```json
{
  "absolute_paths": [
    "/home/zm/imgs/page-001.png",
    "/home/zm/imgs/page-002.png",
    "/home/zm/other/dir/page-003.jpg"
  ]
}
```

## TXT 清单

- 每行一条绝对路径
- 空行与以 `#` 开头的注释行会被跳过
- 自动过滤不存在 / 非图像格式的路径
- 解析后无有效图像返回 `manifest_empty`

```text
# 批次示例：第 1 批 30 张跨目录整合
/home/zm/imgs/page-001.png
/home/zm/imgs/page-002.png
# 下面这行会被跳过（非图像格式）
/home/zm/notes.pdf
/home/zm/other/page-003.jpg
```

> 小贴士：`.txt` 后缀保留为清单输入后缀；TXT 清单中的 `.txt` 路径会作为非图像格式被跳过。

## 显式声明清单模式

脚本默认通过扩展名（`.json` / `.txt`）自动识别清单。扩展名非标准或被改成无扩展名时，加 `--manifest` 显式声明（非 `.txt` 时按 JSON 解析）：

```bash
SKILL_DIR="/absolute/path/to/zm-batch-ocr2md"
conda run -n ocr python "$SKILL_DIR/scripts/run.py" --input mylist --manifest --provider local
```

## 输出目录

- 默认在清单文件所在目录新建 `<清单文件名（无扩展名）>_ocr2md/`
  - 例：`/path/batch_1.json` → `/path/batch_1_ocr2md/`
  - 例：`/path/batch_1.txt`  → `/path/batch_1_ocr2md/`
- 所有 Markdown 扁平输出到该目录
- `--output-dir` 可覆盖

## 失败状态码

| 状态码 | 触发条件 |
|---|---|
| `manifest_parse_failed` | JSON 格式错误、缺少 `absolute_paths`、类型不符 |
| `manifest_empty` | 过滤后无有效图像路径（全部不存在或非图像格式） |
| `unsupported_format` | 清单文件本身不是 `.json` / `.txt`，且未传 `--manifest` 显式声明 |

## 分批建议

- 每个 manifest 的输出写到 `<manifest-stem>_ocr2md/`。
- 多会话并行时建议每个会话使用独立 manifest 文件名，避免输出目录冲突。
- 失败状态码与逐图落盘语义保证结果可检查、可重试、可合并。
