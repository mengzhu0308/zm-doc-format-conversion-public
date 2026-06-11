# Skill 卸载脚本（`project-uninstall/`）

将当前项目 `skills/` 中的全部或指定 skill，从本机 AI 工具安装位置卸载。它是 `project-install/` 的逆向操作，负责清理运行态，不修改源码仓库里的 skill 目录。

当前支持三种目标选择方式：

1. 全量卸载：不传目标参数时卸载全部合法 skill
2. 指定子集：重复传入 `--skill <name>`
3. 模式匹配：使用 `--pattern <glob>`

## 卸载目标选择与探测

脚本分两步决定卸载位置：

1. **先选工具**：先确定本轮要从哪些 AI 工具入口卸载。支持 `Claude Code`、`Codex`、`Gemini`、`Kimi`、`OpenCode`、`OpenClaw` 多选，交互式界面额外支持“全选”
2. **再探测目录**：检查被选中的工具目录是否存在

### 目标判定规则

卸载目标固定为用户目录下的 SSOT 目录：

- Linux / macOS：`~/.agent-skills/.zm/`
- Windows：`%USERPROFILE%\.agent-skills\.zm\`

脚本使用 Python `Path.home()` 和路径组件拼接，路径分隔符会按当前平台规范生成。

- 所有 skill 都先从 SSOT 目录删除
- 对**本轮勾选**的 AI 工具主入口顶层 `skills/` 目录，同步清理指向 SSOT 的符号链接
- `claude` 的主入口目录是 `~/.claude/skills/`，并额外清理 `~/.claude-official-accounts-provider/shared/skills/`
- `codex` 的主入口目录是 `~/.codex/skills/`，并额外清理 `~/.codex-accounts/shared/skills/`
- 若旧版本曾在工具 `.zm/` 隐藏目录下创建同名入口，本次卸载会同步清理这些旧入口
- 若某工具目录不存在，仅跳过该目录的链接清理，不影响 SSOT 中的实际删除

### 交互与非交互行为

- 传入 `--tool` 时，按给定工具集合执行
- 未传 `--tool` 时，脚本会先尝试使用当前 `stdin` 进入交互式选择
- 若当前 `stdin` 不是 TTY，但存在可用的 `/dev/tty` 控制终端，脚本会自动回退到 `/dev/tty` 继续交互
- 交互式输入支持 `0` / `all` / `全选` 直接全选 6 个工具
- 交互式直接回车时，默认选择 `Claude Code` 和 `Codex`
- 若当前环境既不是交互式 `stdin`，也无法访问 `/dev/tty`，脚本会直接报错，要求显式传入一个或多个 `--tool`

## 目标选择规则

- `skills/*` 下只要目录存在且包含 `SKILL.md`，就视为可卸载 skill
- `--skill` 可重复传入；重复名称会自动去重
- `--pattern` 按 skill 名称执行 glob 匹配，例如 `zm-humanizer-*`
- 不能同时混用重复 `--skill` 和 `--pattern`
- 两者都不传时视为全量卸载
- 指定目标为空时直接报错，并列出当前可卸载 skill

## 卸载过程

对每个目标 skill 执行以下步骤：

1. **定位安装位置**：固定为 `~/.agent-skills/.zm/<skill-name>/`
2. **先删符号链接**：先删除各 AI 工具顶层入口中指向 SSOT 的符号链接（包括额外链接目录）
3. **再删 SSOT 实体**：再删除 SSOT 中的实际 skill 目录
4. **跳过未安装项**：若目标位置不存在该 skill，则打印“未安装”并继续，不把它当作失败

补充规则：

- 卸载流程不会删除源码仓库里的 `skills/<skill-name>/`
- 不会为了卸载而自动创建新的 AI 工具目录
- 若某个工具目录下同名位置本来就是实体目录或文件，而不是由 SSOT 同步的符号链接，脚本不会误删它
- 若某个工具入口是安装流程在符号链接不可用时创建的受管目录副本，且目录内包含 `.zm-managed-entry` 标记，脚本会将其作为受管入口清理
- `--dry-run` 只预览将删除哪些目录和符号链接，不做实际改动
- **安全限制**：卸载目标路径必须在 home 目录下的受管 skill 运行态目录中；不符合该条件时脚本会直接报错拒绝操作

## 运行方式

**语义运行**：

```text
从系统卸载所有 skill
```

```text
只卸载 zm-init-skill-project 和 zm-write-skill-readme
```

```text
按 zm-humanizer-* 模式卸载一组 skill
```

**Python 命令运行**：

```bash
# uv 环境
uv run python project-uninstall/main.py --tool claude
uv run python project-uninstall/main.py --tool claude --skill zm-init-skill-project
uv run python project-uninstall/main.py --tool claude --tool codex --pattern 'zm-humanizer-*'

# conda 环境
python project-uninstall/main.py --tool codex
python project-uninstall/main.py --tool codex --skill zm-init-skill-project
python project-uninstall/main.py --tool codex --tool gemini --pattern 'zm-humanizer-*'

# 系统级
python3 project-uninstall/main.py --tool opencode
python3 project-uninstall/main.py --tool opencode --skill zm-init-skill-project
python3 project-uninstall/main.py --tool opencode --tool openclaw --pattern 'zm-humanizer-*'
```

## 命令行参数

| 参数 | 说明 |
| ---- | ---- |
| `--dry-run` | 预览模式：显示将执行的操作但不实际卸载 |
| `--verbose`, `-v` | 显示详细日志 |
| `--skill <name>` | 卸载指定 skill；可重复传入多个名称 |
| `--pattern <glob>` | 按 skill 名称模式卸载子集，例如 `zm-humanizer-*` |
| `--tool <name>` | 卸载目标工具；可重复传入。可选值：`claude`、`codex`、`gemini`、`kimi`、`opencode`、`openclaw` |

## 输出口径

- 若勾选的工具目录存在，会正常卸载或清理关联链接
- 若勾选的工具目录不存在，仅跳过该目录的链接清理
- 若交互终端在选择过程中被关闭或不可读，脚本会退出并提示改为显式传入 `--tool`

## 验证卸载

```bash
# 查看 SSOT 和工具入口是否已删除
ls ~/.agent-skills/.zm/
ls -l ~/.claude/skills/ ~/.codex/skills/
ls -l ~/.claude-official-accounts-provider/shared/skills/ ~/.codex-accounts/shared/skills/

# 只预览不删除
python3 project-uninstall/main.py --tool claude --skill zm-init-skill-project --dry-run
```
