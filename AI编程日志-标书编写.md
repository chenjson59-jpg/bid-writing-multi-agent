# AI编程日志-标书编写

## 1.创建标书编写多Agent系统

创建开发环境

```bash
# 创建 .venv 虚拟环境，指定 Python 版本
uv venv --python 3.12

# 初始化项目，生成 pyproject.toml 等文件
uv init
```

创建.env文件，设置LLM的API KEY

```bash
# DeepSeek模型 - 获取API密钥：https://platform.deepseek.com/
DEEPSEEK_API_KEY=

# LLM 模型配置
LLM_PROVIDER=deepseek
DEEPSEEK_MODEL=deepseek-flash

# 智谱模型 - 获取API密钥：https://bigmodel.cn/apikey/platform
ZHIPU_API_KEY=

# 嵌入模型配置
EMBEDDING_MODEL=embedding-3
# Chroma 向量库目录
# 启动命令：chroma run --path ./chroma_db
# 接口文档：http://localhost:8000/docs
CHROMA_DIR=./chroma_db

# API服务配置
API_HOST=0.0.0.0
API_PORT=8001

# PostgreSQL 数据库
DATABASE_URL=postgresql+asyncpg://postgres:123456@localhost:5432/bidding
# Redis 缓存
REDIS_URL=redis://localhost:6379/0

# MinerU 模型配置 - 获取API密钥：https://mineru.net/apiManage/token
# API密钥有效期 90 天，过期后需要重新获取
# 文档：https://mineru.net/apiManage/docs
MINERU_MODE=cloud
MINERU_API_KEY=
# 精准解析 API，页数限制 ≤ 200 页
MINERU_ENDPOINT=https://mineru.net/api/v4/extract/task
MINERU_TIMEOUT=180
MINERU_MODEL_VERSION=vlm
MINERU_POLL_INTERVAL=5
MINERU_MAX_POLLS=60
```

## 2.安装依赖

参考了智标的技术栈：https://github.com/Foulereee/ZhiBiao

提示词1：

```bash
安装FastAPI、uvicorn、Celery、Redis、SQLAlchemy 2.0、asyncpg、ChromaDB、OpenAI SDK、zai-sdk、LangGraph、React、Vite、TypeScript、TailwindCSS、Electron、python-docx、PyMuPDF、pytest
```

.gitignore：原本没忽略 node_modules/，会把 1887 个文件（含 223MB 的 electron.exe）提交进去，现已加上。

`npm run dev` 起前端
`npm run build` 出包
`npm run electron` 起桌面端（Electron 打包产物时从 dist/ 加载）

1.启动ChromaDB

```bash
uv run chroma run --path ./chroma_db

# 正在将数据保存到：./chroma_db
# 连接到Chroma：http://localhost:8000
```

2.启动Redis

```bash
redis-server.exe redis.conf

# 端口：6379
```

3.执行命令`/init`，创建CLAUDE.md，在该文件中增加指令：

```bash
## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。
```

## 3.文档解析

提示词1：

```
@"upload/files/6055594244948.pdf" 做文档解析，为后续多Agent标书编写做准备。
```

 **☐ 交付范围**

本次「文档解析」要产出到什么程度？

解析 + 抽取 + 入向量库
     在上一项基础上，把 Markdown 分块并嵌入 Chroma（走智谱 embedding-3），为后续 RAG 检索做准备。工作量最大，会真正用上那个空的 chroma_db。

 **☐ 解析引擎**

MinerU 为主 + PyMuPDF 兜底（推荐）
     与 .env 里已有的 MINERU_* 配置意图一致。MinerU 走云端 vlm 模型，对复杂版式/表格/扫描件效果好——后续完整招标文件会大量出现这类内容；失败时自动回退到本地 PyMuPDF。注意 MinerU 需把本地文件先传到签名 URL（/file-urls/batch），而 .env 里配的 MINERU_ENDPOINT 是单文件 URL 端点，需要补一个上传端点配置。

 **☐ 入口形式**

1. **CLI 模块（推荐）**

​     uv run python -m src.parsing <pdf路径>，先把单文档跑通，产出落到 upload/parsed/。不引入 FastAPI 脚手架，改动面最小，后续要接 API 时直接复用同一个pipeline 函数。

**☐ 分片策略**

目标文档 210 页，超出 MinerU 单文件 200 页限制，怎么处理？

自动分片走 MinerU（推荐）
     用 PyMuPDF 将 PDF 按页拆成 2 份（如 1-105、106-210），同一批上传解析后合并 Markdown，并按原 TOC 重新对齐章节。表格/评分表还原最好，消耗约 210页配额（免费额度约 1000-2000 页/天）。

 **☐ 抽取重点**

结构化抽取应该重点抽什么？

评分办法+资格+格式全量（推荐）
     除项目概况外，重点抽取第三章评标办法的评审因素/分值/评分标准、投标人须知前附表、资格审查条件、第八章投标文件格式清单、关键时间节点。这些直接决定标书怎么写、怎么得分。

## 4.引入测试框架pytest

提示词1：

```
@tests\ 引入测试框架pytest
```

**☐ 覆盖范围**

本次引入 pytest 的测试覆盖范围选哪个？

核心逻辑 + 关键集成（推荐）
     覆盖 TOC 解析、分片、章节感知分块、抽取 schema 容错、JSON 提取、合并去重、溯源核对、CLI 退出码；网络调用（MinerU/DeepSeek/智谱）全部 mock，约 45–55个用例，秒级跑完。

 **☐ 覆盖率**

是否同时引入覆盖率工具 pytest-cov？

不引入
     只装 pytest，保持依赖最小。

## 5.增加FastAPI 接口

提示词1：

```
搭起 api/ 的 FastAPI 应用，POST /api/parse 上传解析。
```

**☐ 返回方式**

202 + 轮询
     POST 立即返回 job_id，再通过 GET /api/jobs/{id} 查状态与结果。长任务体验更好、客户端不会超时，但多一个接口与内存任务表（服务重启即丢失）。

**☐ 解析范围**

解析 + 可选抽取/入库
     增加 extract / ingest 查询参数，按需继续执行。一步到位，但请求耗时显著变长（抽取要 2–4 分钟），且需要更大的上传/超时预算。

## 6.增加Celery 异步任务（待办）

提示词1：

```
增加Celery 异步任务。按 CLAUDE.md 里「长文档解析不能阻塞请求」的设想做成后台任务。需要先跑通 Redis + worker
```

**☐ 任务后端**

Celery 与现有内存任务后端怎么共存？

Celery 默认 + local 兜底（推荐）
     TASK_BACKEND=celery 为默认；Redis 不可用时 POST 返回 503。保留现有内存后端作为 local 模式，供测试和无 Redis 的开发环境使用。

**☐ Worker 池**

solo（推荐）
     单进程串行执行，Windows 上最稳定，适合本地桌面单用户；一次只跑一个解析任务。仍可通过 CELERY_WORKER_POOL 改成 threads。

## 7.多Agent标书编写

提示词1：

```
根据解析的招标文件，使用多Agent技术实现投标文件编写功能。
```

**☐ 投标人资料**

投标文件需要大量投标人自身信息（资质、业绩、人员、财务），系统目前没有这些数据。怎么处理？

提供 profile JSON（推荐）
     新增 bidder_profile.json 输入（公司名称、资质、类似业绩、人员、财务等）；缺失字段一律生成【待填写】占位符，绝不编造事实。

**☐ 功能入口**

这个功能做到哪一层入口？

CLI 优先（推荐）

​     新增 `uv run python -m src.cli write <doc_id>`，先跑通 LangGraph 多 Agent 流程与产物落盘；API/Celery 接入留到下一阶段。

**☐ 输出格式**

生成的投标文件用什么格式交付？

Markdown + Word
     额外用 python-docx 导出 .docx；更贴近投标实务，但样式控制工作量大。

## 8.增加API/Celery异步任务

提示词1：

```
增加API/Celery异步任务。同时新增 POST /api/bids 与 bidding.write_bid 任务，复用现有队列与轮询。
```

☐ profile 传递

POST /api/bids 的投标人资料（profile）怎么传？

JSON 请求体（推荐）
     请求体直接包含 profile 对象与参数：{"doc_id":"...","profile":{...},"max_sections":0}。无需文件上传，Pydantic 直接校验，最适合前端调用。

## 9.增加前端界面

```
增加前端界面
```

**☐ 功能范围**

这个前端界面需要覆盖哪些功能？（可多选）

1. [✔] 上传与解析

​         拖拽/选择 PDF，设置解析方式与抽取入库选项，实时显示阶段进度和解析摘要。

2. [✔] 投标文件编写

​         填写投标人资料与编写参数，发起编写任务，实时进度，展示评分覆盖与章节得分报告。

3. [✔] 已解析文档库

​         新增后端列表接口，展示 upload/parsed 下已有文档，可直接选中发起投标编写，无需重新上传。

❯ 4. [✔] 历史投标产物
         新增后端列表接口，展示 upload/bids 下历史生成结果，并支持下载/打开。

**☐ 资料录入**

投标人资料用哪种方式录入？

常用表单 + 高级 JSON（推荐）
     公司名称、法人、地址、资质等常用字段用表单；类似业绩、人员、财务等复杂结构放在可折叠的高级 JSON 编辑器中，两种视图同步。

**☐ 产物处理**

生成的投标文件产物如何处理？

下载 + 预览 + 打开文件夹（推荐）
     新增后端文件接口，界面可预览 Markdown、下载 md/docx/report，并通过 Electron IPC 在资源管理器中定位文件。

**☐ 布局风格**

界面采用哪种布局？

左侧步骤导航 + 工作区（推荐）

┌─────────────────────────────────────────────────────┐
│ ┌──────────────────────────────────────────────┐    │

│ │ 招标文件多智能体系统         ● 服务正常 8001 │    │

│ ├──────────┬───────────────────────────────────┤    │

│ │ ① 上传解析      │  上传招标文件 (PDF)               │  │
│ │ ② 编写标书      │  ┌─────────────────────────────┐  │  │
│ │ ③ 文档库          │  │  拖拽 PDF 到这里             │  │ │
│ │ ④ 历史产物      │  └─────────────────────────────┘  │  │
│ │                             │  [解析并抽取]  进度 ▓▓▓░░ 60%     │    │
│ │                             │  阶段日志…                         │   │
│ └──────────┴───────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘

## 10.增加深色/浅色切换按钮

提示词1：

```
增加深色/浅色主题切换按钮
```

## 11.增加公司信息进入PostgreSQL（待办）



## 12.增加检索功能（待办）

检索：「投标报价得分怎么算」→ 表10/表11；「施工组织设计怎么写」→ 三甲全部命中；「资格审查需要什么材料」→ 资格审查各表。

## 13.使用MinIO对象存储系统（待办）

启动MinIO

```bash
D:\minio\bin\start.bat

# start.bat
@echo off
set MINIO_ROOT_USER=minioadmin
set MINIO_ROOT_PASSWORD=admin123456
.\minio.exe server D:\MinIO\data --console-address "127.0.0.1:9000" --address "127.0.0.1:9005" > D:\MinIO\logs\minio.log 2>&1

# API: http://127.0.0.1:9005 
# 网页用户界面: http://127.0.0.1:9000 
```

## 99.安装Nginx（待办）

适合生产环境