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
  vectorstore/           # 章节感知分块 + 智谱嵌入 + Chroma
  cli/                   # doctor / parse / extract / ingest / inspect
```

## 环境准备

```bash
uv sync
cp .env.example .env      # 填入 DEEPSEEK / ZHIPU / MINERU 三个 API Key
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

退出码：`0` 成功、`3` 配置错误、`4` 解析失败、`5` 抽取失败、`6` 入库失败、`130` 取消。

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
chroma_db/                   # 本地向量库
```

`doc_id` 是源文件内容的 sha256 前 16 位，同一文件重跑会复用目录。

## 设计要点

- **章节结构以 PDF 书签为准**。目标文档前 7 页是目录，正则识别标题会被目录行污染，
  字体元数据也大量损坏，书签是唯一可靠来源。
- **MinerU 单文件限 200 页**，超过时自动均衡分片；合并时用 `content_list.json`
  的 `page_idx` 加分片页偏移还原原始页码。
- **嵌入走 `zai-sdk`**。`openai` SDK 在未显式传参时会强制
  `encoding_format="base64"`，而智谱兼容端点不支持；`zai-sdk` 原样透传，
  代码里再显式指定 `"float"`。
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
| GET | `/api/jobs/{job_id}` | 查询任务状态、进度与结果（parse / bid 共用） |
| GET | `/api/documents` | 已解析文档列表（读取 `upload/parsed`） |
| GET | `/api/bids` | 历史投标产物列表（读取 `upload/bids/*/report.json`） |
| GET | `/api/documents/{doc_id}/markdown` | 预览解析后的 `content.md` |
| GET | `/api/bids/{bid_id}/files/{name}` | 预览/下载 `bid.md`、`bid.docx`、`report.json`、`outline.json`；`download=1` 触发附件下载 |
| GET | `/api/health` | 健康检查 |

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
`[1/3] 解析文档` 这类阶段信息。`kind` 区分任务类型（`parse` / `bid`）。
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

- profile 非法、参数越界或 `doc_id` 格式错误 → **422**。
- 队列深度达到 `API_MAX_PENDING_JOBS` → **429**；Redis 不可用 → **503**。
- 输出目录由服务端生成（`upload/bids/<doc_id>-<时间>/`），不接受客户端指定。
- 与解析任务共用 worker；新增任务后需**重启 worker** 才会注册 `bidding.write_bid`。

上传限制：仅 PDF（校验 `%PDF-` 魔数）、默认 200 MB；文件名会净化，落盘为
`upload/files/<doc_id>_<原文件名>`。

## 异步任务（Celery + Redis）

`TASK_BACKEND=celery`（默认）时，`POST /api/parse` 与 `POST /api/bids` 只负责
校验与入队，实际执行由独立 worker 完成；任务状态与进度存放在 Redis。队列中注册了
`bidding.parse_document` 与 `bidding.write_bid` 两个任务。

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
- `GET /api/health` 返回 `task_backend` 与 `broker_ok`，可用于判断队列是否就绪。

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
sections/*.md           每个章节的正文
bid.md                  合并后的完整投标文件
bid.docx                Word 导出
report.json             评分覆盖、章节得分、占位符、缺失资料
```

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
提示词路由、抽取缓存、章节分块、智谱嵌入包装、Chroma 幂等入库、CLI 退出码与编排顺序，
以及 API 任务分发、投标任务、文档库/标书历史列表与产物下载的路径校验。

## 当前限制

- 工程量清单、图纸等以矢量线框为主的章节，本地解析几乎取不到正文；
  需要 MinerU 的版面/表格能力，且抽取结果会标注为不可用。
- 目录页会被识别并跳过，不参与分块。
- 无书签 PDF 仍可解析与入库，但只能按页分块，无法恢复章节层级。
- 尚未实现数据库持久化与前端联调。
- 投标文件生成为草稿：占位符必须补全，公司信息与报价必须人工核对；
  完整跑一遍会发起较多 LLM 调用，开发时可用 `--max-sections` 控制规模。
