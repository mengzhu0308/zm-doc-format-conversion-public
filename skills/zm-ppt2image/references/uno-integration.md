# UNO API 与 AI Agent 集成

本 skill 的主流程是「PPT/PPTX → 中间 PDF → 拆页图片」，通过 `scripts/run.py` 一次性完成；本文件补充说明 LibreOffice UNO API 的高级集成场景，并明确与本 skill 的边界。

## 适用场景

适合用 UNO 集成的场景：

- Agent **直接生成或修改**幻灯片内容（写入文本、插入图示、调整样式）
- Agent **回写讲稿**（speaker notes）到现有 PPT
- 长期运行的 **演示文稿自动化流水线**（同时编辑多份 PPT、共用一个 LibreOffice 实例）
- 把渲染结果**直接喂给下游 LLM** 做内容提取

不适合用 UNO 集成的场景：

- 只是把 PPT 拆成图片 → 用 `scripts/run.py` 即可，不要引入 UNO 复杂度
- 单次转换 / 一次性批量 → 用 `scripts/run.py --path <dir> --workers N`

## 启动 LibreOffice UNO 服务器

`--accept` 是 LibreOffice 进程参数，不是 `scripts/run.py` 的 CLI 参数。需要把 LibreOffice 作为独立外部服务启动：

```bash
# Linux / macOS
libreoffice --headless --accept="socket,host=127.0.0.1,port=2002;urp;" \
  --norestore --nologo --nodefault &
# Windows（PowerShell）
& "C:\Program Files\LibreOffice\program\soffice.exe" --headless `
  --accept="socket,host=127.0.0.1,port=2002;urp;" `
  --norestore --nologo --nodefault
```

> 端口冲突时换成 `port=2003` 等空闲端口；`host=127.0.0.1` 限定本地访问，跨机访问需要改 `host=0.0.0.0` 并加防火墙规则。

## Python `uno` 库连接示例

`uno` 库通常随 LibreOffice 自带（`<libreoffice>/program/python`），也可以通过 pip 安装 `python-uno`：

```python
import uno
from com.sun.star.beans import PropertyValue

def make_prop(name, value):
    p = PropertyValue()
    p.Name = name
    p.Value = value
    return p

# 1. 连接到已启动的 UNO 服务
local_ctx = uno.getComponentContext()
resolver = local_ctx.ServiceManager.createInstanceWithContext(
    "com.sun.star.bridge.UnoUrlResolver", local_ctx
)
ctx = resolver.resolve(
    "uno:socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext"
)
smgr = ctx.ServiceManager
desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

# 2. 打开已有 PPT
url = "file:///home/user/demo.pptx"
props = (make_prop("Hidden", True),)
doc = desktop.loadComponentFromURL(url, "_blank", 0, props)

# 3. 读取 slide 数量
slides = doc.DrawPages
print(f"slide count: {slides.Count}")

# 4. 导出为 PDF（用本 skill 流程替代：先转 PDF 再拆图更通用）
# pdf_props = (make_prop("FilterName", "writer_pdf_Export"),)
# doc.storeToURL("file:///tmp/demo.pdf", pdf_props)

doc.close(True)
```

## 与本 skill 的边界

> **重要边界声明**：本 skill **不含** UNO 客户端脚本，也不把 `--accept` 透传给 `scripts/run.py`；下表仅说明 LibreOffice UNO 集成的能力边界与推荐做法，不是本 skill 已提供的功能。若需做 UNO 集成，请将 LibreOffice 作为独立外部服务自行启动和管理，详见上文「启动 LibreOffice UNO 服务器」与「Python `uno` 库连接示例」。

| 需求 | 推荐做法 |
|------|----------|
| PPT → 拆页图片（一次性 / 批量） | `scripts/run.py --path ...`（**本 skill 主流程**）|
| Agent 修改 PPT 内容 / 写讲稿 | UNO 集成（启动 LibreOffice 服务 + Python `uno` 库，**非本 skill 范围**）|
| 长期演示文稿自动化（多 Agent 共用） | UNO 集成（一个 LibreOffice 实例，多连接，**非本 skill 范围**）|
| 仅做内容提取交给 LLM | 建议先用本 skill 拆图，再 OCR 或读 PDF 文本层 |

**禁止行为**：

- 不要把 `--accept` 加到 `scripts/run.py` 调用链里——本 skill 不会把 LibreOffice 启动为 UNO 服务器
- 不要把 `python3 -c "import uno; ..."` 嵌进 `scripts/run.py`——`uno` 不是本 skill 依赖
- 不要让 `scripts/run.py` 同时承担 PPT 内容编辑——这是 UNO 集成的工作

## 故障排查

- `uno` 模块找不到：使用 LibreOffice 自带的 Python（`<libreoffice>/program/python`），或 `apt install python3-uno`（Debian/Ubuntu）
- `connect: connection refused`：LibreOffice UNO 服务未启动或端口被占用；用 `lsof -i :2002` 检查
- `NoSuchElementException`：URL 协议头必须是 `file:///`，本地路径加 3 个斜杠（`/home/user/...` → `file:///home/user/...`）
- 跨平台路径：Linux/macOS 用 `/`，Windows 用 `\`；UNO URL 始终用正斜杠
