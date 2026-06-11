# Skill 安装脚本（`project-install/`）

将项目 `skills/` 中的 skill 安装至本系统的统一位置。当前支持三种 skill 选择方式：

1. 全量安装：不传目标参数时安装全部合法 skill
2. 指定子集：重复传入 `--skill <name>`
3. 模式匹配：使用 `--pattern <glob>`

若要做反向清理，使用 `project-uninstall/`。

## 安装目标选择与探测

脚本分两步决定安装目标：

1. **先选工具**：先确定本轮要为哪些 AI 工具入口创建符号链接。支持 `Claude Code`、`Codex`、`Gemini`、`Kimi`、`OpenCode`、`OpenClaw` 多选，交互式界面额外支持“全选”
2. **再探测目录**：检查被选中的工具目录是否存在

### 目标判定规则

安装目标固定为用户目录下的 SSOT 目录：

- Linux / macOS：`~/.agent-skills/.zm/`
- Windows：`%USERPROFILE%\.agent-skills\.zm\`

脚本使用 Python `Path.home()` 和路径组件拼接，路径分隔符会按当前平台规范生成。

- 所有 skill 都先安装到这个 SSOT 目录
- 对**本轮勾选**的 AI 工具主入口顶层 `skills/` 目录，自动创建指向 SSOT 的符号链接，确保 Codex `$`、Claude Code `/` 等工具入口能扫描到
- 若工具目录不存在，会自动创建
- `claude` 的主入口目录是 `~/.claude/skills/`，并额外同步 `~/.claude-official-accounts-provider/shared/skills/`
- `codex` 的主入口目录是 `~/.codex/skills/`，并额外同步 `~/.codex-accounts/shared/skills/`

### 交互与非交互行为

- 传入 `--tool` 时，按给定工具集合执行
- 未传 `--tool` 时，脚本会先尝试使用当前 `stdin` 进入交互式选择
- 若当前 `stdin` 不是 TTY，但存在可用的 `/dev/tty` 控制终端，脚本会自动回退到 `/dev/tty` 继续交互
- 交互式输入支持 `0` / `all` / `全选` 直接全选 6 个工具
- 交互式直接回车时，默认选择 `Claude Code` 和 `Codex`
- 若当前环境既不是交互式 `stdin`，也无法访问 `/dev/tty`，脚本会直接报错，要求显式传入一个或多个 `--tool`

## 目标选择规则

- `skills/*` 下只要目录存在且包含 `SKILL.md`，就视为可安装 skill
- `--skill` 可重复传入；重复名称会自动去重
- `--pattern` 按 skill 名称执行 glob 匹配，例如 `zm-humanizer-*`
- 不能同时混用重复 `--skill` 和 `--pattern`
- 两者都不传时视为全量安装
- 指定目标为空时直接报错，并列出当前可选 skill

## 安装过程

对每个目标 skill 执行以下步骤：

1. **冲突检测**：检查 SSOT 与本轮工具入口中是否已有同名 skill（实体目录、实体文件或符号链接）
2. **清理旧版**：彻底删除旧版，包括 SSOT 中的同名实体目录/文件，以及各 AI 工具顶层入口中的同名入口
3. **精简复制**：仅复制安装态所需白名单文件与目录到 `~/.agent-skills/.zm/`
4. **同步工具入口**：在**本轮勾选**的 AI 工具顶层 `skills/` 目录中创建指向 SSOT 的符号链接（目录不存在时自动创建）

### 安装态白名单

| 类型 | 内容 | 说明 |
| ---- | ---- | ---- |
| 必需文件 | `SKILL.md` | 技能定义 |
| 必需文件 | `VERSION.yaml` | 版本元信息 |
| 可选目录 | `agents/` | Codex 等工具的展示元数据 |
| 可选目录 | `scripts/` | 可执行脚本 |
| 可选目录 | `references/` | 参考文档 |
| 可选目录 | `assets/` | 模板、图标等资源 |

`CHANGELOG.md`、`README.md`、测试文件、workspace 产物等**不复制**。

补充规则：

- `.gitkeep` 等占位文件不会进入安装态
- 只有包含实际文件的 `agents/`、`scripts/`、`references/`、`assets/` 才会被复制
- 仅为空目录或只含占位文件的可选目录会被跳过，不污染安装态
- 若某个工具顶层 `skills/` 目录下已存在同名实体目录、文件或旧符号链接，脚本会先删除该同名入口，再创建指向 SSOT 的符号链接
- 若当前平台或权限不允许创建符号链接，脚本会退回为带 `.zm-managed-entry` 标记的受管目录副本；后续卸载只清理符号链接或带该标记的受管副本
- **安全限制**：安装目标路径必须在 home 目录下的受管 skill 运行态目录中；不符合该条件时脚本会直接报错拒绝操作

## 运行方式

**语义运行**：

```text
安装所有 skill 到系统
```

```text
先让我选 Claude Code / Codex / Gemini / Kimi / OpenCode / OpenClaw，再安装这些 skill
```

```text
先默认选 Codex 和 Claude Code，再安装这些 skill
```

```text
先全选所有 AI 工具，再安装这些 skill
```

```text
只安装 zm-planning-with-files-zh 和 zm-write-skill-readme
```

```text
按 zm-humanizer-* 模式安装一组 skill
```

**Python 命令运行**：

```bash
# uv 环境
uv run python project-install/main.py --tool claude
uv run python project-install/main.py --tool claude --skill zm-mds-merge
uv run python project-install/main.py --tool claude --tool codex --skill zm-mds-merge --skill zm-pdf2image
uv run python project-install/main.py --tool claude --pattern 'zm-*2image'

# conda 环境
python project-install/main.py --tool codex
python project-install/main.py --tool codex --skill zm-mds-merge
python project-install/main.py --tool codex --tool gemini --skill zm-mds-merge --skill zm-pdf2image
python project-install/main.py --tool codex --pattern 'zm-*2image'

# 系统级
python3 project-install/main.py --tool opencode
python3 project-install/main.py --tool opencode --skill zm-mds-merge
python3 project-install/main.py --tool opencode --tool openclaw --skill zm-mds-merge --skill zm-pdf2image
python3 project-install/main.py --tool opencode --pattern 'zm-*2image'
```

## 命令行参数

| 参数 | 说明 |
| ---- | ---- |
| `--dry-run` | 预览模式：显示将执行的操作但不实际安装 |
| `--verbose`, `-v` | 显示详细日志 |
| `--skill <name>` | 安装指定 skill；可重复传入多个名称 |
| `--pattern <glob>` | 按 skill 名称模式安装子集，例如 `zm-humanizer-*` |
| `--tool <name>` | 安装目标工具；可重复传入。可选值：`claude`、`codex`、`gemini`、`kimi`、`opencode`、`openclaw` |

## 输出口径

- 若勾选的工具目录存在，会正常安装或同步链接
- 若勾选的工具目录不存在，会自动创建对应顶层 `skills/` 目录及其父目录
- 若交互终端在选择过程中被关闭或不可读，脚本会退出并提示改为显式传入 `--tool`

## 验证安装

```bash
# 查看 SSOT 目录
ls ~/.agent-skills/.zm/

# 查看主入口符号链接
ls -l ~/.claude/skills/
ls -l ~/.codex/skills/

# 查看额外兼容入口符号链接
ls -l ~/.claude-official-accounts-provider/shared/skills/
ls -l ~/.codex-accounts/shared/skills/

# 确认只包含白名单文件
ls -la ~/.agent-skills/.zm/<skill-name>/
```
