# 招标文件多智能体系统

解析中文招标文件，抽取结构化要点并写入向量库，为后续多角色标书编写工作流提供输入底座。

已实现**文档摄取 + 异步 API + 多角色投标文件编写工作流 + 桌面工作台**：解析 →
结构化抽取 → 向量入库，Planner / Writer / Reviewer / Reviser 固定角色协作的
Plan-and-Execute 风格编写流程（标准库线程池章节级并发），以及 Electron + React 操作界面。HTTP 接口通过 Celery + Redis 异步执行，统一用 `GET /api/jobs/{job_id}` 轮询。

> **关于「多智能体」的命名说明**：产品名沿用「多智能体系统」，但当前实现是
> **多角色 LLM 工作流（Plan-and-Execute 风格）**——Planner / Writer / Reviewer / Reviser
> 是职责固定的函数节点，按预定义流程协作，章节级并发由标准库 `ThreadPoolExecutor` 扇出，
> 不存在运行时自主选择工具或决定下一步的自主 Agent。这样命名与实现严格对应，避免概念夸大；
> 术语界定见 `AGENTS.md` 第 1 节，选型与修订过程见 `docs/tech-decisions.md` 的 ADR-004。
> 若未来出现条件分支、人工中断恢复等复杂编排需求，再评估 LangGraph 等图编排框架。

## 文档导航

| 文档 | 内容 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 需求与业务边界、模块划分与数据流、组件工作机制、开发流程与验证门禁 |
| [`docs/tech-decisions.md`](docs/tech-decisions.md) | 技术选型对比、架构决策记录（ADR）、技术栈与版本快照 |
| [`docs/api-contract.md`](docs/api-contract.md) | 接口规范、任务状态、数据规范、配置、安全与故障排查 |
| [`docs/algorithms.md`](docs/algorithms.md) | 解析/抽取/检索/编写核心算法、投标编写代码精读 |
| [`docs/code-guide.md`](docs/code-guide.md) | 代码阅读路径与提交前验证门禁 |
| [`docs/deployment.md`](docs/deployment.md) | 单机服务器部署：Redis 容器 compose、tmux 双进程、验证与排障 |
| [`docs/containerization-feasibility.md`](docs/containerization-feasibility.md) | 二期预研：应用容器化可行性（双进程拆分、Chroma 卷、CI 成本），非当前能力 |
| [`AGENTS.md`](AGENTS.md) | 工程协作规范、LLM/RAG 约束与完成定义 |

## 目录结构

```
src/
  config.py              # pydantic-settings 配置，路径锚定仓库根目录
  errors.py              # 异常层级与 CLI 退出码
  pipeline.py            # run_parse / run_extract / run_ingest
  parsing/               # PDF → Markdown + 章节树
    toc.py               #   从 PDF 书签读章节树（唯一可靠的结构来源）
    splitter.py          #   超过 MinerU 页数上限时按页分片
    mineru_client.py     #   MinerU 云端：上传 → 轮询 → 下载解压
    local_parser.py      #   PyMuPDF 兜底，按书签切分
    builder.py           #   两条路径统一成 ParsedDocument 并落盘
  extraction/            # 章节级 map-reduce 结构化抽取
  vectorstore/           # 章节感知分块 + 火山方舟嵌入 + Chroma
  cli/                   # doctor / parse / extract / ingest / inspect / write
```

## 环境准备

```bash
uv sync
cp .env.example .env      # 填入 DEEPSEEK(火山方舟) / EMBEDDING / MINERU 三个 API Key
```

若 `uv sync` 报 `failed to remove directory ... 拒绝访问 (os error 5)`，
原因是 `.venv` 内的目录带了 Windows 只读属性（不是进程占用），执行
`cmd /c "attrib -R -S -H /S /D .venv\*"` 后重新 `uv sync` 即可。

#### PyPI 镜像（国内网络可选）

仓库与 `uv.lock` 默认使用官方 PyPI：CI 运行在海外 runner，把区域镜像锁进仓库会导致
间歇性下载失败（T-027 修复记录见 `docs/specs/2026-11-07-t027-pypi-index-neutrality.md`）。
国内开发执行 `uv add`、`uv lock` 等重新解析操作时，可用本机级镜像加速，不要改仓库文件：

```powershell
# 方式一：当前会话临时生效
$env:UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
```

方式二是写入用户级配置 `%APPDATA%\uv\uv.toml`（对本机所有项目生效）：

```toml
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
```

`uv sync --frozen` 按 `uv.lock` 记录的制品 URL 与 sha256 哈希安装；镜像偏好请保持在
本机环境，不要重新提交进 `pyproject.toml`。

## 用法

所有命令从仓库根目录执行：

```bash
# 环境自检（含一次真实嵌入探测，用于确认维度与编码格式）
.venv/Scripts/python.exe -m src.cli doctor --probe-embeddings

# 仅解析
.venv/Scripts/python.exe -m src.cli parse "upload/files/xxx.pdf" --parser auto

# 仅结构化抽取（参数可以是 PDF 路径或已有 doc_id）
.venv/Scripts/python.exe -m src.cli extract 0c7dc9d6493c8e50

# 完整流水线：解析 + 抽取 + 入库
.venv/Scripts/python.exe -m src.cli ingest "upload/files/xxx.pdf"

# 检索自检
.venv/Scripts/python.exe -m src.cli inspect tender_document --query "评标办法" --limit 5
```

常用参数：

- `--parser auto|cloud|local`：`auto` 优先 MinerU，失败自动回退 PyMuPDF
- `--strict-parser`：云解析失败时直接报错，便于发现过期密钥
- `--force`：忽略已有产物重新执行
- `--no-extract` / `--no-ingest`：跳过对应阶段

退出码：`0` 成功、`3` 配置错误、`4` 解析失败、`5` 抽取失败、`6` 入库失败、`7` 编写失败、`130` 取消。

## 产物

```
upload/
  files/                     # 源 PDF
  parsed/<doc_id>/
    manifest.json            # 解析清单（解析器、页数、章节、告警）
    content.md               # 归一化后的全文
    extraction.json          # 结构化抽取结果
    raw/                     # MinerU 分片与解压原件（调试溯源）
  images/<doc_id>/           # 抽取的图片
  bids/<doc_id>-<时间>/       # 生成的投标产物（见下文「产物」）
chroma_db/                   # 本地向量库
```

`doc_id` 是源文件内容的 sha256 前 16 位，同一文件重跑会复用目录。

## 设计要点

- **章节结构以 PDF 书签为准**。目标文档前 7 页是目录，正则识别标题会被目录行污染，
  字体元数据也大量损坏，书签是唯一可靠来源。
- **MinerU 单文件限 200 页**，超过时自动均衡分片；合并时用 `content_list.json`
  的 `page_idx` 加分片页偏移还原原始页码。
- **嵌入走火山方舟**。用 `openai` SDK 直连火山方舟 OpenAI 兼容端点
  （`EMBEDDING_BASE_URL`），默认模型 `doubao-embedding-vision`（2048 维，可换
  `doubao-embedding-large`），显式传 `encoding_format="float"` 返回浮点向量。
- **入库幂等**：先 upsert 新块，再删除同一 `doc_id` 下不再存在的旧块；
  内容未变的块按 `content_sha256` 跳过重新嵌入。
- **删文档级联清向量**：删除文档时清理 Chroma 两个已知集合中该 `doc_id` 的全部片段，
  不保留孤儿向量；向量库故障时接口返回 503 且文件保留，可安全重试。
- **评审与规则检查分离**：LLM Reviewer 只给语义评分；引用越界、无检索却引用、
  片段 doc_id 不匹配、具体报价/让利等客观项由零模型调用的确定性规则检查器判定
  （`CITE_*`/`QUOTE_FORBIDDEN`/`FACT_MISMATCH` 规则码），结果独立写入 `report.json` 的
  `rule_checks`，不与语义评审混在一起。
- **三层覆盖漏斗**：评分覆盖区分计划覆盖（因素分配给章节）、正文覆盖（非占位章节
  正文实际响应）、证据覆盖（同 doc_id 片段 + 合法引用），三层结果与逐层缺口写入
  `report.json` 的 `evaluation_coverage` 与 `coverage_summary`，全部确定性可复算。
- **全局事实表与口径校验（T-029）**：写作前从抽取结果确定性构建事实表（工期、保证金、有效期、预算、项目名称与编号，见 `src/bid_writer/facts.py`），随产物输出 `facts.json`；正文与事实表口径冲突时以 `FACT_MISMATCH` 规则码标记，零模型调用、可离线复算，月/天与工作日/自然日等含业务假设的换算不做自动判定。
- **检索降级显式标记**：集合缺失、Embedding 失败或查询异常时检索器置
  `degraded=True` 并透传到 `report.json` 的 `retrieval_degraded`，前端结果页红色
  提示「本次生成无原文依据」；抽取阶段原文核实失败的字段进入
  `ungrounded_fields` 并在抽取校正页单独提示，不确定的内容不悄悄放过。
- **可信度信号成体系展示**：结果页呈现计划/正文/证据三层覆盖漏斗、确定性规则错误
  章节标记（hover 查看规则码与原因），与 LLM 语义评分分开。
- **DeepSeek 是推理模型**：`deepseek-flash` 默认开启思考，思维链与正文共享
  `max_tokens`，预算耗尽时会返回空内容。抽取属于机械任务，`.env` 中
  `LLM_THINKING=disabled` 关闭思考后，评分表章节从 59s 降到约 15s；
  同时 `LLM_MAX_TOKENS=32768` 给足输出预算。
- **外部调用重试分层**：文本 LLM 与嵌入复用 OpenAI SDK 内置传输重试，次数分别由 `LLM_MAX_RETRIES`（默认 2）与 `EMBEDDING_MAX_RETRIES`（默认 10）配置；MinerU 四个 httpx 调用由 `RetryPolicy` 统一加指数退避重试（`MINERU_MAX_RETRIES` 默认 3），仅 429/5xx/超时/连接错误可重试，4xx 鉴权错误与 2xx 业务错误立即失败，`MINERU_MAX_RETRIES=0` 可关闭；重试耗尽后异常语义与 auto 本地降级链路不变。
- **抽取按章节 map-reduce**：单章超长时递归细分，输出被截断时二分重试；
  每个单元的结果缓存在 `extraction_parts/`，中断后可续跑。

## 离线评测（Golden Data）

`eval/` 维护手工构造的合成中文招标样本（工程/货物/服务三类场景），每份包含招标
Markdown、脚本化假模型夹具与人工核定的期望抽取产物，全程离线、零网络依赖：

```powershell
uv run python -m eval.harness.runner             # 抽取链：精确匹配 + 字段级 P/R/F1 与未溯源字段率，失配退出码 1
uv run python -m eval.harness.writing_runner     # 编写链：三份样本 A/B 两臂冻结基线，失配退出码 1（T-022）
uv run pytest -q                                 # eval harness 自身的单元测试在 tests/ 内
```

样本刻意覆盖脏数据归一化（字符串分值、null、非法条目）、代码围栏/空响应重试、
未溯源字段（`ungrounded_fields`）等边界；新增样本与冻结流程见 `eval/README.md`。
抽取阶段字段级 Precision/Recall/F1 与未溯源字段率已在该底座上实现（T-021，口径见
`eval/README.md` 第 7 节）。编写阶段（T-022，第 8 节）以同目录冻结的抽取产物为输入，
用脚本化假模型与 Fake 检索驱动完整编写链，A/B 两臂分别对应 `max_revisions=0` 单轮直写
与 `max_revisions=1` 评审修订闭环，输出三层覆盖率、章节级引用有效率、确定性规则违规数、
无依据信号（非幻觉检测）、占位符 P/R/F1 与合成 token/调用数基线，并覆盖无检索、引用
越界、错 doc_id、报价禁令、检索降级等边界。真实模型效果评测不混入单元测试。

## HTTP API

```bash
uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8001
# 或
uv run python -m api
```

交互式文档：<http://127.0.0.1:8001/docs>

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/parse` | 上传 PDF，创建解析任务，立即返回 **202** |
| POST | `/api/bids` | 基于已有抽取结果创建投标文件编写任务，立即返回 **202** |
| POST | `/api/bids/{bid_id}/supplement` | 补写未覆盖的评分因素，生成新产物（原产物保留），立即返回 **202** |
| GET | `/api/jobs/{job_id}` | 查询任务状态、进度与结果（parse / bid 共用） |
| POST | `/api/jobs/{job_id}/cancel` | 取消未结束的任务（Celery `revoke`；local 后端丢弃结果）；已结束返回 **409** |
| POST | `/api/jobs/{job_id}/retry` | 仅失败任务可重试，参数取自任务历史，返回新 `job_id`（**202**） |
| GET | `/api/jobs/{job_id}/trace` | 任务轨迹：各阶段耗时、失败阶段与异常类型（无日志返回 **404**） |
| GET | `/api/metrics` | 运行指标：终态分布、任务类型分布、平均耗时、最近失败（近 500 个任务） |
| GET | `/api/documents` | 已解析文档列表（读取 `upload/parsed`），含 `extraction_reviewed` 核对状态 |
| GET | `/api/documents/{doc_id}/extraction` | 抽取结果视图：基础字段 + 评分因素 / 资格条件 / 格式条目三张表；无结果返回 **404** |
| PUT | `/api/documents/{doc_id}/extraction` | 保存人工校正并标记「已核对」（`reviewed_at`），其余抽取信息保留；非法条目 **422** |
| DELETE | `/api/documents/{doc_id}` | 级联删除向量片段、解析结果与上传原件；不存在 **404**、向量库故障 **503**（文件保留可重试） |
| GET | `/api/bids` | 历史投标产物列表（读取 `upload/bids/*/report.json`） |
| GET | `/api/bids/{bid_id}` | 单份产物摘要（不存在返回 **404**） |
| DELETE | `/api/bids/{bid_id}` | 删除标书产物目录（不动原始招标文件）；不存在返回 **404** |
| GET | `/api/bids/{bid_id}/sections` | 章节列表：顺序、锁定状态、字数、占位符数与评审分数 |
| GET | `/api/bids/{bid_id}/sections/{section_id}` | 单章详情（含 Markdown 正文，供编辑回填） |
| PUT | `/api/bids/{bid_id}/sections/{section_id}` | 保存单章正文与/或锁定状态，原地刷新整套产物，返回最新摘要 |
| GET | `/api/bids/{bid_id}/sections/{section_id}/file` | 导出单章 Markdown（附件下载） |
| POST | `/api/bids/{bid_id}/sections/rewrite` | 重写指定章节（`section_ids` 为空表示全部未锁定章节），返回 **202** |
| GET | `/api/documents/{doc_id}/markdown` | 预览解析后的 `content.md` |
| GET | `/api/bids/{bid_id}/files/{name}` | 预览/下载 `bid.md`、`bid.docx`、`report.json`、`outline.json`、`facts.json`；`download=1` 触发附件下载 |
| GET | `/api/tasks` | 任务历史（解析 + 编写，读数据库；库不可用返回空） |
| GET | `/api/profiles` | 投标人资料列表（读数据库） |
| POST | `/api/profiles` | 新建资料，公司名已存在时覆盖最近一条 |
| PUT | `/api/profiles/{id}` | 覆盖指定资料 |
| DELETE | `/api/profiles/{id}` | 删除资料；数据库不可用返回 **503** |
| GET | `/api/health` | 健康检查，返回 `task_backend` / `broker_ok` / `worker_ok` / `queue_depth` |

`/api/parse` 查询参数：`parser=auto|cloud|local`、`force`、`extract`、`ingest`
（后两者为 `true` 时继续抽取 / 入库）。

```bash
# 上传（返回 202 与 job_id）
curl -X POST "http://127.0.0.1:8001/api/parse?parser=auto&extract=true&ingest=true" \
     -F "file=@upload/files/6055594244948.pdf"

# 轮询
curl "http://127.0.0.1:8001/api/jobs/<job_id>"
```

任务状态为 `pending` → `running` → `succeeded` / `failed`；`progress` 会记录
`[1/3] 解析文档` 这类阶段信息。`kind` 区分任务类型（`parse` / `bid` / `supplement`）。
Celery 后端的任务状态存放在 Redis，重启 API 不丢失；`local` 后端的状态仅在内存中。

### 投标文件编写任务

`POST /api/bids` 的 `doc_id` 必须已有 `upload/parsed/<doc_id>/extraction.json`
（先调用 `/api/parse?extract=true`），否则返回 **404**。

抽取结果可在前端「文档库 → 抽取结果」中人工校正评分因素、格式条目与资格条件，
保存后写入 `extraction_meta.reviewed_at`；编写页对未核对文档会给出提示，
校正后的因素名称直接进入大纲映射与覆盖统计。

```bash
curl -X POST "http://127.0.0.1:8001/api/bids" \
  -H "Content-Type: application/json" \
  -d '{
    "doc_id": "0c7dc9d6493c8e50",
    "profile": {"company_name": "示例公司"},
    "max_revisions": 1,
    "min_score": 75,
    "max_sections": 0,
    "export_word": true
  }'
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `doc_id` | 必填 | 已解析并抽取的文档 ID |
| `profile` | `null` | 内联投标人资料，结构同 `examples/bidder_profile.example.json`；省略字段生成占位符 |
| `max_revisions` | 服务端默认 | 每章最多修订次数，`0`–`3` |
| `min_score` | 服务端默认 | 触发修订的评审分数阈值，`0`–`100` |
| `max_sections` | `0` | 限制章节数，`0` 表示全部（调试时可用于控制 LLM 成本） |
| `export_word` | `true` | 是否同时导出 `bid.docx` |

轮询响应的 `kind="bid"`，`bid` 字段给出摘要：章节数与总字数、格式 / 评分覆盖数、
未覆盖评分因素、占位符数量、缺失资料、各章节修订前后得分、规则错误数
（`rule_error_count`）、正文/证据层覆盖数、检索降级标志（`retrieval_degraded`），
以及 `bid.md`、`bid.docx`、
`report.json` 的路径。完整报告（含三层覆盖明细 `coverage_summary` 与每章节
确定性规则检查 `rule_checks`）仍在 `report.json` 中。

摘要还包含两类计量字段：

- `llm_usage`：本次运行的模型调用次数、输入 / 输出 token、耗时、单价与
  `estimated_cost`；`cost_estimated=false` 表示未配置单价（`.env` 中的
  `BID_WRITER_PRICE_INPUT_PER_1K` / `BID_WRITER_PRICE_OUTPUT_PER_1K`）。
- `supplement_of`：补写产物会记录原 `bid_id`，便于两份 `report.json` 对比覆盖率。

#### 补写未覆盖评分因素

```bash
curl -X POST "http://127.0.0.1:8001/api/bids/<bid_id>/supplement"
```

服务端读取原产物的 `outline.json` 与 `bid.md`，为 `uncovered_factors` 中每一项追加
「补充：<因素名>」章节，只生成新增章节后写出一份新产物（新 `bid_id`，原产物不动）。
全部因素已覆盖时返回 **409**，产物不存在返回 **404**。

- 补写默认使用 `generate` 策略，避免标题缺少关键词被降级为占位符。
- 产物结构必须与大纲章节数一致（数量不符时提示重新生成后再补写）。
- 新增任务后需**重启 worker** 才会注册 `bidding.supplement_bid`。

编写任务通用约束：

- profile 非法、参数越界或 `doc_id` 格式错误 → **422**。
- 队列深度达到 `API_MAX_PENDING_JOBS` → **429**；Redis 不可用 → **503**。
- 输出目录由服务端生成（`upload/bids/<doc_id>-<时间>/`），不接受客户端指定。
- 与解析任务共用 worker；新增任务后需**重启 worker** 才会注册 `bidding.write_bid`。

上传限制：仅 PDF（校验 `%PDF-` 魔数）、默认 200 MB；文件名会净化，落盘为
`upload/files/<doc_id>_<原文件名>`。

### 任务取消、重试与数据生命周期

- 任务页（「任务与产物」）合并展示**标书产物**与**任务记录**：运行中的任务可取消，
  失败任务可按原参数重试；取消只是不再接收结果，不保证中断已发出的模型调用。任务进入
  终态时列表自动刷新（事件驱动，无空转轮询，T-030），也可随时手动点「刷新」。
- `POST /api/jobs/{job_id}/retry` 从 `task_history` 还原参数：解析任务按
  `doc_id` 找回原件，编写任务还原 profile 与 `max_sections` 等参数；原件或抽取结果
  已被清理时返回 **404**。
- 删除接口只删除服务端副本：`DELETE /api/documents/{doc_id}` 先级联清理 Chroma
  向量片段，再删解析结果与原件（向量清理失败返回 503 且保留全部文件），
  `DELETE /api/bids/{bid_id}` 只删产物目录。
- 保留期由 `DATA_RETENTION_DAYS` 控制（默认 30 天，`0` 表示不自动清理），清理脚本：

```bash
uv run python scripts/cleanup.py            # 按保留期删除
uv run python scripts/cleanup.py --dry-run  # 只打印待删内容
uv run python scripts/cleanup.py --all      # 清空演示数据
```

数据库中的任务历史不会被脚本清理，只清理磁盘上的解析结果、产物与上传原件。

### 章节级编辑与锁定

产物生成后可按章返工，不必整篇重跑：

```bash
# 列出章节（含 locked、字数、占位符数）
curl "http://127.0.0.1:8001/api/bids/<bid_id>/sections"

# 改写单章正文（空 body 或非法章节返回 422）
curl -X PUT "http://127.0.0.1:8001/api/bids/<bid_id>/sections/section_3" \
     -H 'Content-Type: application/json' \
     -d '{"markdown": "## 施工组织设计\n\n……", "locked": true}'

# 重写：section_ids 为空数组/缺省表示全部未锁定章节
curl -X POST "http://127.0.0.1:8001/api/bids/<bid_id>/sections/rewrite" \
     -H 'Content-Type: application/json' -d '{"section_ids": ["section_3"]}'

# 导出单章
curl -OJ "http://127.0.0.1:8001/api/bids/<bid_id>/sections/section_3/file"
```

- 事实来源是 `sections/index.json`；保存或重写后统一重新合并 `bid.md`、
  导出 `bid.docx`、刷新 `report.json` 与 `outline.json`，不会出现多处不一致。
- **锁定**的章节在批量重写时被跳过，重写结果的 warnings 会写明「跳过 N 个锁定章节」；
  所选章节全部锁定时返回 **422**。
- 重写复用原产物的 `write_options`（修订次数、评分阈值、检索条数等），
  只对目标章节执行写作与评审，`llm_usage` 只统计本次调用。
- 重写是异步任务（`kind="rewrite"`），与补写共用 worker 与任务历史，
  失败可在任务页重试；新增任务后同样需**重启 worker**。
- 旧产物（没有 `sections/`）首次访问章节接口时会按 `bid.md` + `outline.json`
  反推补齐，历史产物因此也能编辑与锁定。

### 可观测性

每个任务执行时都会写一份结构化日志 `<LOG_DIR>/jobs/<job_id>.jsonl`（默认
`logs/jobs/`），事件为 `start` / `stage_start` / `stage_done` / `end`，字段包括
阶段名、耗时、终态、异常类型与消息。`job_id` 通过 ContextVar 贯穿解析、抽取、
入库与编写流水线，无需在各函数间显式传参。

```bash
# 任务轨迹：阶段耗时 + 失败点（status / stage / error / error_type / stages）
curl "http://127.0.0.1:8001/api/jobs/<job_id>/trace"

# 运行指标：终态分布、类型分布、平均耗时、最近 10 条失败
curl "http://127.0.0.1:8001/api/metrics"
```

- `GET /api/jobs/{job_id}` 也返回 `stage` 字段（当前/最后阶段），前端可直接显示。
- 失败任务用 `trace` 定位到具体阶段（如 `解析文档`、`规划章节`、`章节写作`），
  `error_type` 给出异常类名；`stages` 里是已完成阶段的毫秒耗时。
- 同名阶段连续上报只记一次；日志缺失（任务未执行或已清理）返回 **404**。
- 指标接口额外返回 `task_backend` / `broker_ok` / `worker_ok` / `queue_depth`，
  与 `/api/health` 一致，便于脚本一次取全。
- 日志目录由 `LOG_DIR` 控制（相对路径按仓库根解析），已加入 `.gitignore`。

## 异步任务（Celery + Redis）

`TASK_BACKEND=celery`（默认）时，`POST /api/parse` 与 `POST /api/bids` 只负责
校验与入队，实际执行由独立 worker 完成；任务状态与进度存放在 Redis。队列中注册了
`bidding.parse_document`、`bidding.write_bid`、`bidding.supplement_bid` 与
`bidding.rewrite_section` 四个任务。

```bash
# 1. 确认 Redis 在 127.0.0.1:6379 运行
# 2. 启动 worker（Windows 必须用 solo 池，不支持 prefork）
uv run celery -A src.celery_app worker --loglevel=info --pool=solo
# 3. 启动 API
uv run uvicorn api.main:app --host 127.0.0.1 --port 8001
```

| 键 | 默认 | 说明 |
|---|---|---|
| `TASK_BACKEND` | `celery` | `celery` 或 `local`（进程内线程池，无需 Redis） |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | 任务队列 |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | 结果与进度 |
| `CELERY_WORKER_POOL` | `solo` | Windows 不支持 prefork |
| `CELERY_RESULT_EXPIRES` | `86400` | 结果与任务标记的保留秒数 |

- API 与 worker 必须能访问同一份 `upload/files/`。
- worker 未启动时 POST 仍返回 202，任务保持 `pending`；worker 启动后自动执行。
- Redis 不可用时 POST / GET 返回 503，API 本身不受影响。
- `GET /api/health` 返回 `task_backend`、`broker_ok`（Redis 可连通）、`worker_ok`
  （Celery worker 在线探测，5 秒缓存）与 `queue_depth`，前端顶栏据此显示三个状态点。

### 一键启动与自检（Windows）

演示时不必手工开四个终端，两个脚本覆盖启动与检查：

```powershell
# Redis → worker → API → 前端，最后自动自检
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1

# 无 Redis 也能演示（进程内线程池，任务状态重启即丢）
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -Backend local

# 只做自检：接口 / 队列 / worker / 文档库 / 资料库
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

`demo.ps1` 为 worker、API、前端各开一个窗口，日志留在窗口内；端口已被占用时跳过并提示。
`check.ps1` 任一组件异常时打印对应的修复命令，并以退出码 1 结束。

### 演示数据集（无需付费 API）

`examples/demo/` 提供一套**合成、可公开**的招标数据（10 页 / 6 章 PDF + 投标人资料 + 预置产物）：

```powershell
uv run python scripts/install_demo_data.py
```

安装后文档库、标书历史、资料库都有可演示的数据，`report.json` 由 `build_report()`
依据预置章节正文真实计算，覆盖率与正文一致。详见 `examples/demo/README.md`
（含数据来源声明与 3 分钟演示流程）。

## 投标文件编写（多角色工作流）

基于已解析并抽取的招标文件，由 Planner → Writer → Reviewer → Reviser 固定角色
协作完成章节规划、写作、评审与修订，生成投标文件草稿（Markdown + Word）与评分覆盖自检报告。

```bash
# 1) 先确保该文档已解析并抽取
uv run python -m src.cli parse  "upload/files/xxx.pdf"
uv run python -m src.cli extract <doc_id>

# 2) 生成投标文件
uv run python -m src.cli write <doc_id> \
  --profile examples/bidder_profile.example.json \
  --max-revisions 1 --min-score 75
```

常用参数：`--max-sections N`（调试时限制章节数，0 为全部）、`--no-docx`、
`--output DIR`、`--json`。

产物（`upload/bids/<doc_id>-<时间>/`）：

```
outline.json            章节计划
sections/index.json     章节索引（顺序、标题、锁定状态、评审分数、生成计划）
sections/<序号>-<id>.md 单章正文（编辑、重写、锁定的操作对象）
bid.md                  合并后的完整投标文件
bid.docx                Word 导出（封面 + 自动目录 + 页眉页码）
report.json             评分覆盖、章节得分、占位符、缺失资料
```

章节正文按章落在 `sections/`，`bid.md` 由它们合并生成；编辑、重写或切换锁定后会
原地刷新 `bid.md`、`bid.docx`、`report.json`、`outline.json`，保证产物始终一致。
旧产物（没有 `sections/`）首次访问时会按 `bid.md` + `outline.json` 反推补齐。

**投标人资料**：`--profile` 指向 JSON（结构见 `examples/bidder_profile.example.json`）。
资料未提供的公司名称、人员、证书、业绩、财务等信息不会编造，统一生成
`【待填写：xxx】` 占位符；`report.json` 会列出全部占位符与缺失字段。

**流程**：Planner 依据评分办法与投标文件格式制定章节；各章节并行执行
检索（Chroma）→ 写作 → 评审 →（低分时）修订；最后合并并生成覆盖报告。
评审得分低于阈值才触发修订，修订次数受 `max_revisions`（0–3）上限约束，且只保留
得分不下降的修订稿；Reviser 与 Writer 输入对齐，携带章节计划、评分因素、检索证据、
招标摘要与投标人资料，无检索结果时禁止新增引用，避免越改越偏。

注意：这是**草稿**，公司信息必须人工核对补全；投标报价、工程量清单等报价决策内容
只生成结构化占位，不自动填写。

## 桌面前端

`frontend/` 是 Electron + Vite + React 19 的桌面工作台，覆盖完整操作流程：

1. **上传解析**：拖拽或选择 PDF，设置解析方式与「结构化抽取 / 向量入库 / 强制重解析」，
   展示上传百分比、任务阶段日志与解析摘要。
2. **文档库**：列出 `upload/parsed` 下的文档，支持搜索、Markdown 预览，
   可直接选中已抽取文档发起编写。
3. **编写标书**：常用投标人资料表单 + 可折叠的高级 JSON（类似业绩、人员、财务、备注），
   设置修订次数、评分阈值、章节数量与 Word 导出；提交前有成本确认。
4. **结果与历史**：展示格式/评分覆盖、章节得分、缺失资料、未覆盖因素与 warnings；
   可预览 Markdown、下载 md/docx/report，Electron 下可打开 Word 或所在文件夹。
5. **章节返工**：章节明细表支持编辑单章 Markdown、单独重写、锁定 / 解锁与单章导出，
   批量重写只作用于未锁定章节，完成后自动刷新整套产物。

```bash
# 先启动 Redis、Celery worker 与 FastAPI（见上文），再启动前端
cd frontend
npm install
npm run dev        # 浏览器 http://localhost:5173（/api 由 Vite 代理到 8001）
npm run electron   # Electron 窗口，加载开发服务器
npm run build      # tsc --noEmit + vite build，产物在 frontend/dist
```

- 开发态使用相对 `/api`，依赖 Vite 代理；代码中不内置任何公网地址，打包后的 `file://`
  页面必须在构建期通过 `VITE_API_BASE` 显式指定后端地址（本地联调可用
  `http://localhost:8001`，服务器部署用 `http://<服务器地址>:8001`）。桌面端启动脚本见
  仓库根目录 `启动标书桌面端.bat`，无需 SSH 隧道。
- Electron 通过 preload 暴露 `openPath` / `showItemInFolder`，主进程只允许打开
  仓库 `upload/` 下的路径；可用环境变量 `UPLOAD_ROOT` 覆盖根目录。
- HTTP 下载分支有可信 origin 白名单（T-019）：默认仅放行本机后端
  `http://localhost:8001` / `http://127.0.0.1:8001`，远程后端须在启动环境设置
  `ELECTRON_TRUSTED_ORIGINS`（逗号分隔，与构建期 `VITE_API_BASE` 同源），
  非可信地址在下载前直接拒绝；`contextIsolation=true`、`nodeIntegration=false` 保持不变。
- 构建后可用 `ELECTRON_LOAD_DIST=1 npm run electron` 验证 `file://` 加载与 IPC。
- 切换导航（tab）不会中断后台任务，任务进度视图可自动恢复（T-031）：活跃任务与当前
  选中任务由 App 组件统一持有并轮询，页面随 tab 卸载/重挂不丢失状态；底部任务栏
  「查看」可跳回对应任务并滚动定位。
- 任务进入终态后立即停止该任务的轮询，终态快照与结果视图（编写 ResultView、解析结果
  卡）在内存中持续保留，不再被定时器自动清空（T-030）；只有用户显式复位才回表单：
  编写成功点「再写一份」、解析成功点「再上传一个」、解析失败点「重新上传」，编写失败
  沿用「返回修改参数 / 重试（沿用原参数）」，复位后无需浏览器刷新即可再次提交。任务态
  为内存态，整页刷新（F5）仍会重置当前任务视图，但落盘产物可在「任务与产物」页查看；
  Celery 结果后端仍保留 24 小时。
- 支持多任务排队，提交按钮不会因任务运行而禁用（T-034）：同一页面可连续提交多个同类
  任务，新任务追加置顶并自动选中，旧任务不被覆盖、不丢轮询；后端单 worker 串行执行时
  先提交者显示「运行中」、排队者显示「排队中」。任务进行中点「返回表单（后台继续）」
  即可脱选当前任务回表单再次提交，任务在后台继续运行并可经底部任务栏「查看」切回。
- 底部任务栏渲染全部在途任务（解析/编写/补写/重写四类标签区分），头部汇总任务总数、
  运行中与排队数量；终态任务停止轮询但行内留存，只有点终态行的「关闭」才显式清理
  （T-034，运行中任务不提供关闭入口，后端取消仍在「任务与产物」页操作）。
- 补写与章节重写已纳入全局任务栏与 App 统一 1.5s 轮询（T-034），不再使用组件内 2s
  私有定时器：终态迁移点只触发一次数据刷新（补写刷新标书列表/侧栏计数，重写刷新章节
  与摘要），切 tab 卸载组件后回调自动撤销、无残留定时器，任务本身仍在全局跟踪。
- 「任务与产物」页在挂载时加载一次，并由 App 终态事件驱动自动刷新：任意任务进入
  succeeded/failed 后自动重新拉取标书产物与任务记录，无活跃任务时不产生轮询请求
  （T-030）；数据库不可用时任务记录仍保持返回空列表的既有降级行为。
- 界面采用单一深色主题（根元素静态 dark class 驱动 Tailwind 深色样式），不提供主题切换，主题偏好不写入本地存储。

## 服务器部署

单机部署形态：**应用本身不容器化**——FastAPI 挂载 `frontend/dist`，前端与 API 共用
单端口（示例 `8001`）对外；uvicorn 与 Celery worker 是宿主机上的两个进程，运行在
tmux 会话中。**只有 Redis 运行在 Docker 容器**，仓库内
`deploy/docker-compose.redis.yml` 提供可版本化的 compose 编排（与手工 `docker run`
等价，并保留无 Docker 的用户态回退）。第一期不接 Nginx（ADR-012），应用不容器化的
决策与权衡（Chroma 进程内单例的多进程写风险、本地卷锚定、双进程形态）见 ADR-014。

完整部署步骤（前端打包、服务器取代码、Redis compose、`.env`、tmux 启动、验证、
运维与排障）见独立文档 [`docs/deployment.md`](docs/deployment.md)；文中服务器地址、
账号、密码均为占位符，仓库不记录任何真实服务器信息。

## 测试

pytest 作为开发依赖安装（`[dependency-groups] dev`），`tests/` 下的用例全部离线运行，
不会调用真实 API，也不会写入 `upload/` 或 `chroma_db/`。

```bash
uv run pytest                       # 全量，约 10 秒
uv run pytest -q
uv run pytest tests/test_toc.py -v
uv run pytest -k "chunk or toc"
```

测试覆盖：书签树与归页、分片、本地解析、MinerU 结果合并、schema 容错、JSON 提取、
提示词路由、抽取缓存、章节分块、火山方舟嵌入包装、Chroma 幂等入库、CLI 退出码与编排顺序，
以及 API 任务分发、投标任务、文档库/标书历史列表与产物下载的路径校验、
章节落盘反推与章节级编辑/重写/锁定。

端到端用例 `tests/test_e2e.py` 用 fake 模型调用 + `local` 后端跑通
「上传 → 解析 → 抽取 → 编写 → 产物下载」，不依赖真实 API、Redis 与 PostgreSQL；
共享夹具在 `tests/conftest.py`，产物目录统一重定向到临时目录。

持续集成见 `.github/workflows/ci.yml`：后端执行 `ruff check`、`pytest`、抽取链与
编写链两个 Golden Data 离线评测 runner，以及 Redis compose 制品的 `docker compose config`
校验（仅语法与变量插值，不起容器）；前端执行 `npm ci` 与 `npm run build`（tsc + vite）。
Playwright 浏览器冒烟暂未纳入。

### 前端测试（Vitest + Testing Library）

前端测试在 `frontend/` 下运行，测试文件统一放 `frontend/tests/`，全程离线、不访问后端：

```bash
cd frontend
npm test            # vitest run，一次性跑纯函数与组件测试
npm run test:watch  # watch 模式
npm run build       # tsc --noEmit 类型检查（含 tests/）+ vite 构建
```

- 纯函数测试（`*.test.ts`，如 `task_state`/`api`/`bid_quality`）不依赖 React，
  与组件测试共用一套 Vitest 配置；只测 Node 能力（如 `node:fs` 读源码）的文件
  在首行加 `// @vitest-environment node` 显式指定 node 环境（见 `electron_trust.test.ts`）。
- 组件测试（`*.tsx`，范例 `tests/ui.test.tsx`）默认运行在 jsdom 环境：
  用 `render`/`screen` 渲染与查询，断言用 `@testing-library/jest-dom` matcher
  （`toBeInTheDocument`/`toBeDisabled` 等），交互用 `@testing-library/user-event`
  触发真实事件序列，不直接调用组件 props 回调；查询优先 `getByRole`。
- 测试沿用显式 `import { describe, it, expect } from 'vitest'` 风格（`globals: false`）；
  `tests/setup.ts` 统一注册 jest-dom matcher，并在每个用例后自动 cleanup 卸载组件。

jsdom 只提供 DOM API 子集、没有真实布局引擎，已知缺失能力与统一 mock
（集中在 `frontend/tests/setup.ts`，不要在各用例里重复定义）：

| 缺失 API | 处理方式 | 说明 |
|---|---|---|
| `Element.prototype.scrollIntoView` | 替换为空操作 mock | jsdom 仅输出 Not implemented 警告；`WritePage` 任务定位会调用，断言调用用 `vi.spyOn` |
| `IntersectionObserver` | 最小构造器桩（observe/unobserve/disconnect/takeRecords） | 懒加载、可见性观察场景 |
| `window.matchMedia` | 固定 `matches:false` 的 `MediaQueryList` 桩 | 响应式断点/暗色模式；需模拟断点变化时在用例内 `vi.spyOn` |

jsdom 不能验证真实像素渲染、CSS 布局、滚动落点与 Electron 主进程行为，这些仍以手动
验证为准；新增缺失 API 的 mock 统一补到 `tests/setup.ts` 并在本表登记。按 AGENTS.md
第 4.3 节，未设覆盖率门禁前不安装任何 `@vitest/coverage-*` 覆盖率工具，也不宣称覆盖率数值。

## 当前限制

- 工程量清单、图纸等以矢量线框为主的章节，本地解析几乎取不到正文；
  需要 MinerU 的版面/表格能力，且抽取结果会标注为不可用。
- 目录页会被识别并跳过，不参与分块。
- 无书签 PDF 仍可解析与入库，但只能按页分块，无法恢复章节层级。
- 前端联调已完成；数据库持久化为可选增强（投标人资料与任务历史，`DATABASE_URL` 留空则降级）。
- 只有单一共享 `API_TOKEN`，无登录、无多用户与权限隔离；下载接口支持 `?token=`，会进访问日志。
- 取消任务只是不再接收结果，不保证中断已发出的模型调用；重写按章并发缺失，
  长产物批量重写仍是串行执行。
- 章节编辑是纯文本替换，没有富文本与版本回滚（每次保存都会覆盖 `sections/` 内正文）。
- `bid.docx` 带封面、自动目录域、页眉与页码，但目录由 Word 打开时更新域生成
  （设置 `updateFields`，WPS / Word 均会提示更新）；Markdown 仍是无版式正文。
- 投标文件生成为草稿：占位符必须补全，公司信息与报价必须人工核对；
  完整跑一遍会发起较多 LLM 调用，开发时可用 `--max-sections` 控制规模。

## 声明

本项目为个人学习与求职 Demo，不是任何公司的线上系统；未使用前公司代码、文档、
客户数据或内部模板。`examples/demo/` 中的招标文件、公司名、人员与金额均为代码生成
的虚构数据，不含真实招标文件原文。

