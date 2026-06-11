# `EXTEND.md` 键结构

采用简单 `key: value` 结构。项目内、XDG 和用户目录三处支持，优先级按 `SKILL.md` 顺序处理。

## 支持的键

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `target_language` | `zh-CN` | 默认目标语言，按 BCP 47 规范化（如 `zh-cn` → `zh-CN`、`zh_hans_cn` → `zh-Hans-CN`） |
| `default_mode` | `normal` | `quick` / `normal` / `refined` |
| `audience` | `general` | 译文面向谁 |
| `style` | `natural` | 风格偏好 |
| `chunk_threshold` | `4000` | 达到该词数后建议切块 |
| `chunk_max_words` | `5000` | 单块最大词数 |
| `default_output_dir` | 空 | 独立输出根目录 |
| `glossary_files` | 空 | 一个或多个术语表路径，逗号分隔；缺失或非文件的路径会被跳过并写 warning |

## 示例

```md
target_language: zh-CN
default_mode: refined
audience: `technical`
style: formal
chunk_threshold: 3500
chunk_max_words: 4500
default_output_dir: /absolute/path/to/out
glossary_files: ./terms/en-zh.md, ./terms/product.md
```

## 解析规则

- 空行、标题行、代码围栏和注释行忽略。
- 标量值中成对或单个的反引号 inline code 会被自动剥除，例如 ``audience: `technical` `` 解析为 `technical`，避免示例片段污染实际偏好。
- 数值键必须为正整数；非法值回退到默认值。
- `target_language` 会被规范化为 BCP 47 形式：主标签小写、4 字母脚本标签首字母大写（`Hans`/`Hant`/`Latn`）、2 字母地区标签大写（`CN`/`US`/`TW`），分隔符统一为 `-`。
- `glossary_files` 的相对路径相对 `EXTEND.md` 所在目录解析；不存在的路径或非普通文件会被跳过并把原因写入 `warnings`。
- 未命中 `EXTEND.md` 时不报错，直接使用安全默认值。

## 错误与回退

下面三类写法都会触发结构化警告或默认值回退，而不是整轮失败。常见坑：

| 写法 | 触发原因 | 实际回退 |
| --- | --- | --- |
| `target_language: zh CN`（带空格） | BCP 47 字符白名单拒绝 | 报错 `invalid_target_language`，不会落盘任何 chunk |
| `target_language: zh/CN`（含斜杠） | BCP 47 字符白名单拒绝 | 同上 |
| `chunk_max_words: 0` 或 `chunk_max_words: -1` | 数值键必须为正整数 | 退回默认 `5000`；CLI `--max-words 0` 会触发 `invalid_max_words` |
| `chunk_max_words: not-a-number` | 数值键解析失败 | 退回默认 `5000` |
| `glossary_files: ./missing.md`（不存在） | `parse_extend_file` 跳过非文件 | `warnings` 数组追加 `"glossary file './missing.md' not found, skipped"` |
| `glossary_files: ./link.md`（符号链接） | `parse_extend_file` 跳过非常规文件 | `warnings` 追加 `"glossary file './link.md' is not a regular file, skipped"` |
| `default_output_dir: ../../escaped`（祖先存在 symlink） | `validate_output_root` 拦截 | 报错 `unsafe_output_ancestor`，要求改用真实路径 |
| 整个 EXTEND.md 是非 UTF-8 编码 | 读取阶段 `UnicodeDecodeError` | 走安全默认值，并在 `warnings` 报告 codec 与位置 |

修复优先级：

1. 看 `main.py prefs --pretty` 输出里的 `warnings` 数组，确认哪些键被回退或忽略。
2. 对真正想覆盖的键，更新 `EXTEND.md` 并去掉误写的注释、代码围栏和示例。
3. 对不想覆盖的键，直接从 `EXTEND.md` 中删除该行；脚本会自动用默认值补齐。
