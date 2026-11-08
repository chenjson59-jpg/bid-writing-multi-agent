# 规格：部署材料完善（T-024）

> 状态：**已定稿并实施**。2026-11-08 用户确认第 3 节全部决策点按推荐方案执行：
> 方案 A（文档优先 + 仅 Redis compose，不写应用 Dockerfile）、ADR-014 落于
> `docs/tech-decisions.md`（不新建 `docs/adr/`）、抽出独立 `docs/deployment.md`、
> CI 仅做 `docker compose config` 校验、演示数据只核对不改写、D2–D10 同批修正。
> 本文全部结论以 main 分支 `6f83b1d` 的当前代码与跟踪文件为准，审计时间 2026-11-08；
> 实施结果与验证数据见本地 `工作日志.md` 对应条目。

## 1. 用户场景与业务目标

T-024 要求在「不追求生产级大而全」的前提下，让仓库部署材料与系统真实能力完全
一致、可验证、可复现。已知真实缺口：

1. README 部署章标题「Docker + 单端口」暗示应用整体容器化，实际只有 Redis 是容器；
2. Redis 只有手工 `docker run` 命令，仓库内无可版本化复现的部署制品；
3. 缺少独立部署形态决策记录（应用为何不容器化）；
4. 部署命令从未做过系统性漂移审计。

目标：诚实化部署表述、把 Redis 容器编排沉淀为仓库制品、补齐 ADR、消除审计发现的
文档漂移；不引入应用 Dockerfile，不新增 Redis 之外的外部服务，不产生依赖变更。

## 2. 现状审计结论（含证据）

### 2.1 README 服务器部署章（525–718 行）逐条核对

| README 声明 | 当前代码事实（文件:行） | 结论 |
|---|---|---|
| `uvicorn api.main:app` | `api/main.py` 末行 `app = create_app()`；`create_app()` 在 `frontend/dist` 存在时把 StaticFiles 挂到 `/`，单端口成立 | 一致 |
| `celery -A src.celery_app worker` | `src/celery_app.py` 定义 `celery_app`，`include=["src.tasks"]` | 一致 |
| 四个 Celery 任务名（README 379–384） | `src/tasks.py:103/142/186/226` 注册 `bidding.parse_document/write_bid/supplement_bid/rewrite_section` | 一致 |
| `/api/health` 示例 `{"status":"ok","broker_ok":true,"worker_ok":true}` | `api/schemas.py:9-15` 实际 6 字段：`status/version/task_backend/broker_ok/worker_ok/queue_depth`；`api/routes.py:65-77` | README 为手工排查节选，字段与类型均真实存在，不算错；完整 schema 漂移见 D2 |
| `.env` 关键键 11 项（API_TOKEN、CELERY×3、DATABASE_URL、LOG_DIR、DATA_RETENTION_DAYS、三个 KEY、DEEPSEEK_BASE_URL） | `.env.example` 全部存在且语义一致；`scripts/cleanup.py:35` 实读 `settings.data_retention_days` | 一致 |
| Web 端口 8001、Redis 宿主端口 6382 | `API_PORT` 默认即 8001；6382 是文档显式示例，章内连接串、`ss`、回退命令全文一致 | 一致 |
| `upload/`、`chroma_db/`、`logs/` 按仓库根锚定、不依赖 CWD | `src/config.py:12` `REPO_ROOT`，`:93-105` 两个 validator 锚定 chroma/log；`src/pipeline.py:30` `UPLOAD_DIR = REPO_ROOT / "upload"` | 一致 |
| `scripts/demo.ps1`、`check.ps1`、`cleanup.py` | 三文件均在 `scripts/`；demo.ps1 支持 `-Backend local`、端口占用跳过；check.ps1 五项自检、失败退出码 1 | 一致 |
| `python -m src.cli doctor --probe-embeddings` | `src/cli/` 是包（含 `__main__.py`，`python -m src.cli` 合法）；`doctor.py:13` 有 `--probe-embeddings`，输出实际嵌入维度 | 一致 |
| `X-API-Token` 头或 `?token=` 参数 | `api/main.py:51-57` 中间件实现，`/api/health` 与 OPTIONS 放行 | 一致 |
| 构建期 `VITE_API_BASE`/`VITE_API_TOKEN` | `frontend/src/api.ts:57,64` 实际读取；未配置时回退相对路径 | 一致 |
| Python ≥3.12，3.11 在 uv sync 失败 | `pyproject.toml` `requires-python = ">=3.12"` | 一致 |
| prefork 多子进程写同一 Chroma 索引可能触发租户错误，建议 `--concurrency=1` 或 solo | `src/vectorstore/ingest.py:55-62` 模块级 `PersistentClient` 进程内单例；`retrieval.py:4` 注释说明串行化规避租户错误 | 告警有代码依据，表述成立 |
| DEEPSEEK 默认走火山方舟、MINERU key 90 天有效 | `.env.example` `DEEPSEEK_BASE_URL` 指向 ark coding 端点、MinerU 注释写明 90 天 | 一致 |
| `admin@IP`、`~/group_2` 示例 | 文中 602–606 行显式声明为占位符并给替换规则 | 保持占位，不引入真实地址 |
| 私库 clone 三种取代码方式、tar 打包排除项、tmux 排障、docker daemon 代理排障、用户态 Redis 回退 | 均为通用运维命令与流程说明，不依赖仓库内缺失文件 | 知识有效，迁移时保留 |

结论：部署章**命令本身未发现失效或虚构项**，问题集中在标题级定位（D1）与制品缺失。

### 2.2 全仓 Docker 声称审计

- 全仓（排除 `.venv/node_modules/.git`）不存在 `Dockerfile*`、`docker-compose*`、
  `.dockerignore`；
- 全仓 grep `Dockerfile|docker-compose|docker compose|容器化|镜像构建` **0 命中**，
  不存在「无对应文件的 Docker 声称」；
- README 内 17 处 docker 提及全部围绕 Redis 容器（`docker run/logs/restart/ps/exec`），
  正文 527–529 行诚实写明「Redis 跑在 Docker 容器里；API 与 worker 跑在 tmux 会话中」；
- 唯一夸大是 525 行章节标题「服务器部署（Docker + 单端口）」（漂移 D1）；
- Redis 仅以 614–621 行手工 `docker run` 描述，命名卷 `bidding-redis-data`、
  `127.0.0.1:6382` 绑定、`--requirepass`、AOF 等参数只存在于文档，无可版本化制品。

### 2.3 演示数据声明与指标核对（examples/demo）

- 合成声明完备：`examples/demo/README.md` 第 1 节声明全部内容由
  `scripts/make_demo_tender.py` 虚构生成、可公开再分发；根 README「声明」节同步声明；
- `artifacts/sections/` 实测 9 个文件；`outline.json` 策略分布 3×`generate`、
  5×`llm_fill`、1×`placeholder`，与 README 一致；
- 9 个 md 原文去空白字符数 **4279**；占位符 `【…】` 去重 **15 个**（共出现 21 次）；
- `extraction.json` 实测 5 项评分因素（含「财务状况」）、8 项格式条目；
- 用生产函数 `src/bid_writer/assembly.py::build_report` 对预置 artifacts 做零副作用
  内存复核（不写 `upload/`），`summary()` 输出：`total_sections=9`、
  `total_words=4279`、`placeholder_count=15`、格式覆盖 **8/8**、评分覆盖 **4/5**、
  `uncovered_factors=["财务状况"]`——五项预期指标全部精确一致；
- 附带观察（不属漂移）：该 report `rule_error_count=2`（第 3/7 章合成文本含招标文件
  要求的金额，触发 QUOTE_FORBIDDEN 确定性规则），文档从未声称零规则错误，本任务不改。

### 2.4 敏感信息与占位符审计

- 对 README、docs、examples、`任务清单.md`、`工作日志.md` 扫描 IPv4 字面量：
  仅出现 `0.0.0.0`、`127.0.0.1`（外加 localhost 主机名），无公网 IP；
- 未发现 `sk-` 密钥、真实 token/口令赋值；跟踪文件中的相关内容均为历史审计结论、
  规则文本或 token 作用域描述（无原值）；
- `.gitignore` 已覆盖 `.env`、`upload/`、`chroma_db/`、`logs/`、两份本地台账与
  `examples/demo/录屏操作卡.md`；实测 `git check-ignore deploy/.env` 命中既有
  `.env` 规则（退出码 0），新增制品的真实环境文件天然不会入库。

### 2.5 审计附带发现的其他文档漂移（均为纯文档问题）

> 下表「漂移内容」列保留的是修正前的失效路径，属审计证据而非导航引用；
> 「修正方向」列给出的才是当前真实路径。链接审计脚本对本表左列的缺失告警按此说明豁免。

| 编号 | 位置 | 漂移内容 | 修正方向 |
|---|---|---|---|
| D1 | README.md:525 | 标题「Docker + 单端口」暗示应用容器化 | 改为诚实标题（如「服务器部署（单端口 + Redis 容器）」） |
| D2 | docs/api-contract.md:596-617 | §7.2 health 示例与字段表只有 4 字段，缺 `worker_ok`、`queue_depth` | 按 `HealthResponse` 补齐 6 字段与说明 |
| D3 | README.md:741-742 | CI 描述只列 ruff/pytest，实际 ci.yml 后端另有两个 eval runner 步骤 | 补写两个评测步骤 |
| D4 | README.md:43 | 目录结构注释 `cli/ # doctor/parse/extract/ingest/inspect` 漏 `write` | 补 write（`src/cli/__init__.py` 实注册 6 个子命令） |
| D5 | docs/tech-decisions.md:568 | 引用不存在的 `src/extraction/llm_client.py` | `DeepSeekModelCaller` 实际在 `src/extraction/extractor.py`，删除/改写该条 |
| D6 | docs/tech-decisions.md:768 | `src/index.css` 路径错误 | 改为 `frontend/src/index.css`（文件实测存在） |
| D7 | docs/architecture.md:2195 | `frontend/src/features/` 目录不存在 | 改为实际的 `frontend/src/pages/`（+`lib/`） |
| D8 | docs/architecture.md:2203 | `src/summaries.py` 不存在 | 覆盖/摘要逻辑在 `src/bid_writer/assembly.py`（同行已列），删除该虚构成份 |
| D9 | docs/algorithms.md:1825 | `tests/test_parsing.py` 不存在 | A5–A8 实际对应 `test_splitter.py`/`test_builder.py`/`test_local_parser.py` |
| D10 | docs/algorithms.md:1830 | `tests/test_celery.py`、`test_bid_api.py` 不存在 | A22 与任务接口实际由 `tests/test_api.py`、`tests/test_e2e.py` 覆盖（全 tests/ 无 celery 字样，测的是 dispatch 抽象） |

说明：T-017 规格中 `src/lib/...`、`tests/...` 是前端相对路径简写，实际文件在
`frontend/` 下存在；历史规格属时点记录，不在修正范围。

### 2.6 既有 ADR 与目录约定

- 现有 ADR-001～009、011～013 全部维护在 `docs/tech-decisions.md` 单文件内
  （010 为空号），`docs/adr/` 目录从未创建；
- AGENTS.md 第 2 节规定「架构决策记录放 `docs/adr/`，按需创建，不维护空目录」，
  属允许性约定；项目既有事实约定是 tech-decisions 单文件；
- 下一个可用编号为 **ADR-014**。ADR-012「不接 Nginx（第一期单机）」是本任务
  部署形态决策的直接上游约束。

## 3. 决策结论（已确认，按推荐方案执行）

> 以下五个决策点已于 2026-11-08 由用户确认全部采纳推荐方案；各子节保留备选与权衡
> 作为决策依据。

### 决策 1：总体路线

- **方案 A（推荐）：文档优先 + 仅 Redis 编排制品**。诚实改写部署表述、新增部署
  形态 ADR、新增只含 Redis 的 compose 制品，不写应用 Dockerfile。
  - 依据：符合「不追求生产级大而全」与 ADR-012；所有写入文档的能力均可在 CI 与
    本地验证；不触碰 Chroma 多进程写、双进程编排等未验证领域。
- 方案 B：完整应用容器化（多阶段 Dockerfile、compose 编排 api/worker/redis/命名卷、
  CI 增加镜像构建）。展示性更强，但需解决 uvicorn+celery 双进程、Chroma 持久化与
  并发租户风险、密钥注入、`.dockerignore`，且无 CI 实际构建验证即违反 AGENTS
  「只描述可验证能力」，复杂度与本任务定位不匹配。
- **子问题 1a：ADR 落点**。推荐按项目既有约定在 `docs/tech-decisions.md` 增补
  **ADR-014**（不新建 `docs/adr/`，避免同一仓两套 ADR 维护位置）；若你希望落地
  独立文件，则新建 `docs/adr/0014-deployment-shape.md` 并在 tech-decisions 留索引行。

### 决策 2：部署文档形态

- **推荐：抽出独立 `docs/deployment.md`，README 留摘要 + 链接**。现部署章近 200 行，
  抽出后 README 更聚焦快速开始，部署排障知识整体迁移不删减；README「文档导航」
  增加一行。
- 备选：保持 README 单源，只改标题与结构。知识不拆分，但 README 继续膨胀。

### 决策 3：Redis compose 的 CI 验证强度

- 推荐接受：GitHub ubuntu runner 自带 docker，backend job 增加
  `docker compose -f deploy/docker-compose.redis.yml config` 语法/变量插值校验步骤，
  不实际起容器（CI 不依赖镜像拉取与 Redis 运行时，校验的是制品可解析、可复现）。
- 本机已实测 docker 29.9.2 / compose v5.6.1，可同命令本地复核。

### 决策 4：演示数据章节范围

- 推荐：**只核对不改写**。2.3 已证明声明与五项指标全部精确一致，无事实漂移，
  扩大改写只会制造无意义 diff。

### 决策 5（审计新增）：D2–D10 漂移是否同批修正

- 推荐同批修正：全部是机械性文档修正，不改任何代码；且任务完成标准要求「README 与
  docs 中出现的每个路径、脚本、模块都要能在仓库找到」，分批会留下已知红灯。
- 备选：只做部署相关项（D1–D4），D5–D10 另立任务台账。

## 4. 改动清单（已定稿，实际交付与本节一致）

预期**零生产代码改动**（`src/`、`api/`、`tests/`、`frontend/`、`pyproject.toml`、
`uv.lock` 不动）：

1. 新增 `docs/specs/2026-11-08-t024-deployment-materials.md`（本文件，确认后回填
   决策结论与定稿状态）；
2. 新增 `docs/deployment.md`：整体迁移 README 525–718 行知识（保留全部排障内容，
   只重组不删知识），标题诚实化，新增 compose 使用方式，明确「compose 只编排
   Redis，应用仍按 tmux 形态运行」；
3. 改 `README.md`：部署章压缩为形态摘要 + 链接；文档导航加 deployment.md；
   顺带修正 D3、D4；
4. 新增 `deploy/docker-compose.redis.yml`（单服务 redis：`redis:7-alpine`、
   容器名 `bidding-redis`、`restart: unless-stopped`、`127.0.0.1:6382:6379`、
   命名卷 `bidding-redis-data`、AOF、密码取自 `${REDIS_PASSWORD:?…}`），与现手工
   `docker run` 参数一一对应；
5. 新增 `deploy/.env.example`：仅 `REDIS_PASSWORD=请替换为强密码` 占位说明；真实
   `deploy/.env` 被既有 `.gitignore` 的 `.env` 规则覆盖（已验证），不入库；
6. 改 `docs/tech-decisions.md`：增补 ADR-014（应用不容器化的理由：Chroma 进程内
   单例多进程写租户风险、upload/logs/chroma_db 本地卷锚定、uvicorn+celery 双进程、
   一期单机与 ADR-012 不接 Nginx）；顺带修正 D5、D6；
7. 改 `docs/api-contract.md`（D2）、`docs/architecture.md`（D7、D8）、
   `docs/algorithms.md`（D9、D10）；
8. 改 `.github/workflows/ci.yml`：backend job 增加 compose config 校验步骤。

## 5. 验证方式与验证计划

文档/制品类任务按 AGENTS 3.2 裁剪 TDD，每项改动的验证方式：

| 改动 | 验证方式 |
|---|---|
| deployment.md / README 改写 | 全仓路径引用审计脚本复跑，真实缺失为 0；人工逐条比对迁移前后知识点清单 |
| compose 制品 | 本机 `docker compose -f deploy/docker-compose.redis.yml --env-file deploy/.env config` 退出码 0；CI 同构步骤退出码 0（合并后复核） |
| deploy/.env.example | 确认只含占位；`git status` 证明真实 env 不入库、`git check-ignore` 命中 |
| ADR-014 与 D2–D10 | 逐条对照代码事实复核（行号、符号、文件名存在）；链接审计复跑 |
| 零生产代码改动 | `git diff --stat` 确认无 src/api/tests/frontend/pyproject/uv.lock；`git status` 确认 uv.lock 未被改写 |

整体验证（每条 uv 命令在同一条命令内前置官方源，防止本机清华索引重写 lock）：

```powershell
$env:UV_DEFAULT_INDEX='https://pypi.org/simple'; uv run ruff check api src tests scripts eval
$env:UV_DEFAULT_INDEX='https://pypi.org/simple'; uv run pytest -q
$env:UV_DEFAULT_INDEX='https://pypi.org/simple'; uv run python -m eval.harness.runner
$env:UV_DEFAULT_INDEX='https://pypi.org/simple'; uv run python -m eval.harness.writing_runner
docker compose -f deploy/docker-compose.redis.yml --env-file deploy/.env config
```

前端无改动，跳过 npm test/build 并在工作日志说明；新增/改写的 Markdown 保持
CRLF、UTF-8 无 BOM（与现仓库文档一致）。

## 6. 回滚方式

- 本任务只在 `codex/t024-deployment-materials` 分支提交，不合并不影响 main；
- 整体回滚即丢弃分支（需用户授权）或 revert 后续 PR；
- 不触碰 `chroma_db/`、`upload/`、`logs/` 运行时数据，无数据回滚需求；
- compose 制品若弃用，删除 `deploy/` 与 CI 步骤即可，系统运行不依赖它（它只是
  手工 docker run 的等价声明）。

## 7. 流程裁剪说明

按 AGENTS.md 3.2，本任务属文档与部署制品变更、无业务行为变化，裁剪 TDD；
裁剪前提是所有变更均有替代性验证（命令实跑、schema 对照、链接审计、CI 校验），
并在本文第 5 节逐项列明。若实施中发现需要改生产代码，立即停止并在本规格单列，
按 3.1 走完整规格 + TDD 流程。

## 8. 边界与不做事项

- 不写应用 Dockerfile、不做镜像构建、不引入 Nginx/k8s/MinIO 等二期内容；
- 不硬编码公网 IP、域名、令牌、真实服务器路径；服务器信息一律占位符；
- 不产生依赖变更，不修改 uv.lock / package-lock.json；
- 不自行开 PR、不合入、不删除任何分支；推送后停住等待用户开 PR；
- 不入库 `任务清单.md`、`工作日志.md`、`examples/demo/录屏操作卡.md` 与运行时目录。