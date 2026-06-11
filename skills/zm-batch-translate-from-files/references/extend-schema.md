# `EXTEND.md` 键结构

采用简单 `key: value` 结构。项目内、XDG 和用户目录三处支持，优先级按 `SKILL.md` 顺序处理。

## 支持的键

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `target_language` | `zh-CN` | 默认目标语言 |
| `default_mode` | `normal` | `quick` / `normal` / `refined` |
| `audience` | `general` | 合法值：`general` / `technical` / `academic` / `children` / `business`；非法值走 warning + 回退默认 |
| `style` | `natural` | 合法值：`natural` / `formal` / `casual` / `literary`；非法值走 warning + 回退默认 |
| `chunk_threshold` | `4000` | 达到该词数后建议切块 |
| `chunk_max_words` | `5000` | 单块最大词数 |
| `default_output_dir` | 空 | 独立输出根目录 |
| `glossary_files` | 空 | 一个或多个术语表路径，逗号分隔；不存在的路径会作为 warning 输出 |
| `batches_dir` | 空 | 目录输入触发分组时，存放 `batch_*.json` 的目录；可被 `--batches-dir` 覆盖 |

## 示例

```md
target_language: zh-CN
default_mode: refined
audience: technical
style: formal
chunk_threshold: 3500
chunk_max_words: 4500
default_output_dir: /absolute/path/to/out
glossary_files: ./terms/en-zh.md, ./terms/product.md
```

## 解析规则

- 空行、标题行、代码围栏和注释行忽略。
- 数值键必须为正整数；非法值回退到默认值。
- `glossary_files` 的相对路径相对 `EXTEND.md` 所在目录解析。
- 未命中 `EXTEND.md` 时不报错，直接使用安全默认值。
