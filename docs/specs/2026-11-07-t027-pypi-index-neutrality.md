# 规格：PyPI 索引中立化与 CI 下载故障修复（T-027）

## 1. 用户场景与业务目标

PR #11（T-020）合并后，main 的 CI run #24 backend job 在 `uv sync --frozen`
阶段失败：`aiohttp==3.14.5` 从清华镜像下载连接失败，uv 内部重试 3 次共 44.5s 后
退出码 2。同一提交在 PR 阶段的 run #23 成功，此前 11 次运行也全部成功，属间歇性
网络故障而非代码缺陷。

根因：`pyproject.toml` 通过 `[[tool.uv.index]] default=true` 把清华 PyPI 镜像锁为
默认索引，`uv.lock` 中全部制品 URL 均为 `pypi.tuna.tsinghua.edu.cn` /
`files` 镜像路径。GitHub Actions runner 在海外，跨区域镜像连接不稳定，重跑只能
赌网络，故障会随机复发。

目标：仓库依赖配置回归官方 PyPI 中立，保证海外 CI 可稳定复现；国内本地开发保留
镜像加速手段，但镜像只存在于本机环境，不再进入仓库契约。

## 2. 输入、输出与副作用

- 输入：无运行时输入，属构建/CI 配置变更；
- 改动点：
  1. `pyproject.toml` 删除 `[[tool.uv.index]]` 清华镜像块；
  2. `uv.lock` 重新生成，registry 与制品 URL 主机切换为 `pypi.org` /
     `files.pythonhosted.org`，包集合、版本、哈希与尺寸保持不变；
  3. README 增补「国内网络的 PyPI 镜像（可选）」说明；
  4. `docs/tech-decisions.md` 依赖环境表的默认索引行同步修正；
- 副作用：无 HTTP API、数据库、文件产物、业务逻辑变化；不新增运行时依赖。

## 3. 正常路径、异常路径与边界条件

- 正常路径（CI）：无镜像环境变量时 `uv sync --frozen` 从锁内官方 URL 安装成功；
- 本地路径：本机保留 `UV_DEFAULT_INDEX` 清华镜像时，`uv sync --frozen` 同样成功
  （实测退出码 0），`uv lock`/`uv add` 等重新解析操作继续走镜像加速；
- 边界：镜像配置只允许通过环境变量或用户级 `%APPDATA%\uv\uv.toml` 存在，禁止再次
  提交进 `pyproject.toml`；锁内下载仍带 sha256 哈希校验，供应链完整性不变。

## 4. 安全与权限

不涉及密钥、鉴权与数据一致性。删除的是区域镜像偏好而非信任源切换：官方 PyPI 为
uv 默认上游，锁文件哈希校验机制不变。

## 5. 兼容性与验收标准

- 兼容性：Python 版本、依赖版本与锁哈希零变化，`.venv` 无需重建；
- 验收：
  1. 主机名归一化后新旧 `uv.lock` 内容逐字节一致（仅下载主机不同）；
  2. 清空 `UV_DEFAULT_INDEX` 后 `uv sync --frozen` 成功，保留该变量时同样成功；
  3. `uv run ruff check api src tests scripts eval` 通过；
  4. `uv run pytest -q` 312 passed；`python -m eval.harness.runner` 3/3 匹配；
  5. 合并后 main 的 backend CI 运行成功（合并后人工核对）。
- 回滚：直接 revert 本任务 PR，恢复旧 `pyproject.toml` 与 `uv.lock`。

## 6. 流程裁剪说明

本任务为构建配置修复，无新增可自动化测试的业务行为，按 AGENTS.md 3.2 裁剪 TDD，
验证方式为锁文件等价性比对、双环境 frozen 安装实验与全量离线测试，并以合并后
CI 结果作为最终确认。