# B.1 同学包合并说明（仅补充、不覆盖）

- 合并策略：`rsync -a --ignore-existing`，目标路径已存在则**保留你本地版本**。
- 已排除：`sandbox/`、`memory/`、`sona.egg-info/`、`__pycache__/`、`*.pyc`、`.git/`。
- 事后清理：删除误并入的 Windows 虚拟环境目录 `.venv/Lib`、`.venv/Scripts`（在 **大小写不敏感** 磁盘上可能与 macOS 的 `.venv/lib` 冲突，已导致本地 venv 需重装依赖）；删除临时 `_merge_sona_B1_staging`；合并嵌套的 `opinion_analysis_kb/opinion_analysis_kb/`。
- **venv 修复**：已在项目根执行 `.venv/bin/pip install -r requirements.txt` 恢复 `site-packages`（若你惯用 `uv`，也可再执行 `uv sync`）。
- 若你发现仍有不应入库的大文件，可删除后自行 `git checkout -- path`（在已跟踪文件上）。

日期：2026-05-07
