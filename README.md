# 招标文件多智能体系统

解析中文招标文件，抽取结构化要点并写入向量库，为后续多 Agent 标书编写提供输入底座。

已实现**文档摄取 + 异步 API + 多 Agent 投标文件编写 + 桌面工作台**：解析 →
结构化抽取 → 向量入库，基于 LangGraph 的 Planner / Writer / Reviewer / Reviser
编写流程，以及 Electron + React 操作界面。HTTP 接口通过 Celery + Redis 异步执行，统一用 `GET /api/jobs/{job_id}` 轮询。

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
  cli/                   # doctor / parse / extract / ingest / inspect
```

## 环境准备

```bash
uv sync
cp .env.example .env      # 填入 DEEPSEEK(火山方舟) / EMBEDDING / MINERU 三个 API Key
```

若 `uv sync` 报 `failed to remove directory ... 拒绝访问 (os error 5)`，
原因是 `.venv` 内的目录带了 Windows 只读属性（不是进程占用），执行
`cmd /c "attrib -R -S -H /S /D .venv\*"` 后重新 `uv sync` 即可。

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
- **DeepSeek 是推理模型**：`deepseek-flash` 默认开启思考，思维链与正文共享
  `max_tokens`，预算耗尽时会返回空内容。抽取属于机械任务，`.env` 中
  `LLM_THINKING=disabled` 关闭思考后，评分表章节从 59s 降到约 15s；
  同时 `LLM_MAX_TOKENS=32768` 给足输出预算。
- **抽取按章节 map-reduce**：单章超长时递归细分，输出被截断时二分重试；
  每个单元的结果缓存在 `extraction_parts/`，中断后可续跑。

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
| GET | `/api/documents` | 已解析文档列表（读取 `upload/parsed`） |
| DELETE | `/api/documents/{doc_id}` | 删除解析结果与上传原件；不存在返回 **404** |
| GET | `/api/bids` | 历史投标产物列表（读取 `upload/bids/*/report.json`） |
| GET | `/api/bids/{bid_id}` | 单份产物摘要（不存在返回 **404**） |
| DELETE | `/api/bids/{bid_id}` | 删除标书产物目录（不动原始招标文件）；不存在返回 **404** |
| GET | `/api/bids/{bid_id}/sections` | 章节列表：顺序、锁定状态、字数、占位符数与评审分数 |
| GET | `/api/bids/{bid_id}/sections/{section_id}` | 单章详情（含 Markdown 正文，供编辑回填） |
| PUT | `/api/bids/{bid_id}/sections/{section_id}` | 保存单章正文与/或锁定状态，原地刷新整套产物，返回最新摘要 |
| GET | `/api/bids/{bid_id}/sections/{section_id}/file` | 导出单章 Markdown（附件下载） |
| POST | `/api/bids/{bid_id}/sections/rewrite` | 重写指定章节（`section_ids` 为空表示全部未锁定章节），返回 **202** |
| GET | `/api/documents/{doc_id}/markdown` | 预览解析后的 `content.md` |
| GET | `/api/bids/{bid_id}/files/{name}` | 预览/下载 `bid.md`、`bid.docx`、`report.json`、`outline.json`；`download=1` 触发附件下载 |
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
未覆盖评分因素、占位符数量、缺失资料、各章节修订前后得分，以及 `bid.md`、
`bid.docx`、`report.json` 的路径。完整报告仍在 `report.json` 中。

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
  失败任务可按原参数重试；取消只是不再接收结果，不保证中断已发出的模型调用。
- `POST /api/jobs/{job_id}/retry` 从 `task_history` 还原参数：解析任务按
  `doc_id` 找回原件，编写任务还原 profile 与 `max_sections` 等参数；原件或抽取结果
  已被清理时返回 **404**。
- 删除接口只删除服务端副本：`DELETE /api/documents/{doc_id}` 同时清理解析结果与原件，
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

## 投标文件编写（多 Agent）

基于已解析并抽取的招标文件，用 LangGraph 编排 Planner → Writer → Reviewer → Reviser
多个 Agent，生成投标文件草稿（Markdown + Word）与评分覆盖自检报告。

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
bid.docx                Word 导出
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
评审得分低于阈值才触发修订，且只保留得分不下降的修订稿。

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
npm run dev        # 浏览器 http://127.0.0.1:5173（/api 由 Vite 代理到 8001）
npm run electron   # Electron 窗口，加载开发服务器
npm run build      # tsc --noEmit + vite build，产物在 frontend/dist
```

- 开发态使用相对 `/api`，依赖 Vite 代理；打包后的 `file://` 页面自动回退到
  `http://127.0.0.1:8001`，也可用构建期变量 `VITE_API_BASE` 覆盖。
- Electron 通过 preload 暴露 `openPath` / `showItemInFolder`，主进程只允许打开
  仓库 `upload/` 下的路径；可用 `BIDDING_UPLOAD_ROOT` 覆盖根目录。
- 构建后可用 `ELECTRON_LOAD_DIST=1 npm run electron` 验证 `file://` 加载与 IPC。
- 切换导航不会中断后台任务：解析/编写任务 ID 会写入 `localStorage`，
  页面刷新后可继续轮询（Celery 结果保留 24 小时）。
- 右上角可切换深色/浅色主题（默认深色），选择保存在 `localStorage`，
  刷新或重启后保持。

## 服务器部署（Docker + 单端口）

生产形态：FastAPI 挂载 `frontend/dist`，前端与 API 共用一个端口对外；Redis 跑在
Docker 容器里；API 与 worker 跑在 tmux 会话中，SSH 断开不受影响。以下以 Web 端口
`8200`、Redis 宿主端口 `6382`、部署目录 `~/group_2/bidding` 为例。

### 1. 本地打包前端

访问令牌在构建期注入，必须与服务器 `.env` 的 `API_TOKEN` 一致：

```powershell
cd frontend
$env:VITE_API_TOKEN='自定义复杂令牌'; npm run build
```

> 令牌会编译进前端 JS，防的是陌生脚本直接打 API，不是强保密手段；
> 高安全场景应改用反向代理 Basic Auth 或防火墙 IP 白名单。

### 2. 服务器准备

```bash
docker ps                                      # 报 permission denied 则走文末回退方案
ss -tln | grep -E ':(8200|6382)'               # 有输出说明端口被占，换端口
python3 -V                                     # 需 ≥3.11
curl -sI https://ark.cn-beijing.volces.com | head -1   # 必须能访问火山方舟

cd ~/group_2
git clone git@github.com:chenjson59-jpg/bid-writing-multi-agent.git bidding && cd bidding
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
uv sync
```

仓库为私有时需在服务器配置 SSH key 或改用 `https://<token>@github.com/...` 形式。
然后上传构建产物与已有数据（本地执行；后两者可选，省去重新解析的 LLM 费用）：

```powershell
scp -r frontend\dist user@IP:~/group_2/bidding/frontend/
scp -r upload chroma_db user@IP:~/group_2/bidding/
```

### 3. Redis 容器

```bash
docker run -d \
  --name bidding-redis \
  --restart unless-stopped \
  -p 127.0.0.1:6382:6379 \
  -v bidding-redis-data:/data \
  redis:7-alpine \
  redis-server --requirepass 'Redis密码' --appendonly yes

docker exec -it bidding-redis redis-cli -a 'Redis密码' PING   # 期望 PONG
```

只绑定 `127.0.0.1` 且必须设密码；数据存命名卷，容器重建不丢，服务器重启后自动拉起。

### 4. 配置 .env

```bash
cp .env.example .env && chmod 600 .env
```

关键项：

```env
API_TOKEN=自定义复杂令牌                          # 与第 1 步 VITE_API_TOKEN 一致；留空不校验
CELERY_BROKER_URL=redis://:Redis密码@127.0.0.1:6382/0
CELERY_RESULT_BACKEND=redis://:Redis密码@127.0.0.1:6382/1
CELERY_WORKER_POOL=prefork                      # Linux 可用 prefork；Windows 必须 solo
DATABASE_URL=                                   # 留空则不启用 PostgreSQL（任务历史不持久化）
```

### 5. 启动（tmux）

```bash
tmux new -s bidding
uv run uvicorn api.main:app --host 0.0.0.0 --port 8200
# Ctrl+B " 分屏后：
uv run celery -A src.celery_app worker --loglevel=info
# Ctrl+B d 脱离会话；tmux attach -t bidding 重新进入
```

### 6. 验证

```bash
curl localhost:8200/api/health        # {"status":"ok","broker_ok":true}
curl localhost:8200/api/documents     # 401（令牌生效）
curl -H 'X-API-Token: 自定义复杂令牌' localhost:8200/api/documents   # 200
curl -s localhost:8200/ | head -1     # <!doctype html>
```

浏览器访问 `http://服务器IP:8200`。打不开说明防火墙未放行 8200，可联系管理员开放，
或临时用 `ssh -L 8200:127.0.0.1:8200 user@IP` 建隧道后访问 `http://localhost:8200`。

### 7. 运维与回退

```bash
docker logs -f bidding-redis     # Redis 日志
docker restart bidding-redis     # 重启 Redis
tmux attach -t bidding           # 查看 API / worker
```

`docker ps` 无权限时改用用户态 Redis，`.env` 连接串不变：

```bash
conda install -c conda-forge redis -y
redis-server --port 6382 --bind 127.0.0.1 --requirepass 'Redis密码' \
  --daemonize yes --logdir ~/group_2 --logfile redis.log
```

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

持续集成见 `.github/workflows/ci.yml`：后端执行 `ruff check` 与 `pytest`，
前端执行 `npm ci` 与 `npm run build`（tsc + vite）。Playwright 浏览器冒烟暂未纳入。

## 当前限制

- 工程量清单、图纸等以矢量线框为主的章节，本地解析几乎取不到正文；
  需要 MinerU 的版面/表格能力，且抽取结果会标注为不可用。
- 目录页会被识别并跳过，不参与分块。
- 无书签 PDF 仍可解析与入库，但只能按页分块，无法恢复章节层级。
- 前端联调已完成；数据库持久化为可选增强（投标人资料与任务历史，`DATABASE_URL` 留空则降级）。
- 只有单一共享 `API_TOKEN`，无登录、无多用户与权限隔离；下载接口支持 `?token=`，会进访问日志。
- 取消任务只是不再接收结果，不保证中断已发出的模型调用；重写按章并发缺失，
  长产物批量重写仍是串行执行。
- 章节编辑是纯文本替换，没有富文本与版本回滚（每次保存都会覆盖 `sections/` 内正文）；
  Word 仍无封面、目录与页码。
- 投标文件生成为草稿：占位符必须补全，公司信息与报价必须人工核对；
  完整跑一遍会发起较多 LLM 调用，开发时可用 `--max-sections` 控制规模。

## 声明

本项目为个人学习与求职 Demo，不是任何公司的线上系统；未使用前公司代码、文档、
客户数据或内部模板。`examples/demo/` 中的招标文件、公司名、人员与金额均为代码生成
的虚构数据，不含真实招标文件原文。

