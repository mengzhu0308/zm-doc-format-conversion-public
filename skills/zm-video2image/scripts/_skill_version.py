"""zm-video2image 版本号单源真相。

- 该值由 project-publish/publish_steps.py::sync_skill_version_files
  在发布 bump 流程中自动从 VERSION.yaml 同步；手动修改时务必同步。
- 安装态 fallback：若 _skill_version.py 与 VERSION.yaml 不一致，
  优先信任 _skill_version.py（避免运行时 yaml 依赖）。
"""

__version__ = "2.0.1"
