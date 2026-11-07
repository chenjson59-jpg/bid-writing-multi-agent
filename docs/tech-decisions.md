# 技术决策：选型对比、ADR 与技术栈

> 本文件由原《技术选型对比》《团队分工与ADR》《技术栈》于 2026-11-06 归并并修订而成。
> ADR 保留修订轨迹；版本类信息以 `uv.lock` / `frontend/package-lock.json` 为权威来源，本文档快照可能滞后。

## 目录

- 第一部分　技术选型对比（原《技术选型对比》）
- 第二部分　团队分工与架构决策记录 ADR（原《团队分工与ADR》）
- 第三部分　技术栈与版本快照（原《技术栈》）

## 第一部分　技术选型对比（原《技术选型对比》）

> 文档版本：1.0  
> 阶段：方案设计  
> 配套文档：`architecture.md`（业务目标）、`architecture.md`（模块与数据流）、`tech-decisions.md`（架构决策记录）、`tech-decisions.md`（已选方案与版本快照）。

---

### 1. 文档说明

#### 1.1 目的

对系统各关键决策点给出**至少两套候选方案**，对比优缺点并说明取舍，作为最终技术栈的依据。

#### 1.2 评估维度

| 维度 | 说明 |
|---|---|
| 功能匹配 | 是否满足业务需求 |
| 复杂度 | 引入/维护成本 |
| 生态与成熟度 | 社区、文档、稳定性 |
| 性能 | 长任务、并发、本地部署适配 |
| 团队技能 | 与现有技能栈契合度（Python / React / FastAPI） |
| 运维成本 | Windows 单机下的部署与排障 |

#### 1.3 结论标注

- ✅ **采用**：最终选定方案；
- ⭕ **备选**：可行但未采用；
- ❌ **不采用**：已排除。

---

### 2. 关键决策点对比

#### 2.1 前端形态（桌面 vs 浏览器）

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. Electron + React（✅ 采用）** | 可打开本地 Word、定位产物目录；与本地后端/文件系统协作；符合单机桌面定位 | 包体大；需管理主/渲染/预加载三进程与 IPC 安全 | 满足「桌面打开/定位」硬需求 |
| B. 纯浏览器 Web（⭕ 备选） | 免安装、跨平台、无 Electron 复杂度 | 无法打开本地文件/定位目录，只能下载；需要额外下载交互 | 桌面能力缺失，不满足需求 |
| C. Tauri（⭕ 备选） | 体积小、内存占用低 | Rust 栈，团队不熟；生态与插件成熟度低于 Electron | 团队技能不符 |

**取舍**：核心诉求是「桌面打开 Word / 定位文件夹」，纯 Web 无法满足；Electron 生态成熟、团队有 React 基础，故采用 A。

#### 2.2 前端框架

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. React + TypeScript（✅ 采用）** | 生态最广、类型安全、与 Vite 集成好 | 需手写状态管理（本项目规模小可接受） | 采用 |
| B. Vue 3 + TypeScript（⭕ 备选） | 上手快、模板直观 | 团队 React 更熟；生态插件差异 | 不采用 |

**取舍**：仅四个工作区、状态集中在单页，React + `useState` 足够，不引入额外状态库。

#### 2.3 后端 Web 框架

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. FastAPI + Pydantic（✅ 采用）** | 异步、类型校验、自动 OpenAPI、依赖注入；上传/下载/JSON 支持成熟 | 需要 ASGI 服务（Uvicorn） | 采用 |
| B. Flask（⭕ 备选） | 简单、学习成本低 | 异步弱、校验需额外库、文档需手写 | 不采用 |
| C. Django + DRF（⭕ 备选） | 内置 ORM/Admin | 偏重，本项目无需完整 Web 后台 | 不采用 |

**取舍**：需要异步接口 + 强类型模型 + 自动文档，FastAPI 最契合。

#### 2.4 任务队列与异步执行

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. Celery + Redis（✅ 采用）** | 成熟、支持状态/进度/结果、跨请求执行 | Redis 是额外依赖；Windows 只能 solo/threads | 采用（默认） |
| B. 进程内线程池 + 内存任务表（✅ 采用为 local 兜底） | 无外部依赖、测试友好 | 重启丢失、不支持跨进程 | 测试/无 Redis 环境 |
| C. RQ / Dramatiq（⭕ 备选） | 更轻 | 生态与进度/结果支持弱于 Celery | 不采用 |

**取舍**：解析/编写耗时数分钟，必须从请求中剥离；Celery 生态最成熟，配 `local` 兜底兼顾测试。

#### 2.5 Agent 编排

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. 标准库线程池自研编排（✅ 采用）** | 无额外依赖、扇出/并发/容错完全可控，与固定流程匹配 | 需自行实现扇出与结果汇总 | 采用 |
| B. LangGraph（⭕ 备选） | 状态图、条件边、运行时扇出能力完整 | 当前流程无自主决策与复杂分支，引入后收益低于学习与依赖成本 | 暂不采用 |
| C. CrewAI / AutoGen（⭕ 备选） | 高层多 Agent 抽象 | 黑盒、难精确控制章节循环与评分阈值 | 不采用 |

**取舍**：流程是「计划 → 分章节并行 → 汇总」，章节间无依赖且角色固定，`ThreadPoolExecutor` 扇出已满足需求；Agent 框架的图编排能力没有用武之地，保留为复杂编排场景下的演进选项。

#### 2.6 向量库

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. ChromaDB 嵌入式（✅ 采用）** | 单机持久化、无需独立服务、自定义嵌入函数 | 并发写需串行化；索引与进程绑定 | 采用 |
| B. FAISS（⭕ 备选） | 检索快、纯内存/磁盘 | 无元数据过滤友好性弱、需自行管理持久化 | 不采用 |
| C. Milvus / Qdrant（⭕ 备选） | 生产级、分布式 | 重、需独立服务、单机桌面过重 | 不采用 |

**取舍**：单机桌面场景，嵌入式 Chroma 最轻；代价是并发受限，通过共享检索器 + 锁解决。

#### 2.7 PDF 解析

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. MinerU 云端 + PyMuPDF 本地兜底（✅ 采用）** | 云端对表格/图纸/扫描件效果好；本地无外部依赖、可回退 | 云端需密钥、单文件 200 页需分片；本地版面能力有限 | 采用双路径 |
| B. 仅 PyMuPDF（⭕ 备选） | 零外部依赖、快 | 表格/扫描件/复杂版式效果差 | 不采用为唯一方案 |
| C. 仅 MinerU（⭕ 备选） | 解析质量最高 | 有配额/成本、断网不可用、无本地兜底 | 不采用为唯一方案 |

**取舍**：云端质量 + 本地兜底，合并为同一产物结构，后续流程无感知。

#### 2.8 文本模型（LLM）

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. DeepSeek（✅ 采用）** | 中文强、OpenAI 兼容、性价比高 | 推理模型思考链占用输出预算，需关闭思考 | 采用 |
| B. 智谱 GLM（⭕ 备选） | 中文好、生态好 | 需独立账号与密钥，文本调用成本/风格与现有 DeepSeek 端点存在差异 | 未采用为文本主模型 |
| C. OpenAI（⭕ 备选） | 质量高 | 国内访问/合规/成本问题 | 不采用 |

**取舍**：中文招标文本 + 成本敏感，DeepSeek 最契合；文本与嵌入均托管在火山方舟，嵌入模型见 2.9。

#### 2.9 嵌入模型与调用 SDK

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. 火山方舟 doubao-embedding-large + OpenAI 兼容 SDK（✅ 采用）** | 与文本模型同一云账号体系；包装层显式传 `encoding_format="float"`，规避 SDK 默认 base64 与方舟端点不兼容；2048 维 | 需要火山方舟密钥 | 采用 |
| B. 智谱 embedding-3（⭕ 备选，早期方案） | 中文嵌入成熟、维度选择多 | 需独立账号；原 zai-sdk 已随技术栈迁移移除，继续使用要重新引依赖 | 不采用 |
| C. 本地嵌入模型（⭕ 备选） | 离线、免费 | 中文效果/维度一致性难以保证，需额外部署 | 不采用 |

**取舍**：早期曾采用智谱 embedding-3 + zai-sdk，后统一迁移到火山方舟 doubao-embedding-large，SDK 收敛为 OpenAI 兼容客户端，通过显式 `encoding_format="float"` 解决编码兼容；zai-sdk 已从依赖中移除（迁移过程见 ADR-007 修订记录）。

#### 2.10 数据库与元数据持久化

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. PostgreSQL + SQLAlchemy（✅ 第一期采用）** | 持久化投标人资料与任务历史，为二期打基础 | 增加部署与迁移成本 | 第一期接入 |
| B. 本地文件 + 磁盘扫描（✅ 保留） | 零额外依赖、简单 | 无分页/查询能力 | 文档/产物元数据仍走磁盘 |
| C. SQLite（⭕ 备选） | 单文件、零运维 | 并发写弱、与 asyncpg 栈不一致 | 未采用 |

**取舍**：第一期接入 PostgreSQL 持久化「投标人资料 + 任务历史」两张核心表；文档/产物元数据仍走磁盘扫描（二期再迁）。

#### 2.11 对象存储

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. 本地磁盘 `upload/`（✅ 当前采用）** | 零依赖、简单、可被桌面端直接访问 | 无版本/生命周期管理 | 当前采用 |
| B. MinIO（⭕ 已规划待办） | 对象存储、版本、可扩展 | 增加服务依赖，桌面打开需额外对接 | 后续演进（待 Q-03） |

**取舍**：本地磁盘满足当前单机需求，MinIO 留作待办。

#### 2.12 Word 导出

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. python-docx（✅ 采用）** | 纯 Python、可控样式（字体/表格/分页） | 需手写 Markdown 解析，样式工作量大 | 采用 |
| B. Pandoc（⭕ 备选） | 格式转换强 | 需安装外部二进制，跨环境一致性差 | 不采用 |
| C. 模板 + 变量替换（⭕ 备选） | 排版精美 | 不适用于结构多变的整篇生成 | 不采用 |

**取舍**：需要程序化逐段生成，python-docx 最可控；导出失败不阻断 Markdown 与报告。

---

### 3. 最终选型总表

| 层次 | 采用方案 | 状态 |
|---|---|---|
| 前端形态 | Electron + React | ✅ |
| 前端框架 | React 19 + TypeScript + Vite + Tailwind | ✅ |
| 后端框架 | FastAPI + Pydantic + Uvicorn | ✅ |
| 任务队列 | Celery + Redis（`local` 兜底） | ✅ |
| 编写编排 | 标准库线程池工作流 | ✅ |
| 向量库 | ChromaDB（嵌入式） | ✅ |
| PDF 解析 | MinerU 云端 + PyMuPDF 本地兜底 | ✅ |
| 文本模型 | DeepSeek（OpenAI 兼容 SDK） | ✅ |
| 嵌入模型 | 火山方舟 doubao-embedding-large（OpenAI 兼容 SDK） | ✅ |
| 数据库 | PostgreSQL（第一期接入，持久化资料+任务历史） | ✅ |
| 对象存储 | 本地磁盘（本期不接 MinIO） | ✅ |
| Word 导出 | python-docx | ✅ |
| 包管理 | uv（后端）/ npm（前端） | ✅ |
| 测试 | pytest（后端）/ vitest + tsc + vite build（前端） | ✅ |

---

### 4. 已确定 vs 待定

| 状态 | 决策点 | 说明 |
|---|---|---|
| ✅ 已确定 | 前端形态、框架、后端框架、任务队列、编排、向量库、解析、模型、Word 导出 | 见上表 |
| ✅ 已确定 | 数据库持久化（Q-01） | 第一期接 PostgreSQL（资料+任务历史） |
| ✅ 已确定 | 检索问答对外入口（Q-02） | 第二期 |
| ✅ 已确定 | MinIO（Q-03）、Nginx（Q-04） | 第二期 |
| ✅ 已确定 | 多文档聚合（Q-05）、抽取粒度（Q-06） | 第二期 |

> 分期决策详见 `architecture.md` 第 10 节。

---

*文档结束。*

---

## 第二部分　团队分工与架构决策记录 ADR（原《团队分工与ADR》）

> 文档版本：1.0  
> 阶段：方案设计  
> 配套文档：`architecture.md`、`tech-decisions.md`、`architecture.md`、`api-contract.md`。

---

### 1. 文档说明

本文档给出**团队角色与分工**（对应到具体模块与交付物），以及**架构决策记录（ADR）**归档。分工以模块为单位，一人可兼任多角色，也可按角色拆给多人。

---

### 2. 团队角色与职责

| 角色 | 主要职责 |
|---|---|
| 方案/架构 | 需求梳理、技术选型、模块划分、ADR 维护、跨模块接口约定 |
| 后端开发 | 解析/抽取/索引/编写/接口/任务 的实现与测试 |
| 前端开发 | 桌面工作台（React）、Electron 主进程与 IPC、构建 |
| 测试/验收 | 离线测试、手工验收、质量门禁 |
| 文档/运维 | 文档维护、环境准备、部署与排障 |

---

### 3. 模块 → 角色 → 交付物

| 模块 | 责任角色 | 交付物 |
|---|---|---|
| 需求与边界 | 方案/架构 | `architecture.md`、待确认问题清单 |
| 技术选型 | 方案/架构 | `tech-decisions.md`、ADR |
| 模块划分与数据流 | 方案/架构 | `architecture.md` |
| 文档解析 | 后端开发 | `src/parsing/` + 解析测试 |
| 结构化抽取 | 后端开发 | `src/extraction/` + 抽取测试 |
| 检索索引 | 后端开发 | `src/vectorstore/` + 索引测试 |
| 投标编写编排 | 后端开发 | `src/bid_writer/` + 编写测试 |
| 接口服务 | 后端开发 | `api/` + 接口测试 |
| 任务分发/执行器 | 后端开发 | `src/tasks.py` + 任务测试 |
| 命令行 | 后端开发 | `src/cli/` + CLI 退出码测试 |
| 配置 | 后端开发 | `.env.example`、`src/config.py` |
| 桌面工作台 UI | 前端开发 | `frontend/src/`（四工作区 + 状态） |
| 桌面桥/IPC | 前端开发 | `frontend/electron/`（主进程 + preload） |
| 构建与类型检查 | 前端开发 | `npm run build` 通过 |
| 离线测试与门禁 | 测试/验收 | pytest 全绿、`tsc --noEmit` 通过 |
| 环境与运维 | 文档/运维 | 环境核对清单、启动说明 |

---

### 4. 里程碑建议

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| M0 方案设计 | 需求/选型/模块/分工/ADR | 本 4 份文档评审通过 |
| M1 环境与骨架 | 依赖安装、项目骨架 | `uv sync`、`npm install` 成功 |
| M2 文档接入 | 解析 + 抽取 + 索引 + CLI | `ingest` 跑通，产物落盘 |
| M3 异步接口 | FastAPI + Celery + 轮询 | 上传/查询接口通过测试 |
| M4 投标编写 | 多角色工作流（线程池编排）+ Word | 编写产物 + 覆盖报告 |
| M5 桌面工作台 | React + Electron | 四工作区 + 桌面能力 |
| M6 验收 | 全量测试 + 手工验收 | 验收清单全通过 |

---

### 5. 架构决策记录（ADR）

> 状态：`已接受` / `已否决` / `待定`。每个 ADR 记录背景、决策、备选方案与后果。

#### ADR-001：采用 Electron 桌面端

- **状态**：已接受
- **背景**：需要「打开本地 Word」「在文件管理器中定位产物」的能力；纯浏览器只能下载。
- **决策**：Electron + React，通过 preload 桥暴露受限能力。
- **备选**：纯浏览器 Web；Tauri。
- **后果**：需维护主/渲染/预加载三进程与 IPC 安全；已配置 electron-builder 产出 Windows 安装包，开发态仍可源码运行。

#### ADR-002：采用 FastAPI + 前后端分离

- **状态**：已接受
- **背景**：Agent 业务逻辑全部在后端，Web 只做交互；需要异步接口与自动文档。
- **决策**：FastAPI + Pydantic，接口以 `/api` 前缀、无鉴权、默认仅本机监听。
- **备选**：Flask；Django + DRF。
- **后果**：前端通过 Vite 代理访问；打包后回退本机地址。

#### ADR-003：采用 Celery + Redis 异步任务（local 兜底）

- **状态**：已接受
- **背景**：解析/编写耗时数分钟，不能在请求内执行；需状态/进度/结果。
- **决策**：Celery + Redis 为默认后端，`TASK_BACKEND=local` 进程内兜底。
- **备选**：进程内线程池为主；RQ/Dramatiq。
- **后果**：Windows 下只能 solo 池；任务结果默认保留 24 小时。

#### ADR-004：编写编排采用固定角色工作流（线程池扇出）

- **状态**：已修订（取代原「采用 LangGraph 编排」决策）
- **背景**：流程是「计划 → 分章节并行 → 汇总」，章节数量运行时动态，但角色与跳转规则设计期固定，无运行时自主决策。
- **决策**：用标准库 `ThreadPoolExecutor` 实现章节级扇出，Planner/Writer/Reviewer/Reviser 为职责固定的函数节点（Plan-and-Execute 风格多角色工作流，非自主多 Agent）。
- **备选**：LangGraph `StateGraph` + `Send`；CrewAI/AutoGen。
- **后果**：无额外框架依赖，技术栈与实现一致；代价是复杂条件分支、人工中断恢复等能力需自行实现，出现该类需求时再重新评估图编排框架。
- **修订记录（2026-11-06）**：原 ADR 写为「采用 LangGraph `StateGraph` + `Send`」，但实际实现从未引入该框架，为避免决策记录与代码不符，按真实实现修订。

#### ADR-005：采用 ChromaDB 嵌入式向量库

- **状态**：已接受
- **背景**：单机桌面场景，需要持久化检索索引但不想要独立向量服务。
- **决策**：ChromaDB `PersistentClient`，目录 `./chroma_db`，cosine 距离。
- **备选**：FAISS；Milvus/Qdrant。
- **后果**：并发写需串行化（共享检索器 + 锁）；索引与进程绑定。

#### ADR-006：采用 MinerU 云端 + PyMuPDF 本地兜底解析

- **状态**：已接受
- **背景**：招标文件含复杂表格/图纸/扫描件，云端质量高但需密钥与配额。
- **决策**：MinerU 云端优先，失败/未配置回退 PyMuPDF；统一产物结构。
- **备选**：仅 PyMuPDF；仅 MinerU。
- **后果**：云端单文件 200 页需分片；本地版面能力有限。

#### ADR-007：采用火山方舟托管 DeepSeek（文本）+ doubao-embedding（嵌入）双模型

- **状态**：已修订（嵌入方案由智谱迁移至火山方舟）
- **背景**：文本生成与向量化需求不同；中文招标文本为主。
- **决策**：文本用 DeepSeek（经火山方舟 Ark 的 OpenAI 兼容端点），嵌入用火山方舟 doubao-embedding-large，同一 OpenAI 兼容 SDK 调用，包装层显式指定 `encoding_format="float"`。
- **备选**：单一模型；本地嵌入模型；智谱 embedding-3（早期方案）。
- **后果**：DeepSeek 需关闭思考链避免输出预算耗尽；显式 float 编码解决 OpenAI SDK 默认 base64 与方舟端点不兼容；文本与嵌入同云账号，密钥管理更简单。
- **修订记录（2026-11-06）**：原 ADR 写为「智谱 embedding-3 + zai-sdk」，实际嵌入已迁移火山方舟 doubao-embedding-large，zai-sdk 依赖已移除，按真实实现修订。

#### ADR-008：第一期接入 PostgreSQL 持久化投标人资料与任务历史

- **状态**：已接受（第一期）
- **背景**：投标人资料刷新即丢是核心痛点；任务历史需跨重启追溯；数据库是二期功能的公共基础。
- **决策**：第一期接入 PostgreSQL + SQLAlchemy，持久化「投标人资料 + 任务历史」两张核心表；文档/产物元数据仍扫描 `upload/` 目录。
- **备选**：SQLite（未采用，并发写弱、与 asyncpg 栈不一致）；本地文件扫描（仍用于文档元数据）。
- **后果**：增加 PostgreSQL 部署与迁移成本；文档/产物元数据留待二期入库。

#### ADR-009：合规约束——不编造、不自动报价

- **状态**：已接受
- **背景**：投标文件含公司、人员、业绩、财务、报价等敏感事实。
- **决策**：缺失资料统一 `【待填写：字段名】`；报价/清单只生成结构化占位。
- **备选**：自动补全（否决）。
- **后果**：生成结果是草稿，必须人工核对；输出免责声明。

---

### 6. 已定稿 ADR（第二期不实现，列为后续演进）

| 编号 | 决策 | 状态 |
|---|---|---|
| ADR-011 | 不引入 MinIO 对象存储（产物存本地磁盘） | 已暂缓（第二期） |
| ADR-012 | 不接 Nginx 生产部署（第一期单机开发态） | 已暂缓（第二期） |
| ADR-013 | 不支持多文档项目聚合（doc_id 对应单份招标文件） | 已暂缓（第二期） |

> Q-01（PostgreSQL）已在第一期采纳，见 ADR-008。Q-02（检索问答页）、Q-06（抽取粒度）属功能范围，不单独列 ADR，决策见 `architecture.md` 第 10 节。

---

*文档结束。*

---

## 第三部分　技术栈与版本快照（原《技术栈》）

> 版本快照：2026-11-06  
> 版本来源：`pyproject.toml`、`uv.lock`、`frontend/package.json`、`package-lock.json` 与实际安装环境  
> 适用代码版本：当前 main；依赖版本以 `uv.lock` / `package-lock.json` 为权威来源  
> 配套文档：`api-contract.md`（业务、接口与数据规范）、`README.md`（使用说明）、`AGENTS.md`（开发约定）

---

### 1. 技术栈总览

| 层次 | 技术 | 版本 | 主要用途 |
|---|---|---|---|
| 运行平台 | Windows 11 | 10.0.26200 | 当前开发与运行环境 |
| 后端语言 | Python | 3.12.7 | 解析、抽取、索引、编写、接口 |
| Python 包管理 | uv | 0.10.12 | 依赖解析、虚拟环境、命令运行 |
| Web 接口 | FastAPI | 0.150.0 | HTTP API |
| ASGI 服务 | Uvicorn | 0.54.0 | 启动接口服务 |
| 数据校验 | Pydantic | 2.14.1 | 请求/响应与领域模型 |
| 配置管理 | pydantic-settings | 2.16.0 | 从 `.env` 读取配置 |
| 表单解析 | python-multipart | 0.0.32 | PDF 上传 |
| 异步任务 | Celery | 5.6.3 | 解析与编写任务队列 |
| 消息传输 | Kombu | 5.6.2 | Celery 传输层 |
| 队列/结果存储 | Redis | 服务端外部；客户端 8.1.0 | Broker、结果、任务标记 |
| 编写编排 | 标准库 ThreadPoolExecutor | Python 3.12 内置 | 章节级并发的多角色工作流编排 |
| 文本模型调用 | OpenAI Python SDK | 3.35.1 | 调用方舟 OpenAI 兼容接口 |
| 文本模型服务 | 火山方舟 | `deepseek-v4-flash` | 抽取与投标正文生成 |
| 嵌入调用 | OpenAI Python SDK | 3.35.1 | 调用火山方舟嵌入服务 |
| 嵌入服务 | 火山方舟 | `doubao-embedding-vision` | 生成检索向量 |
| 向量库 | ChromaDB | 1.5.9 | 本地持久化检索索引 |
| 本地 PDF 解析 | PyMuPDF | 2.0 | 书签、文本、图片、分片 |
| 云端 PDF 解析 | MinerU API v4 | 外部服务 | 版面、表格、扫描件解析 |
| HTTP 客户端 | httpx | 0.28.1（传递依赖） | MinerU 云端接口调用 |
| Word 导出 | python-docx | 1.2.0 | Markdown 草稿转 Word |
| 测试框架 | pytest | 9.1.1 | 后端离线测试 |
| 前端运行库 | React / React DOM | 19.3.1 | 桌面工作台 UI |
| 前端语言 | TypeScript | 7.0.2 | 类型检查与构建 |
| 前端构建 | Vite | 8.3.4 | 开发服务器与生产构建 |
| React 插件 | @vitejs/plugin-react | 6.1.2 | React 支持 |
| 样式 | Tailwind CSS | 4.3.3 | 原子化样式 |
| 样式插件 | @tailwindcss/vite | 4.3.3 | Tailwind 构建集成 |
| 桌面容器 | Electron | 44.9.1 | 桌面窗口、IPC、文件操作 |
| 系统 Node | Node.js | 24.19.0 | 前端构建工具运行 |
| 系统 npm | npm | 11.17.0 | 前端依赖与脚本 |
| Electron 内置 | Chromium / Node.js / V8 | 版本随 Electron 44.9.1 发布 | 渲染引擎、主进程运行时与 JS 引擎，具体版本见 Electron 官方发布说明 |

---

### 2. 运行环境

#### 2.1 操作系统

- 当前开发环境：Windows 11 Home China，版本 10.0.26200。
- 任务执行器在 Windows 下必须使用 `solo` 池，不能使用 `prefork`。
- 路径包含中文、空格或跨盘符时，均按绝对路径解析；项目配置以仓库根目录为锚点。

#### 2.2 Python 环境

| 项目 | 值 |
|---|---|
| Python | 3.12.7 |
| 最低要求 | `>=3.12`（`pyproject.toml`） |
| 版本文件 | `.python-version` → `3.12` |
| 虚拟环境 | `.venv/` |
| 包管理器 | uv 0.10.12 |
| 依赖锁文件 | `uv.lock` |
| 默认索引 | 清华 PyPI 镜像（`pyproject.toml` 中配置） |

Python 依赖全部安装在项目 `.venv` 中；`uv run` 会自动使用该环境，不需要手工激活。

#### 2.3 Node 环境

| 项目 | 值 |
|---|---|
| 系统 Node.js | 24.19.0 |
| 系统 npm | 11.17.0 |
| 前端锁文件 | `package-lock.json`（lockfileVersion 3） |
| 前端包管理配置 | 无 `.npmrc`、`.nvmrc`、`pnpm-lock.yaml` 或 `yarn.lock` |
| Electron 内置 Node.js | 随 Electron 44.9.1 发布，版本见 Electron 官方发布说明 |

系统 Node 与 Electron 内置 Node 是两套运行时：前者用于 Vite/TypeScript 构建，后者用于桌面主进程。

#### 2.4 外部依赖

| 依赖 | 默认地址 | 是否必需 |
|---|---|---|
| Redis | `redis://localhost:6379` | 队列模式必需；本地模式不需要 |
| DeepSeek API（火山方舟托管） | `https://ark.cn-beijing.volces.com/api/coding/v3` | 抽取与编写必需 |
| 火山方舟（嵌入） | `https://ark.cn-beijing.volces.com/api/v3` | 建立检索索引与检索必需 |
| MinerU API v4 | `https://mineru.net/api/v4/...` | 云端解析需要；本地解析不依赖 |
| ChromaDB | 本地 `./chroma_db` | 内嵌运行，无需独立服务 |
| PostgreSQL | `localhost:5432` | 持久化投标人资料与任务历史（第一期已接入，缺失时降级为内存/磁盘） |

---

### 3. 后端技术栈

#### 3.1 Web 接口层

| 组件 | 版本 | 说明 |
|---|---|---|
| FastAPI | 0.150.0 | 路由、依赖注入、请求校验、响应模型、OpenAPI 文档 |
| Starlette | 1.9.0 | FastAPI 底层 ASGI 框架（传递依赖） |
| Uvicorn | 0.54.0 | ASGI 服务，`uvicorn[standard]` 安装 |
| python-multipart | 0.0.32 | 解析上传表单 |
| Pydantic | 2.14.1 | 请求/响应、解析模型、抽取模型、编写模型 |
| pydantic-settings | 2.16.0 | `.env` 配置加载与类型转换 |

关键用法：

- `api/main.py` 使用应用工厂 `create_app(settings)`，在生命周期中创建/关闭任务分发器；
- `CORSMiddleware` 配置允许来源，并暴露 `Content-Disposition`；
- `FileResponse` 提供 Markdown 预览与产物下载；
- 上传经 `UploadFile` 接收并校验扩展名、PDF 文件头与大小；当前实现 `await file.read()` 一次性读入内存，大文件内存占用是已知限制，后续改造为临时文件流式落盘；
- 路由分为 `api/routes.py`（任务/健康检查）与 `api/library.py`（文档库/历史/产物）；
- 同步 I/O 路由使用普通 `def`，由 FastAPI 放入线程池执行；任务提交本身不在请求内执行。

服务入口：

| 入口 | 说明 |
|---|---|
| `api.main:app` | ASGI 应用对象 |
| `python -m api` | 读取 `API_HOST` / `API_PORT` 后启动 |
| `/docs` | 自动生成的交互式接口文档 |

#### 3.2 异步任务层

| 组件 | 版本 | 说明 |
|---|---|---|
| Celery | 5.6.3 | 任务定义、状态、进度、结果 |
| Kombu | 5.6.2 | Celery 消息传输（传递依赖） |
| redis-py | 8.1.0 | Redis 客户端，用于 Broker、结果与任务标记 |
| Redis Server | 外部 | 默认 `localhost:6379`，数据库 0/1 |

任务定义：

| 任务名 | 入口 | 用途 |
|---|---|---|
| `bidding.parse_document` | `src/tasks.py::parse_document_task` | 解析、抽取、建立检索索引 |
| `bidding.write_bid` | `src/tasks.py::write_bid_task` | 多角色编写投标文件 |

队列配置：

| 配置 | 值 |
|---|---|
| Broker | `CELERY_BROKER_URL`，默认 `redis://localhost:6379/0` |
| 结果后端 | `CELERY_RESULT_BACKEND`，默认 `redis://localhost:6379/1` |
| 任务标记 | Redis DB 0，键 `bidding:job:<job_id>` |
| 结果保留 | `CELERY_RESULT_EXPIRES`，默认 86400 秒 |
| 执行器池 | `CELERY_WORKER_POOL`，默认 `solo` |
| 序列化 | JSON |
| 时区 | `Asia/Shanghai` |
| 状态跟踪 | `task_track_started=True` |
| 延迟确认 | `task_acks_late=True` |
| 预取 | `worker_prefetch_multiplier=1` |
| 连接超时 | 连接 2 秒、读写 5 秒 |

两种任务后端：

| 后端 | 实现 | 适用场景 |
|---|---|---|
| `celery` | Redis 队列 + 独立执行器 | 默认，支持长任务、进度、跨请求执行 |
| `local` | 进程内线程池 + 内存任务表 | 测试、没有 Redis 的开发环境 |

`local` 后端不支持跨进程、重启后任务丢失；生产/日常使用应使用 `celery`。

#### 3.3 编写编排层

| 组件 | 来源 | 说明 |
|---|---|---|
| `ThreadPoolExecutor` | 标准库 `concurrent.futures` | 章节级并发扇出 |

关键用法：

- `run_write_pipeline` 先串行完成各章检索（共享检索器加锁），再按章节计划扇出 `section_agent`；
- `ThreadPoolExecutor` + `as_completed` 收集结果，并发度取 `BID_WRITER_MAX_CONCURRENCY`；
- 结果按 `plan.order` 重排，保证章节顺序确定；
- 每个章节内部执行「检索 → 写作 → 评审 → 修订」，修订次数有上限（0–3），
  Reviser 与 Writer 共享章节计划、评分因素、检索片段与投标人资料上下文（T-014）；
- LLM 语义评审之外，`rules.py` 做零模型调用的确定性规则检查（引用有效性、
  doc_id 归属、报价禁令、占位符事实），结果独立于评分写入报告（T-013）；
- 覆盖报告分计划/正文/证据三层漏斗（`coverage.py`，T-012），纯确定性计算，
  旧字段 `covered` 保留为计划层别名；
- 最终由 `assemble` 合并 Markdown、生成报告并可选导出 Word。

#### 3.4 文本模型与嵌入服务

##### 文本模型

| 项目 | 值 |
|---|---|
| 调用 SDK | OpenAI Python SDK 3.35.1 |
| 服务 | 火山方舟 OpenAI 兼容端点 |
| 模型 | `deepseek-v4-flash`（按账号可见型号填写） |
| 基础地址 | `https://ark.cn-beijing.volces.com/api/coding/v3` |
| 默认温度 | `0.0` |
| 最大输出 | `32768` tokens |
| 思考模式 | 默认关闭（`LLM_THINKING=disabled`） |
| JSON 模式 | 抽取时启用；模型不支持时自动退化 |
| 重试 | 连接、超时、限流、5xx 自动重试；4xx 直接失败 |

使用位置：

- `src/extraction/llm_client.py`：统一客户端；
- `src/extraction/extractor.py`：结构化抽取；
- `src/bid_writer/agents.py`：策划、写作、评审、修订。

注意事项：

- 该模型为推理模型，思考链与正文共享输出预算；默认关闭思考以避免空响应；
- 输出被截断（`finish_reason=length`）时抛出明确错误，需提高 `LLM_MAX_TOKENS`；
- JSON 模式只保证语法合法，不保证符合 schema；schema 校验由上层完成。

##### 嵌入模型

| 项目 | 值 |
|---|---|
| 调用 SDK | OpenAI Python SDK 3.35.1 |
| 服务 | 火山方舟 |
| 模型 | `doubao-embedding-vision`（可换 `doubao-embedding-large`） |
| 基础地址 | `https://ark.cn-beijing.volces.com/api/v3` |
| 维度 | 2048 |
| 批大小 | 10（方舟单次上限） |
| 编码格式 | 显式 `float` |

为什么必须显式指定编码格式：

- OpenAI SDK 3.35.1 在未显式传参时会强制注入 `encoding_format="base64"`；
- Chroma 的嵌入函数接口不会主动设置该参数，请求会落到 base64；
- 方舟端点要求 `float`，因此包装层显式传 `encoding_format="float"`。

#### 3.5 检索与存储层

| 组件 | 版本 | 说明 |
|---|---|---|
| ChromaDB | 1.5.9 | 本地持久化检索索引 |
| 存储方式 | 嵌入式 PersistentClient | 无需独立服务；数据目录 `./chroma_db` |
| 距离空间 | cosine | 集合创建时指定 |
| 集合 | `tender_document` / `tender_announcement` | 按文档类型区分 |
| 嵌入函数 | 自定义 `VolcengineEmbeddingFunction` | 包装火山方舟嵌入服务 |

关键用法：

- 入库采用「先 upsert 新块，再清理旧块」的幂等策略；
- 内容未变化的片段跳过，减少重复嵌入消耗；
- 多角色并行写作共享一个检索器实例，并用锁串行化查询；
- Chroma 客户端按进程复用，避免并发创建多个 PersistentClient 导致租户连接错误；
- 未建立索引或集合不存在时，检索返回空列表，编写仍可继续（质量下降）；
- 删除文档走 `delete_document_vectors`：只枚举已存在的两个已知集合（不 get_or_create
  凭空建集合），按 metadata `doc_id` 取 id 后精确删除，缺失集合/零片段按 0 条幂等处理，
  底层异常包装为 `IngestError` 显式上抛（T-016）。

#### 3.6 PDF 解析层

| 组件 | 版本 | 说明 |
|---|---|---|
| PyMuPDF | 2.0 | 本地解析、书签、图片、分片 |
| MinerU API | v4 | 云端版面、表格、扫描件解析 |
| httpx | 0.28.1 | 调用 MinerU 的 HTTP 客户端（传递依赖） |

本地解析：

- 读取 PDF 书签（TOC）作为章节骨架；
- 提取每页文本、图片、页码；
- 支持按页范围拆分超长 PDF；
- 在无书签时保留正文，但章节结构为空；
- 作为云端解析失败、未配置密钥或超过云端限制时的回退方案。

云端解析：

- 本地文件走批量上传流程：申请上传链接 → 上传字节 → 轮询批量结果 → 下载 ZIP；
- 单文件页数上限 200 页，超过自动拆分；
- 单文件大小上限 200 MB；
- 默认模型版本 `vlm`，语言 `ch`，启用公式与表格，OCR 默认关闭；
- ZIP 解压做路径穿越防护；
- 云端失败可按配置回退本地，并记录 `fallback_reason`；
- 下载超时由 `MINERU_DOWNLOAD_TIMEOUT` 单独控制（默认 300 秒）。

#### 3.7 Word 导出

| 组件 | 版本 | 说明 |
|---|---|---|
| python-docx | 1.2.0 | 生成 `bid.docx` |

导出规则：

- 标题、段落、列表按 Markdown 结构转换；
- 中文字体通过 XML 设置东亚字体；
- 导出失败不阻断 Markdown 与报告生成，失败原因写入 `warnings`；
- 输出文件位于投标产物目录。

#### 3.8 命令行

| 组件 | 说明 |
|---|---|
| argparse | Python 标准库 |
| 入口 | `python -m src.cli` |

子命令：

`doctor`、`parse`、`extract`、`ingest`、`inspect`、`write`。

#### 3.9 测试

| 组件 | 版本 | 说明 |
|---|---|---|
| pytest | 9.1.1 | 后端测试框架 |

测试配置：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra --strict-markers"
```

测试特点：

- 全部离线运行；
- 自动拦截外部网络连接（放行回环地址）；
- `settings` fixture 把 `upload/`、`chroma_db/` 重定向到临时目录；
- 使用合成 PDF、假嵌入函数、内存 Chroma 客户端；
- 覆盖解析、抽取、索引、接口、任务分发、编写、导出与资料库接口。

#### 3.10 依赖治理

历史上 `pyproject.toml` 曾声明 aiofiles、Alembic、langgraph 等未使用依赖，已在整改中移除
（见 ADR-004 与提交历史）；当前直接依赖均有实际引用，新增依赖需在 AGENTS.md 第 10 节约束下说明用途。
数据库结构变更前再按 AGENTS.md 第 8 节引入 Alembic baseline。

SQLAlchemy、asyncpg、greenlet 已用于 `src/db/` 持久化层；PostgreSQL 已接入实际持久化
（投标人资料 + 任务历史），数据库不可用时降级为仅日志告警、不阻断主链路。

---

### 4. 前端技术栈

#### 4.1 运行库

| 组件 | 版本 | 说明 |
|---|---|---|
| React | 19.3.1 | UI 组件与状态 |
| React DOM | 19.3.1 | 浏览器渲染 |
| @types/react | 19.3.0 | React 类型 |
| @types/react-dom | 19.3.0 | React DOM 类型 |

前端**没有**引入以下类别的依赖：

- 路由库（页面切换由 `useState` 管理）；
- 状态管理库（状态集中在 `App.tsx` 与少量 hooks）；
- UI 组件库（基础组件自建）；
- 图标库（内联 SVG）；
- Markdown 渲染库（预览使用等宽文本）；
- 数据请求库（原生 `fetch` / `XMLHttpRequest`）；
- 表单库（受控表单 + 手写校验）。

#### 4.2 语言与构建

| 组件 | 版本 | 说明 |
|---|---|---|
| TypeScript | 7.0.2 | 类型检查 |
| Vite | 8.3.4 | 开发服务器、生产构建 |
| @vitejs/plugin-react | 6.1.2 | React 插件 |

TypeScript 严格选项：

- `strict: true`
- `noUnusedLocals: true`
- `noUnusedParameters: true`
- `noFallthroughCasesInSwitch: true`
- `noEmit: true`
- `jsx: react-jsx`
- `moduleResolution: bundler`

构建脚本：

```json
{
  "dev": "vite",
  "build": "tsc --noEmit && vite build",
  "preview": "vite preview",
  "electron": "electron ."
}
```

Vite 配置要点：

- `base: './'`：打包产物可通过 `file://` 加载；
- 开发端口：5173；
- `/api` 代理到 `http://localhost:8001`；
- 构建输出：`frontend/dist`。

#### 4.3 样式

| 组件 | 版本 | 说明 |
|---|---|---|
| Tailwind CSS | 4.3.3 | 原子化样式 |
| @tailwindcss/vite | 4.3.3 | Vite 集成 |

样式约定：

- 不使用 `tailwind.config.js`；
- 主题变量写在 `src/index.css` 的 `@theme` 中；
- 深色为默认主题；
- 浅色主题通过 `html.light` 覆盖 Tailwind 颜色 token 实现；
- 组件只使用语义化工具类，不逐组件写 `dark:` 变体；
- 弹窗遮罩使用独立 `.modal-backdrop` 类；
- 不动态拼接 Tailwind 类名，避免构建扫描遗漏。

#### 4.4 桌面容器

| 项目 | 值 |
|---|---|
| Electron | 44.9.1 |
| Chromium / 内置 Node.js / 内置 V8 | 版本随 Electron 44.9.1 发布，以 Electron 官方发布说明为准 |
| 主进程 | CommonJS（`electron/main.cjs`） |
| 预加载脚本 | CommonJS（`electron/preload.cjs`） |
| 窗口尺寸 | 1440×900，最小 1024×700 |
| 安全设置 | `contextIsolation: true`、`nodeIntegration: false` |
| 加载策略 | 开发态加载 Vite 地址；打包或 `ELECTRON_LOAD_DIST=1` 加载 `dist/index.html` |
| IPC | `desktop:open-path`、`desktop:show-item-in-folder` |
| 页面桥 | `window.desktop.openPath`、`window.desktop.showItemInFolder` |
| 路径白名单 | 仅 `upload/` 目录，支持 `BIDDING_UPLOAD_ROOT` 覆盖 |
| 打包工具 | electron-builder（`npm run dist` 产出 Windows NSIS 安装包） |

#### 4.5 前端状态与本地存储

- 页面导航：单页状态，不使用 Router；
- 任务轮询：自定义 `useJobPolling`，默认 1 秒；
- 列表加载：自定义 `useResource`；
- 本地存储键：

| 键 | 内容 |
|---|---|
| `bidding.lastDocId` | 最后选中的文档 |
| `bidding.parseJobId` | 最近一次解析任务 |
| `bidding.bidJobId` | 最近一次编写任务 |
| `bidding.theme` | `dark` / `light` |

投标人资料不写入本地存储。

---

### 5. 服务、端口与拓扑

#### 5.1 默认端口

| 服务 | 端口 | 说明 |
|---|---|---|
| 接口服务 | 8001 | `API_PORT` |
| Vite 开发服务器 | 5173 | 前端开发 |
| Chroma 独立服务（可选） | 8000 | 项目默认使用嵌入式客户端，不需要启动 |
| Redis | 6379 | Broker / 结果 |
| PostgreSQL | 5432 | 持久化投标人资料与任务历史 |

#### 5.2 运行时拓扑

```text
浏览器 / Electron 渲染进程
        │  HTTP /api
        ▼
FastAPI + Uvicorn :8001
        │
        ├── 提交任务 ──► Redis DB0（队列 + 任务标记）
        │                     │
        │                     ▼
        │              Celery Worker（solo）
        │                     │
        │                     ├── PyMuPDF / MinerU
        │                     ├── DeepSeek API
        │                     ├── 火山方舟嵌入 API
        │                     └── ChromaDB（本地目录）
        │
        └── 查询结果 ──► Redis DB1（结果 / 进度）
```

#### 5.3 外部服务依赖矩阵

| 功能 | DeepSeek | 火山方舟（嵌入） | MinerU | Redis | Chroma |
|---|---|---|---|---|---|
| 仅本地解析 | — | — | — | — | — |
| 解析 + 抽取 | 必需 | — | 可选 | 队列模式必需 | — |
| 建立检索索引 | 可选（仅解析） | 必需 | 可选 | 队列模式必需 | 必需 |
| 投标编写 | 必需 | 检索时必需 | — | 队列模式必需 | 检索时必需 |
| 文档库/历史浏览 | — | — | — | 队列模式需查询任务 | — |

---

### 6. 存储与数据

#### 6.1 文件系统

| 路径 | 内容 | 是否纳入版本控制 |
|---|---|---|
| `upload/files/` | 上传的源 PDF | 运行时文件忽略；已有示例 PDF 被跟踪 |
| `upload/files/.incoming/` | 上传临时文件 | 忽略 |
| `upload/parsed/<doc_id>/` | 解析、抽取、原始产物 | 忽略 |
| `upload/images/<doc_id>/` | 抽取的图片 | 忽略 |
| `upload/bids/<bid_id>/` | 投标 Markdown/Word/报告 | 忽略 |
| `chroma_db/` | 检索索引 | 忽略 |

#### 6.2 Redis

| 用途 | 数据库 | 键/内容 | 保留 |
|---|---|---|---|
| 任务队列 | DB 0 | Celery 队列 | 消费后移除 |
| 任务标记 | DB 0 | `bidding:job:<job_id>` | 默认 24 小时 |
| 任务结果与进度 | DB 1 | Celery 结果键 | 默认 24 小时 |

#### 6.3 ChromaDB

| 项目 | 值 |
|---|---|
| 客户端 | PersistentClient |
| 数据目录 | `CHROMA_DIR`，默认 `./chroma_db` |
| 距离空间 | cosine |
| 集合 | `tender_document`、`tender_announcement` |
| 元数据 | `doc_id`、`doc_type`、源文件名、章节、页码、内容指纹等 |
| 幂等 | 按 `doc_id` 对比，增量写入并清理旧片段 |

#### 6.4 PostgreSQL

- `DATABASE_URL` 指向 `postgresql+asyncpg://`，由 `src/db/session.py` 懒加载引擎与会话；
- 模型 `TaskHistory`、`BidderProfile` 定义于 `src/db/models.py`；
- 读写封装于 `src/db/repository.py`，采用「尽力而为」策略：写库失败仅记 warning，不阻断解析/编写；
- 任务提交时写 `task_history`（pending），进入终态时更新；投标人资料在提交 bid 时落库；
- 第一期用 `Base.metadata.create_all` 建表，暂未引入 Alembic 迁移历史；
- 测试通过 `set_session_factory` 替换为 `aiosqlite` 文件库，不依赖真实 PostgreSQL。

---

### 7. 配置体系

#### 7.1 配置来源

- 后端：`.env` → pydantic-settings → `Settings`；
- 路径：以仓库根目录为锚点，不依赖当前工作目录；
- 前端：Vite 环境变量 + 运行时判断；
- 配置示例：`.env.example`；
- `.env` 不纳入版本控制。

#### 7.2 后端配置分组

##### 接口服务

| 键 | 默认 | 说明 |
|---|---|---|
| `API_HOST` | `127.0.0.1` | 监听地址 |
| `API_PORT` | `8001` | 监听端口 |
| `API_MAX_UPLOAD_MB` | `200` | 上传大小上限 |
| `API_MAX_CONCURRENT_PARSES` | `1` | 本地后端解析并发 |
| `API_MAX_PENDING_JOBS` | `2` | 最大排队任务数 |
| `API_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,null` | 允许来源 |

##### 任务队列

| 键 | 默认 | 说明 |
|---|---|---|
| `TASK_BACKEND` | `celery` | `celery` / `local` |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | 队列 |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | 结果 |
| `CELERY_WORKER_POOL` | `solo` | Windows 必须用 solo |
| `CELERY_RESULT_EXPIRES` | `86400` | 结果保留秒数 |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | 同步执行调试开关 |

##### 文本模型

| 键 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | 必填（抽取/编写） |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型 |
| `DEEPSEEK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/coding/v3` | 火山方舟 OpenAI 兼容端点 |
| `LLM_TIMEOUT` | `120` | 请求超时 |
| `LLM_TEMPERATURE` | `0.0` | 温度 |
| `LLM_MAX_TOKENS` | `32768` | 输出上限 |
| `LLM_THINKING` | `disabled` | 思考模式 |

##### 嵌入与检索

| 键 | 默认 | 说明 |
|---|---|---|
| `EMBEDDING_API_KEY` | 空 | 必填（索引/检索） |
| `EMBEDDING_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | 基础地址 |
| `EMBEDDING_MODEL` | `doubao-embedding-large` | 模型 |
| `EMBEDDING_DIMENSIONS` | `2048` | 维度 |
| `EMBEDDING_BATCH_SIZE` | `10` | 批大小（火山方舟单次上限 10） |
| `EMBEDDING_MAX_RETRIES` | `10` | 429 限流时 SDK 指数退避重试次数 |
| `EMBEDDING_TIMEOUT` | `120` | 请求超时（秒） |
| `EMBEDDING_REQUEST_INTERVAL` | `1.0` | 批次间隔（秒），规避 QPS 限流 |
| `CHROMA_DIR` | `./chroma_db` | 索引目录 |

##### 云端解析

| 键 | 默认 | 说明 |
|---|---|---|
| `MINERU_API_KEY` | 空 | 云端密钥 |
| `MINERU_BATCH_UPLOAD_ENDPOINT` | `https://mineru.net/api/v4/file-urls/batch` | 批量上传 |
| `MINERU_BATCH_RESULT_ENDPOINT` | `https://mineru.net/api/v4/extract-results/batch/{batch_id}` | 批量结果 |
| `MINERU_TIMEOUT` | `180` | 请求超时 |
| `MINERU_DOWNLOAD_TIMEOUT` | `300` | 下载超时 |
| `MINERU_MODEL_VERSION` | `vlm` | 模型版本 |
| `MINERU_POLL_INTERVAL` | `5` | 轮询间隔 |
| `MINERU_MAX_POLLS` | `120` | 最大轮询次数 |
| `MINERU_LANGUAGE` | `ch` | 语言 |
| `MINERU_ENABLE_FORMULA` | `true` | 公式 |
| `MINERU_ENABLE_TABLE` | `true` | 表格 |
| `MINERU_IS_OCR` | `false` | OCR 开关 |
| `MINERU_MAX_PAGES` | `200` | 单文件页数 |

##### 投标编写

| 键 | 默认 | 说明 |
|---|---|---|
| `BID_WRITER_TOP_K` | `6` | 检索条数 |
| `BID_WRITER_MAX_CONCURRENCY` | `3` | 章节并发 |

#### 7.3 前端与桌面配置

| 变量 | 作用 |
|---|---|
| `VITE_API_BASE` | 构建期注入后端地址；未配置时使用相对 `/api`（同源托管/本地代理），代码不内置公网地址 |
| `VITE_DEV_SERVER_URL` | 桌面容器开发态加载的地址，默认 `http://localhost:5173` |
| `ELECTRON_LOAD_DIST` | 设为 `1` 时强制加载 `dist/index.html` |
| `BIDDING_UPLOAD_ROOT` | 桌面文件操作白名单根目录覆盖 |
| `ELECTRON_MIRROR` | 安装 Electron 二进制时的镜像地址（仅安装期） |

---

### 8. 常用命令

#### 8.1 后端

```bash
# 同步依赖
uv sync

# 新增依赖
uv add <package>

# 全量测试
uv run pytest

# 启动接口服务
uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8001
uv run python -m api

# 启动任务执行器（Windows 必须 solo）
uv run celery -A src.celery_app worker --loglevel=info --pool=solo

# 命令行
uv run python -m src.cli doctor --probe-embeddings
uv run python -m src.cli parse "upload/files/example.pdf"
uv run python -m src.cli extract <doc_id>
uv run python -m src.cli ingest "upload/files/example.pdf"
uv run python -m src.cli inspect tender_document --query "评标办法"
uv run python -m src.cli write <doc_id> --profile examples/bidder_profile.example.json
```

#### 8.2 前端

```bash
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173
npm run build      # tsc --noEmit && vite build
npm run electron   # 桌面窗口
```

#### 8.3 桌面加载打包产物

```bash
cd frontend
npm run build
ELECTRON_LOAD_DIST=1 npm run electron
```

#### 8.4 Electron 二进制安装

如果 `npm install` 后 `npm run electron` 找不到二进制：

```bash
cd frontend
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ \
  node node_modules/electron/install.js
```

---

### 9. 构建与交付

#### 9.1 后端

- 无独立编译步骤；
- 依赖与虚拟环境由 uv 管理；
- 运行入口为 `api.main:app` 与 `src.celery_app`；
- 产物、日志和索引均在仓库本地目录。

#### 9.2 前端

- `tsc --noEmit` 先做严格类型检查；
- Vite 输出到 `frontend/dist`；
- `base: './'` 保证 `file://` 可加载；
- 构建产物包含单个 JS 与单个 CSS 文件（当前规模约 294 KB / 24 KB，未做代码分割）；
- 已配置 electron-builder（`npm run dist` 产出 Windows NSIS 安装包到 `frontend/release/`）；日常开发仍可用源码方式运行桌面容器。

#### 9.3 CI / 质量工具

- GitHub Actions CI（`.github/workflows/ci.yml`）：后端作业跑 Ruff 检查与离线 pytest，前端作业跑类型检查与构建；
- 后端 Ruff 是统一静态检查门禁，未配置 ESLint / Prettier / Biome；
- 前端已引入 vitest 单元测试（`frontend/tests/`），CI 前端作业当前只构建，测试在本地与提交前运行；
- 前端构建门禁是 `npm run build`（含 `tsc --noEmit`）。

---

### 10. 版本管理

#### 10.1 后端

- `pyproject.toml` 使用下限约束（`>=`），不锁死小版本；
- `uv.lock` 锁定实际解析结果；
- 升级流程：

```bash
uv lock --upgrade-package <package>
uv sync
uv run pytest
```

- 新增依赖必须写入 `pyproject.toml`，不能只依赖传递安装；
- 当前 `httpx` 被 `src/parsing/mineru_client.py` 直接导入，但未在 `pyproject.toml` 中声明，属于待修正项。

#### 10.2 前端

- `package.json` 使用 `^` 范围；
- `package-lock.json`（lockfileVersion 3）锁定实际版本；
- 升级流程：

```bash
cd frontend
npm update <package>
npm run build
```

- Electron 二进制下载受网络影响，失败时使用镜像重装。

#### 10.3 运行时版本

- Python 最低 3.12；
- Node 未设置 `engines` 或版本文件；
- Electron 内置 Node 与系统 Node 不一致，构建脚本与桌面主进程需区分；
- Redis 服务端版本未在仓库中约束。

---

### 11. 关键技术决策与原因

#### 11.1 为什么使用 FastAPI + Pydantic

- 请求/响应模型与领域模型统一；
- 上传、JSON 请求、文件下载均有成熟支持；
- 自动生成 OpenAPI 文档，便于联调；
- 依赖注入便于测试时替换配置与分发器。

#### 11.2 为什么任务与接口分离

- 解析 200 页 PDF、抽取、嵌入、编写都可能耗时数分钟；
- HTTP 请求内执行会阻塞连接、难以重试、无法展示进度；
- 队列模式支持独立扩容执行器、结果保留与进度上报；
- 本地线程池后端保证测试和无 Redis 环境仍可运行。

#### 11.3 为什么编写编排用线程池工作流而非 Agent 框架

- 编写流程是固定的「计划 → 分章节并行 → 汇总」，角色与跳转规则在设计期已确定，没有运行时自主决策；
- 章节数量动态但彼此无依赖，`ThreadPoolExecutor` 扇出即可满足并发，无需引入状态图框架；
- 并发上限可配置，便于控制费用与资源；
- 不引入未使用的框架依赖，技术栈与实际实现保持一致；若未来出现条件分支、人工中断恢复等复杂编排需求，再评估 LangGraph 等图编排框架。

#### 11.4 为什么使用嵌入式 ChromaDB

- 单机桌面场景，不需要独立向量服务；
- 持久化目录随项目走，便于备份与清理；
- 自定义嵌入函数 `VolcengineEmbeddingFunction` 封装火山方舟嵌入，显式指定 `encoding_format="float"`；
- 代价是并发写入需要串行化，且索引与进程绑定。

#### 11.5 为什么同时使用云端与本地 PDF 解析

- 云端对表格、图纸、扫描件效果更好；
- 本地解析无外部依赖、速度快、可作为回退；
- 书签是章节结构的主要来源，本地解析也能提供；
- 两种路径合并为同一套产物结构，后续流程无感知。

#### 11.6 为什么嵌入请求要显式传 `encoding_format`

- OpenAI SDK 在嵌入接口上会默认注入 `encoding_format="base64"`；
- Chroma 的嵌入函数不会主动设置该参数，请求因此落到 base64；
- 方舟端点要求浮点数组，包装层显式传 `encoding_format="float"` 解决兼容问题；
- 文本模型与嵌入共用 OpenAI SDK，只是 base URL 与模型不同。

#### 11.7 为什么前端不引入路由/状态/UI 库

- 只有四个工作区，状态集中在单页；
- 避免额外依赖与包体；
- 交互模式固定，自建基础组件成本可控；
- 代价是复杂页面扩展时需要自行维护组件规范。

#### 11.8 为什么使用 Electron

- 需要打开本地 Word、定位产物文件夹；
- 需要与本地后端、文件系统协作；
- 通过预加载桥限制能力，避免页面直接获得 Node 权限；
- 已接入 electron-builder 打包 Windows 安装包；开发态仍可直接运行 Electron 加载 Vite 或 `dist/` 页面。

#### 11.9 为什么浅色主题采用覆盖颜色 token

- 组件大量使用同一套 slate/sky/emerald/amber/rose 工具类；
- 在 `html.light` 下覆盖颜色变量即可整体换肤；
- 避免在每个组件写两套 `dark:` 类；
- 新增颜色或硬编码色值时，必须同时补充浅色覆盖。

---

### 12. 技术债与风险

| 项目 | 说明 | 建议 |
|---|---|---|
| ~~`httpx` 未声明~~ | 已在 `pyproject.toml` 显式声明 | 已解决 |
| ~~已声明未使用依赖~~ | aiofiles、Alembic、langgraph 已移除 | 已解决 |
| 无迁移历史 | 第一期用 `create_all` 建表，无版本化迁移 | 二期引入 Alembic 迁移 |
| 仅有静态令牌 | 共享 `API_TOKEN`，无登录与角色 | 增加登录、角色与项目隔离 |
| ~~无任务取消~~ | 已提供 `POST /jobs/{id}/cancel` 协作式取消 | 已解决；实时进度推送仍待评估 |
| 无实时进度 | 只能轮询 | 可评估 SSE/WebSocket |
| ~~前端无自动化测试~~ | 已引入 vitest 单元测试（`frontend/tests/`） | 已解决；端到端测试仍待补充 |
| ~~无 linter/CI~~ | 已接入 Ruff 与 GitHub Actions CI（后端 ruff+pytest，前端构建） | 已解决；CI 前端作业暂未跑 vitest |
| Chroma 并发限制 | 多线程共享客户端并用锁串行化 | 保持单例，避免每分支新建客户端 |
| Windows Celery 限制 | 不支持 prefork，只能用 solo/threads | 部署到 Linux 可考虑 prefork |
| DeepSeek 预算敏感 | 思考链与正文共享输出上限 | 保持思考关闭与足够 max_tokens |
| MinerU 页数限制 | 单文件 200 页，超过拆分 | 保持自动分片，关注分片边界 |
| ~~Electron 无打包配置~~ | 已配置 electron-builder 与 `npm run dist` | 已解决；尚未产出正式发布安装包 |
| 前端构建未分割 | 单 JS 约 290 KB | 体量可接受，后续按需分割 |
| 任务保留期 | 24 小时后查询 404 | 前端已提示，必要时延长保留 |
| 上传文件忽略规则 | `upload/files/*` 不再纳入版本控制 | 保留示例文件时需显式跟踪 |
| 上传整文件读入内存 | `await file.read()` 全量读入，200 MB 上限下存在内存风险 | 改造为临时文件流式落盘 + 原子替换 |
| 文本 LLM/MinerU 无自动重试 | 仅空结果硬编码重试一次与截断二分；429/5xx/网络抖动会导致任务失败（嵌入层已有 SDK 级重试） | 区分可重试错误，实现指数退避且次数可配置 |

---

### 13. 已知环境问题与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `uv sync` 报 `拒绝访问 (os error 5)` | `.venv` 内目录带 Windows 只读属性 | `cmd /c "attrib -R -S -H /S /D .venv\*"` 后重试 |
| `Failed to uninstall ... missing RECORD` | 残留损坏的 `*.dist-info` | 删除对应目录后重新同步 |
| `npm install` 成功但 Electron 二进制缺失 | postinstall 下载失败且静默退出 | 使用 npmmirror 重新执行 Electron 安装脚本 |
| Celery 在 Windows 启动失败 | 不支持 prefork | 使用 `--pool=solo` 或 `threads` |
| OpenAI SDK 调火山方舟嵌入报编码错误 | SDK 默认注入 `encoding_format=base64`，方舟端点要求浮点数组 | 包装层显式传 `encoding_format="float"`（见 11.6） |
| DeepSeek 返回空内容/截断 | 思考链占用输出预算 | 关闭思考，提高 `LLM_MAX_TOKENS` |
| Chroma 报租户连接错误 | 并发创建多个 PersistentClient | 共享检索器并加锁 |
| MinerU 下载中断 | 大文件下载超时 | 提高 `MINERU_DOWNLOAD_TIMEOUT` |
| `uv run celery`/`uvicorn` 指向旧环境 | 控制台脚本启动器指向旧虚拟环境 | 重新安装对应包以重建启动器 |
| 前端浅色主题局部仍是深色 | 新代码使用了硬编码颜色或未覆盖新色系 | 补充 `html.light` 颜色覆盖 |
| 任务一直 pending | 执行器未启动或未注册任务 | 启动/重启执行器 |
| 429 队列已满 | 排队任务达到上限 | 等待或调整 `API_MAX_PENDING_JOBS` |

---

### 14. 版本快照附录

#### 14.1 后端直接依赖

| 包 | 声明 | 实际安装 |
|---|---|---|
| asyncpg | `>=0.32.0` | 0.32.0 |
| celery | `>=5.6.3` | 5.6.3 |
| chromadb | `>=1.5.9` | 1.5.9 |
| fastapi | `>=0.143.3` | 0.150.0 |
| openai | `>=3.32.0` | 3.35.1 |
| pydantic-settings | `>=2.16.0` | 2.16.0 |
| pymupdf | `>=1.28.2` | 2.0 |
| python-docx | `>=1.2.0` | 1.2.0 |
| python-multipart | `>=0.0.32` | 0.0.32 |
| redis | `>=8.1.0` | 8.1.0 |
| sqlalchemy | `>=2.0` | 2.1.5 |
| uvicorn[standard] | `>=0.54.0` | 0.54.0 |

#### 14.2 后端关键传递依赖

| 包 | 实际安装 | 来源 |
|---|---|---|
| starlette | 1.9.0 | fastapi |
| pydantic | 2.14.1 | fastapi / pydantic-settings |
| kombu | 5.6.2 | celery |
| httpx | 0.28.1 | openai / 直接导入 |

#### 14.3 前端依赖

| 包 | 声明 | 实际安装 |
|---|---|---|
| react | `^19.3.1` | 19.3.1 |
| react-dom | `^19.3.1` | 19.3.1 |
| @tailwindcss/vite | `^4.3.3` | 4.3.3 |
| @types/react | `^19.3.0` | 19.3.0 |
| @types/react-dom | `^19.3.0` | 19.3.0 |
| @vitejs/plugin-react | `^6.1.2` | 6.1.2 |
| electron | `^44.8.0` | 44.9.1 |
| tailwindcss | `^4.3.3` | 4.3.3 |
| typescript | `^7.0.2` | 7.0.2 |
| vite | `^8.3.4` | 8.3.4 |
| vitest | `^5.1.0` | 5.1.0 |
| electron-builder | `^26.18.0` | 26.18.0 |

#### 14.4 工具版本

| 工具 | 版本 |
|---|---|
| uv | 0.10.12 |
| Python | 3.12.7 |
| Node.js（系统） | 24.19.0 |
| npm | 11.17.0 |
| Electron（含 Node） | 44.9.1（内置 Node 运行时，版本随 Electron 发布） |
| Chromium / V8 | 版本随 Electron 44.9.1 发布，以 Electron 官方发布说明为准 |

---

### 15. 维护说明

- 本文档是版本快照，升级依赖后应同步更新总览表与附录；
- 版本优先级：`uv.lock` / `package-lock.json` > `pyproject.toml` / `package.json` > 本文档；
- 新增后端依赖时，同时更新 `pyproject.toml`、`uv.lock` 与本文档；
- 新增前端依赖时，同时更新 `package.json`、`package-lock.json` 与本文档；
- 新增外部服务、端口、配置项时，同步更新 `api-contract.md` 与 `.env.example`；
- 任何密钥、令牌、真实账号不得写入本文档。

---

*文档结束。*
