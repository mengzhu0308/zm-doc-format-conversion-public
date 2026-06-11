# zm-doc-format-conversion-public

`zm-doc-format-conversion-public` 是一个面向 Codex CLI/Claude Code 的文档格式转换 Agent Skill 集合。装好之后，你可以按任务直接调用不同 skill 处理对应任务。

本文档默认由 `project-write-readme/` 自动生成和维护。

## 项目目标

- 帮你把 `zm-doc-format-conversion-public` 这组常用 Agent Skill 一次安装到本机，减少到处找零散配置的时间。
- 帮你按任务快速找到合适的 skill，而不是先翻一圈仓库结构和说明文档。
- 让你在不依赖 skill 级 README 的情况下，也能先跑通第一次使用。

## 安装与使用

如果你是第一次使用这套 skill，先安装到本机，再按任务挑一个最接近的 skill 开始用。若只是想把已经装过的一批 skill 清掉，也可以直接走卸载入口。

### 常用入口

- 先用 `project-install/main.py` 把这套 skill 安装到本机；这是第一次使用时最常见的入口。
- 如果你只是想清理本机运行态，直接用 `project-uninstall/main.py` 反向卸载就行。
- 安装完成后，直接看下面的 `Skills 用途一览` 和 `附录：各 Skill Prompt 示例`，就能开始试用。
- 如果你暂时拿不准该用哪个 skill，优先看“更适合你在什么时候用”这一列。

### 脚本运行环境

#### `project-*` 项目级脚本

- `project-*` 下的项目级脚本沿用统一优先级：`uv run python`（项目存在 `pyproject.toml` / `uv.lock` 时）> `python`（已激活 conda 环境）> `python3`。
- 这套入口规则只用于根级 `project-*` 自动化脚本，不直接外推到 skill 自身的脚本回退入口。
- 安装 skill 到本机时，统一写入 `~/.agent-skills/.zm/`，并把本轮选中的工具入口同步到顶层 `skills/` 目录。
- 卸载时沿用同一套目标规则：先清理工具顶层入口，再删除 `~/.agent-skills/.zm/` 中的实际 skill 目录。

### 安装到本机

```bash
# uv 环境
uv run python project-install/main.py
uv run python project-install/main.py --skill zm-batch-ocr2md --skill zm-batch-translate-from-files
uv run python project-install/main.py --pattern 'zm-batch-*'

# conda 环境
python project-install/main.py
python project-install/main.py --skill zm-batch-ocr2md --skill zm-batch-translate-from-files
python project-install/main.py --pattern 'zm-batch-*'

# 系统级
python3 project-install/main.py
python3 project-install/main.py --skill zm-batch-ocr2md --skill zm-batch-translate-from-files
python3 project-install/main.py --pattern 'zm-batch-*'

```

### 从本机卸载

```bash
# uv 环境
uv run python project-uninstall/main.py
uv run python project-uninstall/main.py --skill zm-batch-ocr2md --skill zm-batch-translate-from-files
uv run python project-uninstall/main.py --pattern 'zm-batch-*'

# conda 环境
python project-uninstall/main.py
python project-uninstall/main.py --skill zm-batch-ocr2md --skill zm-batch-translate-from-files
python project-uninstall/main.py --pattern 'zm-batch-*'

# 系统级
python3 project-uninstall/main.py
python3 project-uninstall/main.py --skill zm-batch-ocr2md --skill zm-batch-translate-from-files
python3 project-uninstall/main.py --pattern 'zm-batch-*'

```

## Skills 用途一览

这张表更关心“你遇到什么任务时该想到它”，而不是展示仓库内部文件怎么组织。

| Skill | 更适合你在什么时候用 | 能帮你做什么 | 分类 |
| --- | --- | --- | --- |
| zm-batch-ocr2md | 需要批量图片 OCR 转 Markdown 时。 | zm-batch-ocr2md 用于把当前层图片目录或 JSON/TXT manifest 中的多张本地图像 OCR 成 Markdown。 | 工作流 |
| zm-batch-translate-from-files | 需要批量翻译本地文档时。 | zm-batch-translate-from-files 用于批量翻译本地 Markdown 或 文本文件。 | 工作流 |
| zm-en2zh | 需要英译中时。 | 最常用场景：调用后翻译当前输入的英文文本。 | 工作流 |
| zm-extract-images | 需要从文档中提取图片时。 | zm-extract-images 从本地 PDF、Word（.docx）和 PowerPoint（.pptx）文档中提取内嵌图片资源，保存到源文件同目录下的 {原文件名}_assets/ 目录中，并在存在图片时同步生成 index.md 索引文件。 | 工作流 |
| zm-image-filter | 需要过滤重复图片时。 | zm-image-filter 基于感知哈希（dHash）过滤内容相同或高度相似的图片。 | 工作流 |
| zm-md2excel | 需要把 Markdown 表格转成 Excel 时。 | zm-md2excel 从本地 Markdown 文件中提取表格并保存为 xlsx 或 csv。 | 工作流 |
| zm-mds-merge | 需要合并多个 Markdown 时。 | zm-mds-merge 将本地多个 Markdown .md 文件按标题结构智能合并为一个统一的 Markdown 文件。 | 工作流 |
| zm-ocr2md | 需要把图片 OCR 转成 Markdown 时。 | zm-ocr2md 用于把单张本地图像 OCR 成 Markdown。 | 工作流 |
| zm-pdf2image | 需要把 PDF 转成图片时。 | zm-pdf2image 将本地 PDF 文件逐页转换为 PNG 或 JPG 图片，每页一张独立图片，输出到与 PDF 同名的子目录中。 | 工作流 |
| zm-ppt2image | 需要把 PPT 转成图片时。 | zm-ppt2image 将本地 PPT/PPTX 文件逐页转换为 PNG 或 JPG 图片。 | 工作流 |
| zm-translate-from-files | 需要翻译本地文档时。 | zm-translate-from-files 用于翻译单个本地 Markdown 或文本文件。 | 工作流 |
| zm-video2image | 需要从视频中提取图片时。 | zm-video2image 从本地视频文件中按间隔抽帧提取图片（PNG/JPG），支持单个视频或目录下批量转换。 | 工作流 |
| zm-word2image | 需要把 Word 转成图片时。 | zm-word2image 将本地 Word 文档（.doc / .docx）逐页转换为 PNG 或 JPG 图片。 | 工作流 |
| zm-zh2en | 需要中译英时。 | 最常用场景：调用 skill 后进入中译英会话模式，粘贴中文自动翻译。 | 工作流 |

## 推荐工作流

下面这些路径更像“第一次用的时候该怎么起手”，不是维护仓库时的内部流程。

- 当前仓库还没有命中预设的使用路径；你可以先从 `Skills 用途一览` 和附录里的起手 Prompt 开始。

## 附录：各 Skill Prompt 示例

下面这些示例尽量直接沿用各个 skill README 里的“用法”代码块；你可以直接复制，再按自己的任务改几个关键词。

### zm-batch-ocr2md

```
请使用 zm-batch-ocr2md skill 将图片目录批量 OCR 为 Markdown
输入：/path/to/pages/
输出：Markdown 文件（输出到 pages_ocr2md/ 目录）
另外，还有下列参数约束：
- OCR 模式：mcp（默认）
- 分组大小：10（默认）
```

### zm-batch-translate-from-files

```
请使用 zm-batch-translate-from-files skill 批量翻译本地目录中的 Markdown 和文本文件
输入：/absolute/path/to/docs
输出：每个源文件各自写到 {stem}-zh/translation.md
另外，还有下列参数约束：
- 目标语言：zh-CN（默认）
- 翻译模式：normal（默认）
```

### zm-en2zh

- 该 skill 的 README 里暂时没有抽到可直接复用的用法示例。

### zm-extract-images

```
请使用 zm-extract-images skill 从 PDF 文档中提取内嵌图片
输入：/path/demo.pdf（本地 PDF 文件路径）
输出：/path/demo_assets/ 目录中的图片文件 + index.md 索引
```

### zm-image-filter

```
请使用 zm-image-filter skill 过滤目录中的重复图片
输入：/path/imgs/
输出：自动推导（输入目录同级的 {dirname}_filter）
另外，还有下列参数约束：
- 阈值（threshold）：5（默认；汉明距离 ≤ 5 视为重复）
- 哈希尺寸（hash-size）：8（默认；生成 64 位 dHash）
- 预览模式（dry-run）：关闭
- JSON 输出（json）：关闭，使用人类可读报告
```

### zm-md2excel

```
请使用 zm-md2excel skill 提取并保存 Markdown 中的表格
输入：/path/report.md
输出：与源文件同目录的 report.xlsx
另外，还有下列参数约束：
- 输出格式（format）：xlsx（默认）
- 详细日志（verbose）：关闭
- JSON 输出（json）：关闭，使用人类可读报告
```

### zm-mds-merge

```
请使用 zm-mds-merge skill 将以下本地 Markdown 文件按标题结构智能合并为一个统一文档。
输入：/path/to/chapter1.md /path/to/chapter2.md /path/to/chapter3.md
输出：/path/to/merged_document.md
另外，还有下列参数约束：
- 分隔符：使用 "\n\n---\n\n" 作为文件间分隔线
- Frontmatter：合并所有文件的 YAML frontmatter，冲突时以第一个文件为准
```

### zm-ocr2md

```
请使用 zm-ocr2md skill 将这张图片 OCR 转为 Markdown
输入：/path/to/screenshot.png
输出：Markdown 文件（与源文件同目录）
另外，还有下列参数约束：
- OCR 模式：mcp（默认）
```

### zm-pdf2image

```
请使用 zm-pdf2image skill 将本地 PDF 转换为逐页图片
输入：/home/user/docs/demo.pdf（本地 PDF 文件路径）
输出：/home/user/docs/demo/image-1.png、/home/user/docs/demo/image-2.png ...
另外，还有下列参数约束：
- 输出格式：png
- DPI：300
```

### zm-ppt2image

```
请使用 zm-ppt2image skill 将本地 PPT 转换为逐页图片
输入：/home/user/demo.pptx（本地 PPT/PPTX 文件路径）
输出：/home/user/demo/image-1.png、/home/user/demo/image-2.png ...
另外，还有下列参数约束：
- 输出格式：png
- DPI：300
```

### zm-translate-from-files

```
请使用 zm-translate-from-files skill 翻译这篇文章
输入：/absolute/path/to/demo.md
输出：Markdown 文件（翻译结果写到 demo-zh_CN/translation.md）
另外，还有下列参数约束：
- 目标语言：zh-CN（默认）
- 翻译模式：normal（默认）
```

### zm-video2image

```
请使用 zm-video2image skill
输入：/path/demo.mp4（本地视频文件路径）
输出：/path/demo/frame-1.png、/path/demo/frame-2.png ...
另外，还有下列参数约束：
- 输出目录：不指定，默认写到源文件同目录的同名子目录
- 抽帧间隔（时间）：1 秒（默认，每秒一帧）
- 抽帧间隔（帧数）：未指定（默认由时间间隔决定）
- 预览模式：未指定（默认关闭）
```

### zm-word2image

```
请使用 zm-word2image skill 将本地 Word 文档转换为逐页图片
输入：/home/user/demo.docx（本地 Word 文件路径）
输出：/home/user/demo/image-1.png、/home/user/demo/image-2.png ...
另外，还有下列参数约束：
- DPI：300
```

### zm-zh2en

```
/zm-zh2en
输入：（在当前会话窗口中粘贴待翻译的中文文本）
输出：在当前会话窗口中输出英文译文
流程：分析 → 草稿 → 定稿（默认快速三轮）
```
