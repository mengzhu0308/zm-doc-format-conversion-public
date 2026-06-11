# 失败口径（`zm-translate-from-files`）

`scripts/main.py` / `scripts/chunk_markdown.py` 在预检、偏好解析与切块阶段可能返回下表所列的 `result_status=error` 错误码。每个错误码都伴随一个稳定的 `summary.<key>=1` 字段，方便消费方做断言或聚合统计。

## 错误码

| 错误码 | 含义 | 处理建议 |
| --- | --- | --- |
| `missing_input` | 输入路径不存在 | 确认输入路径存在、拼写正确、且不是符号链接 |
| `directory_input_not_supported` | 输入是目录 | 改用 `zm-batch-translate-from-files` 处理目录或清单批量翻译 |
| `unsupported_input` | 输入不是 `.md` / `.txt`，或无法读取 | 仅接受 `.md` / `.txt` 普通文件；检查文件类型与权限 |
| `unsafe_symlink_input` | 输入是符号链接 | 改用真实文件路径后再试 |
| `unsafe_output_path` | 输出目录或指定的输出根目录是符号链接，或被非目录路径占用 | 改用真实目录路径后再试 |
| `unsafe_output_ancestor` | 输出根目录的祖先路径中存在符号链接 | 为防止越界写入，改用真实路径后再试 |
| `invalid_target_language` | `--to` 或 `EXTEND.md` 的 `target_language` 为空、含空白、超长或含 BCP 47 非法字符 | 仅使用 ASCII 字母、数字与 `-` 组成的 BCP 47 标签 |
| `unsupported_encoding` | 输入不是合法 UTF-8 | 建议先 `iconv -f <src> -t utf-8` 重编码 |
| `permission_denied` | 输入不可读或输出不可写 | 检查文件系统权限 |
| `invalid_max_words` | `--max-words` 必须为正整数 | 传入正整数 |
| `chunk_failed` | 分块脚本无法读取文件，或无法写入目标 `chunks/` | 检查文件类型 / 权限 / 输出目录可写性 |

## 错误与 EXTEND.md 回退

`EXTEND.md` 解析时的常见写法与回退策略见 [`references/extend-schema.md`](extend-schema.md) 的「错误与回退」小节。`warnings` 数组（位于 `prefs` / `resolve` / `chunk` 输出顶层）会列出被忽略的键、被跳过的非文件 glossary 路径、字符编码错误等可恢复问题。
