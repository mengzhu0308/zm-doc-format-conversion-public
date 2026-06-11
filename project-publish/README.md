# 项目发布工作流（`project-publish/`）

`project-publish/` 是统一的项目发布入口，负责同一套 private / public 发布流程。两种发布都支持语义触发，区别只在目标 GitHub 仓库可见性分别为 `private` 和 `public`。

## 目录结构

```text
project-publish/
├── README.md
├── main.py
├── pack_release.py
├── release.yaml
├── publish_*.py
├── pack_release_*.py
├── release_confirmation.py
└── tests/
```

## 固定职责

- `project-publish/main.py` 是统一发布入口，按 `--repo-visibility private|public` 选择发布模式。
- `project-publish/pack_release.py` 是统一打包入口，两种模式都走整仓快照打包。
- `project-publish/release.yaml` 是发布快照过滤配置，控制全仓发布时的排除边界；除缓存与临时文件外，也会显式排除受控的本地目录（如 `.git/`、`.claude/`、`.codex/`），不会默认一刀切排除所有点前缀路径。
- `private` 与 `public` 发布都会生成微信动态文案，并统一按“最新版本在前”的倒序归档到 `project-publish/WeChat.md`。
- 发布会话产物写入 `.cache/project-publish/<scope>/<tag>/`，打包缓存写入 `.cache/pack-release/<scope>/<tag>/`。

## 语义触发

- “私有发布”“private 发布”“发布到私有仓库”等等价意图，统一映射到 `project-publish/` 的 `private` 模式。
- “公开发布”“public 发布”“发布到公开仓库”等等价意图，统一映射到 `project-publish/` 的 `public` 模式。
- 这些都是语义触发，不要求用户精确匹配固定字面。

## 命令入口

- 主入口：`python3 project-publish/main.py --repo-visibility private`
- 公开发布：`python3 project-publish/main.py --repo-visibility public`
- 单独打包：`python3 project-publish/pack_release.py --repo-visibility private`
- 单独打包：`python3 project-publish/pack_release.py --repo-visibility public`

以上入口默认都要求当前源码仓库位于本地 `main` 分支；`--dry-run` 与 `--pack-only` 也不例外。

## 共享规则

- 发布固定 5 步：提交并 push 源码仓库 → 在发布目标仓库创建并校验 tag → 在发布目标仓库创建 GitHub Release → 执行 `project-publish/pack_release.py` 打包上传 → 生成微信动态文案并按“最新版本在前”的倒序归档到 `project-publish/WeChat.md`。
- `project-publish/main.py` 与 `project-publish/pack_release.py` 都只允许从本地 `main` 执行；`--dry-run` 与 `--pack-only` 仍会先校验当前分支。
- 完整发布会继续要求本地 `main` 已对齐 `origin/main`；若仅落后则自动 fast-forward，若分叉或上游异常则直接停止。
- `private` 与 `public` 正式发布前，都会先打印本次将发布的资源清单与目录结构预览，再提示“是否发布（是/否）”。
- `public` 发布在 preflight 前会强制交互确认发布 license，当前固定为 `MIT`、`Apache-2.0`、`GPL-3.0` 三选一；该值会继续传递给 `pack_release.py` 的预览与结果输出。
- 两种发布都按整仓快照发布，不再区分 private 全量与 public 白名单两套打包模型；`public` 只是把目标仓库可见性设为 `public`。
- `public` 发布不依赖 `project-public-package/` 生成的 sibling 目录，也不会读取或校验该 sibling 目录。
- 未显式传入 `--target-repo-path` 时，`private` 默认目标 GitHub 仓库名取当前源码仓库目录名；`public` 默认目标 GitHub 仓库名取根级 `VERSION.yaml.project_info.name`。若目标仓库不存在，会自动创建对应可见性的同名仓库。
- 双仓发布时，若显式传入的本地目标仓库已经是独立 Git 仓库，则 preflight 会先要求它当前也位于 `main`；随后步骤 1 会在源码仓库处理完成后再次同步目标仓库，再提交并 push 目标仓库。
- 只有 Release Asset 上传成功，才算发布完成。

## 相关目录

- `project-public-package/` 只负责按显式点名生成 sibling public 目录，不参与正式发布。
- 发布流程共享规范见 `references/project-publish.md`。
