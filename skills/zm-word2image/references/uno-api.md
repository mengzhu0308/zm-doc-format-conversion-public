# LibreOffice UNO API 与 AI Agent 集成

> 本文档是 `zm-word2image` skill 的可选知识沉淀。主 skill 文档请看 [`../SKILL.md`](../SKILL.md)。

## 概述

LibreOffice 支持以 UNO（Universal Network Objects）API 协议对外暴露自动化接口，使外部程序或 AI Agent 能够以编程方式打开、编辑、保存、转换 Office 文档。这一能力适合需要长期驻留服务、与多个 Agent 协同、或对文档做精细控制（如内容写入、宏运行、批量格式化）的场景。

## 启动 UNO 服务

把 LibreOffice 启动为长期驻留的 UNO API 服务器：

```bash
libreoffice --headless --accept="socket,host=127.0.0.1,port=2002;urp;"
```

参数说明：

- `--headless`：无 GUI 启动；
- `--accept="socket,host=127.0.0.1,port=2002;urp;"`：监听 127.0.0.1:2002，使用 URP 协议；
- 可选附加 `--norestore --nologo --nodefault` 等参数减少启动噪音。

## 客户端连接

外部程序可通过 UNO 协议连接 127.0.0.1:2002，常用绑定：

- **Python**：`uno` 模块（LibreOffice 自带 python）。
- **Java**：通过 `com.sun.star.*` 包。
- **C++/C#/.NET**：通过 UNO C++ 或 .NET 绑定。

典型 Python 示例（简化）：

```python
import uno
from com.sun.star.beans import PropertyValue

ctx = uno.getComponentContext()
resolver = ctx.ServiceManager.createInstanceWithContext(
    "com.sun.star.bridge.UnoUrlResolver", ctx
)
remote_ctx = resolver.resolve(
    "uno:socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext"
)
smgr = remote_ctx.ServiceManager
desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", remote_ctx)
# 此后即可通过 desktop.loadComponentFromURL(...) 打开/操作文档
```

## 当前 skill 的边界

`zm-word2image` 当前 `scripts/run.py` 走的是「CLI 短任务」模式——为每个 Word 文档启动一次 LibreOffice 进程（headless 转换模式）后立即退出，**不维护长连接**，**不暴露 UNO 客户端**。原因：

- 短任务模式对单文件/批量目录转换延迟可控；
- UNO 模式需要单独管理 LibreOffice 服务进程、监控崩溃/重启，并发复杂度更高；
- 当前用例（Word→图片）每次转换都是独立的，不需要跨文档状态共享。

**`--accept` 是 LibreOffice 自身的启动参数，不是当前 `scripts/run.py` 的 CLI 参数**。如需 UNO/Agent 集成，需要把 LibreOffice 作为外部服务单独启动，再让 Agent 通过 UNO 协议访问。

## 何时升级到 UNO 模式

当出现以下需求时，可考虑把 skill 升级为 UNO 客户端模式：

- 需要在文档中插入/修改文本、表格、图像后立即转图；
- 需要执行 Word 宏（VBA/Basic）；
- 需要把多个 Agent 的编辑操作合并到同一文档；
- 需要自定义字体加载、用户配置或加密文档处理。

升级前请先评估：

- 服务进程生命周期与崩溃恢复；
- 端口冲突与多实例隔离；
- 跨平台（Windows / macOS / Linux）的 LibreOffice 启动路径差异。

## 参考资源

- [LibreOffice 官方 UNO 文档](https://api.libreoffice.org/)
- [Python-UNO 桥接说明](https://wiki.openoffice.org/wiki/Python)
