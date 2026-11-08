# 应用容器化可行性评估（二期预研，非当前能力声明）

> **文档定位**
>
> 本文是 ADR-014（第一期应用不容器化，仅 Redis 以容器运行）的二期预研材料，用于在真正
> 立项前把「双进程拆分、Chroma 卷持久化、CI 构建成本」三件事的约束、方案与代价讲清楚。
> **本文不代表系统已具备容器化部署能力**：截至 main `38483da`，仓库内没有应用
> Dockerfile，只有 `deploy/docker-compose.redis.yml` 编排 Redis。文中的 Dockerfile/compose
> 片段均为**未经验证的设计示意**，落地前必须按第 7 节的门禁在 CI 真实构建并通过冒烟测试，
> 然后才能改写 ADR-014 与对外文档（AGENTS.md：只描述已实现并可验证的能力）。

## 1. 结论先行

- 当前单机开发/演示态下，tmux 双进程 + Redis 容器（ADR-014 现状）已满足需求，**不建议仅为
  「面试展示」而容器化**：投入集中在 CI 构建链与数据卷运维，不改变任何业务能力。
- 真正的触发条件是：需要在第二台机器上复现部署、多人共用一套环境、或出现水平扩展诉求。
- 技术上**可行但有两个硬约束必须先处理**：
  1. `upload/` 根目录在 `src/pipeline.py` 中硬编码为 `REPO_ROOT/upload`，不能用环境变量
     重定向，卷只能挂到镜像内该固定路径；要获得挂载自由度需先做一次带测试的小重构；
  2. Chroma 是进程内 `PersistentClient` 单例，多进程写同一索引有租户错误历史风险；好消息是
     现状下只有 worker 进程写 Chroma、API 进程从不打开向量客户端，单写者约束可以靠拓扑保证。
- 推荐演进路径：**单镜像 + 双命令（api/worker 两个 service）+ 外挂构建产物 dist**，而不是
  在 Dockerfile 内跑 Node 构建；CI 增加真实构建与三服务冒烟，通过后再更新 ADR 与文档。

## 2. 现状约束盘点（均有代码依据）

| 主题 | 事实 | 证据 |
|---|---|---|
| 进程形态 | uvicorn API 与 celery worker 是两个独立进程，入口分别为 `api.main:app`、`src.celery_app` | `api/main.py`、`src/celery_app.py`、`docs/deployment.md` 第 6 节 |
| 路径锚定 | `REPO_ROOT = Path(__file__).resolve().parents[1]`；`chroma_dir`、`log_dir` 的相对值锚到 REPO_ROOT，且接受绝对路径覆盖 | `src/config.py:12,93-105` |
| upload 硬编码 | `UPLOAD_DIR = REPO_ROOT / "upload"`，其下 files/parsed/images/bids 四个子目录，**无环境变量开关** | `src/pipeline.py:30-34` |
| Chroma 客户端 | 模块级 `_client` + 进程内 `threading.Lock` 双检锁，`chromadb.PersistentClient(path=settings.chroma_dir)`；锁只在单进程内有效 | `src/vectorstore/ingest.py:25-64` |
| 集合 | 固定两个集合 `tender_announcement`、`tender_document`，cosine 距离，嵌入函数为自定义火山方舟实现 | `src/vectorstore/ingest.py:20-21,66-73` |
| 谁写 Chroma | 仅 `src/tasks.py` 调用 `ingest_document`（worker 侧）；`api/` 全包不引用 `get_client`，API 进程不打开向量库 | 全仓 grep 结果 |
| 谁读 Chroma | 检索发生在编写链路 `sections.py`、`supplement.py` 与 CLI `inspect.py`，均在 worker 任务或 CLI 进程内 | 全仓 grep 结果 |
| 任务后端 | `TASK_BACKEND=celery`（默认）+ `local` 进程内兜底；容器化场景默认 celery，local 兜底不进容器拓扑 | `src/config.py`、`api/dispatch.py` |
| 配置注入 | pydantic-settings 从 REPO_ROOT/.env 与环境变量读取；密钥只允许环境/本地 env | `src/config.py:15-19`、AGENTS.md 第 7 节 |
| 前端构建 | `npm run build` = `tsc --noEmit && vite build`，产物 `frontend/dist`，vite `base:'./'` 适配静态挂载；令牌在构建期注入 | `frontend/package.json`、`frontend/vite.config.ts`、`frontend/src/api.ts:57,64` |
| Electron 负担 | electron/electron-builder 是 frontend 的 devDependency，`npm ci` 会下载超大桌面端二进制，但 Web 部署不需要它 | `frontend/package.json` devDependencies |
| 依赖规模 | 运行时声明 14 个依赖，锁内共 123 个包；chromadb 连带 onnxruntime、tokenizers、整套 opentelemetry、numpy，PyMuPDF 自带原生库 | `pyproject.toml`、`uv.lock` |
| 忽略规则 | 无 `.dockerignore`；`.gitignore` 已排除 `.venv`、`node_modules`、`upload/`、`chroma_db/`、`logs/`、`.env`、本地台账 | 仓库根 `.gitignore` |
| 健康检查 | `GET /api/health` 免令牌，返回 status/version/task_backend/broker_ok/worker_ok/queue_depth，可直接作为容器 HEALTHCHECK | `api/main.py:45-58`、`api/routes.py:66-77` |
| 数据库 | PostgreSQL 可选，`DATABASE_URL` 留空自动降级；当前无 Alembic，lifespan 内尽力建表 | `api/main.py` lifespan、ADR-008 |
| Redis | 已有只含 Redis 的 compose 制品，固定 project/卷名（bidding / bidding-redis-data） | `deploy/docker-compose.redis.yml` |

## 3. 目标拓扑与方案对比

### 3.1 候选方案

- **方案 0：维持现状**——tmux 跑双进程、Redis 用 compose。零新增构建链，缺点是换机复现要手工配 Python/uv 环境。
- **方案 1（推荐）：单镜像、双命令**——一个 runtime 镜像，compose 中 api 与 worker 两个 service 用同一镜像、不同 command；Redis 复用现有制品。
- **方案 2：双镜像**——api 与 worker 各自 Dockerfile。隔离更彻底，但两者代码与依赖 95% 重合，维护两份构建上下文，一期没有差异化收益。
- **轻量替代：systemd 单元**——不引入镜像构建，用两个 unit 管理双进程 + 现成 Redis 容器，解决「SSH 断开/开机自启/重启策略」，成本远低于容器化；若痛点只是进程守护，这是性价比最高的中间档。

### 3.2 方案 1 的服务拓扑（示意，未经验证）

```text
                 宿主 127.0.0.1:8001
                        │
        ┌───────────────▼────────────────┐
        │ service: api（镜像 bidding:app）│  command: uvicorn api.main:app --host 0.0.0.0 --port 8001
        │   挂 upload 卷(rw)、logs 卷(rw)  │  healthcheck: GET /api/health
        └───────────────┬────────────────┘
                        │ 入队（bidding 网络）
        ┌───────────────▼────────────────┐
        │ service: worker（同一镜像）      │  command: celery -A src.celery_app worker --pool=solo
        │  挂 upload(rw)、chroma(rw)、logs │  单写者：唯一打开 PersistentClient 的进程
        └───────────────┬────────────────┘
                        │ DB0/DB1
        ┌───────────────▼────────────────┐
        │ service: redis（现有 compose 制品）│  127.0.0.1:6382，命名卷 bidding-redis-data
        └────────────────────────────────┘
命名卷：bidding-upload / bidding-chroma / bidding-logs（redis 卷已存在）
```

> 该图是设计示意。API 容器是否也挂 chroma 卷：代码路径上它不访问，**可以不挂**以强制单写者；
> 但同镜像内仍含相关代码，须在文档中明确「禁止给 api service 挂 chroma 卷」的约定。

## 4. 专题一：双进程拆分

### 4.1 单镜像双命令的要点

- 镜像内 WORKDIR 即 REPO_ROOT（例如 `/app`），两个 service 共用同一文件系统布局，保证
  `REPO_ROOT` 锚定规则在容器内依然成立。
- command 覆盖即可区分角色，不需要 entrypoint 黑魔法；示例（**未验证**）：
  api: `uv run uvicorn api.main:app --host 0.0.0.0 --port 8001`；
  worker: `uv run celery -A src.celery_app worker --loglevel=info --pool=solo`。
- compose `depends_on` 只能保证启动顺序，不保证 Redis 就绪；应用本身已有 Redis 不可用
  503 降级与 worker 探活缓存，重启策略交给 `restart: unless-stopped`，不要自行加 sleep 脚本。
- 两个 service 都要挂 `upload` 卷：worker 写解析/标书产物，API 负责文档库列表与产物下载。
- 日志：`LOG_DIR` 锚定 `/app/logs`，挂卷后任务 JSONL 可在宿主收集；stdout/stderr 仍由
  docker 日志驱动捕获，两条通道并存，文档需写清分工。

### 4.2 worker 并发与 Chroma 风险（容器化不自动解决）

- 现状告警（deployment.md 第 6 节）：prefork 多子进程各自持有 PersistentClient，并发写同一
  索引可能触发租户错误。容器化把进程边界换成容器边界，**问题本身没有消失**。
- 拓扑级保证：全仓只有 worker 触碰 Chroma，因此把 worker 池固定为 `--pool=solo`（Linux 亦可
  `--concurrency=1` 的 prefork），就把写者收敛为单进程单线程，风险闭环。
- 这要求把 `CELERY_WORKER_POOL`/并发参数作为 compose 的显式约束写死并加测试，而不是依赖
  `.env` 默认值（当前 `.env.example` 默认 solo，但部署文档曾建议 Linux prefork）。
- 若未来要多 worker 副本水平扩展，单实例 PersistentClient 方案直接失效，必须先走第 5.4 节的
  Chroma 服务化改造，不能靠加容器副本解决。

### 4.3 优雅退出与健康检查

- uvicorn/celery 都响应 SIGTERM，compose 默认 10s 宽限对长 LLM 任务可能偏紧，需评估
  `stop_grace_period`；Celery 已有 `task_acks_late=True`，中断任务可重新投递。
- HEALTHCHECK 直接用免令牌的 `/api/health`，worker 无 HTTP 端口，用 `celery -A src.celery_app
  inspect ping` 或仅依赖 API 的 `worker_ok` 间接观测，避免在镜像内额外装 curl。

## 5. 专题二：Chroma 卷与数据持久化

### 5.1 卷映射的现实约束

- `chroma_dir`、`log_dir` 接受绝对路径，理论上可指向任意挂载点；但 `UPLOAD_DIR` 硬编码
  （第 2 节），upload 卷**只能**挂到 `/app/upload`。为统一，建议三卷都挂到 REPO_ROOT 下的
  原生路径：`/app/upload`、`/app/chroma_db`、`/app/logs`，不改代码即可工作。
- 若希望卷路径可自由配置，需先重构 `src/pipeline.py`：新增 `settings.upload_dir`（默认
  `./upload`，同样做 REPO_ROOT 锚定），替换四个 `*_DIR` 常量的根。这是生产代码改动，按
  AGENTS 3.1 要补规格与 TDD（路径解析、临时目录重定向、既有 upload 测试全绿），不能顺手做。

### 5.2 属主、初始化与备份

- python:3.12-slim 默认 root 运行；命名卷首次挂载由 root 建目录，当前进程也跑 root 时无碍，
  但安全加固要引入非 root USER，届时需处理卷属主（entrypoint chown 或命名 uid），属额外工作量。
- 命名卷随容器重建保留，但 `docker compose down -v` 会清卷；runbook 必须写明备份动作：
  upload 与 chroma_db 离线打包、redis 卷（AOF） separately，并与 AGENTS 5.3 的孤儿向量约束
  对齐——删除文档仍走应用级联删除，不能靠直接删卷绕过。
- 首次启动空卷是合法状态：Chroma 集合在首次 ingest 时 `get_or_create`，无需预置初始化容器。

### 5.3 .dockerignore（当前缺失，落地时必建）

构建上下文若不排除运行时与本地数据，镜像会被污染且可能泄密。至少排除：
`.git`、`.venv`、`node_modules`、`frontend/node_modules`、`upload/`、`chroma_db/`、`logs/`、
`.env`、`tmp_output/`、`*.log`、`dump.rdb`、两份本地台账与 `examples/demo/录屏操作卡.md`。
注意与 `.gitignore` 的差异：**被 git 忽略但构建需要的 `frontend/dist/` 要在 .dockerignore 中放行**
（见 6.1 方案 A），不能直接复制 .gitignore。

### 5.4 演进项：Chroma 服务化（多写者的唯一正路）

`.env.example` 注释里的 `chroma run --path ./chroma_db`（独立服务，8000 端口）对应把
PersistentClient 换成 HttpClient 的架构改造：新增一个 chroma service、客户端改远端连接、
嵌入函数注入方式回归测、备份模式从文件卷变成服务数据卷。这是独立项目，不属于首次容器化范围，
但应在 ADR 中预留为「多 worker 副本」的前置条件。

## 6. 专题三：CI 构建成本

### 6.1 镜像构建策略：不要在 Dockerfile 里装 Node

- **方案 A（推荐）：CI 先构建前端，runtime 镜像只装 Python。**
  现有 CI 的 frontend job 已会 `npm ci && npm run build`；让构建 job 产出 `frontend/dist`
  进入 Docker 构建上下文，Dockerfile 单阶段基于 `python:3.12-slim`，用 uv 按锁安装依赖后
  COPY 源码与 dist。优点：镜像内无 Node/electron，层最少，与 deployment.md「本地打包 dist
  再上传」的现有流程同构。
- 方案 B：多阶段（node:22 构建 → python runtime）。自包含但 `npm ci` 默认拉取 electron
  二进制（百 MB 级），须设 `ELECTRON_SKIP_BINARY_DOWNLOAD=1` 且仍要维护 npm 缓存层，
  构建时间与复杂度都更高；只有当要求「一条 docker build 出全量产物」时才值得。
- 原生依赖风险（**待实测**）：onnxruntime 常需 `libgomp1`，PyMuPDF/openssl 可能需补充
  运行库；slim 基座要加哪些 apt 包不能凭猜测写死，须在首次构建时按报错收敛并记录。

### 6.2 GitHub Actions 成本（私有仓库，按账号套餐计分钟）

- 现状：backend/frontend 两个 job，无镜像构建；新增成本来自一次 `docker build` + 冒烟。
- 量级（**经验估算，需以实际 run 为准**）：冷构建含 123 个包安装与原生 wheel 解压，约
  3–6 分钟；命中 buildx GHA 缓存 + uv 缓存后增量构建约 1–2 分钟。镜像体积估算 0.6–1.2 GB
  （chromadb/onnxruntime/numpy/PyMuPDF 占大头），以 `docker image ls` 实测为准。
- 缓存组合：`docker/setup-buildx-action` + `cache-to/from: type=gha`，uv 依赖层依赖
  `uv.lock` 哈希失效，Dockerfile 中先 COPY pyproject/lock 再 sync，最大化层复用。
- 触发策略建议：PR 与 main 只做 build + smoke、**不 push 镜像**；打 tag 才 push 到 GHCR。
  私有仓库对应 private package，拉取需 GHCR 认证，部署侧要存只读 PAT，又是一份密钥管理负担。
- 不需要多架构矩阵：服务器是 linux/amd64，加 arm64 只会翻倍分钟数。

### 6.3 冒烟测试门禁（没有它就不能宣称容器化）

AGENTS.md 明令禁止描述未验证能力，因此 CI 至少要真实执行：

1. `docker compose -f <app compose> up -d` 拉起 redis + api + worker；
2. 轮询 `/api/health` 直到 200 且 `broker_ok=true`、`worker_ok=true`；
3. 用 local/celery + Fake 模型夹具提交一个最小解析或编写任务，断言产物落到挂载卷；
4. 重建 api/worker 容器后命名卷数据仍在（验证持久化）；
5. `down -v` 清理，保证 runner 无残留。

这套冒烟本身要写进仓库（compose override + 脚本），属于新的可测试制品，而非一次性手工验证。

## 7. 风险登记册

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| Chroma 多进程写租户错误 | 高 | 容器化不改变 PersistentClient 单进程约束 | worker 固定 solo/concurrency=1，api 不挂 chroma 卷 |
| upload 路径不可配置 | 中 | 卷必须挂 /app/upload，拓扑自由度低 | 首期接受固定挂载；要灵活则立 TDD 小重构 |
| 卷属主/权限 | 中 | 非 root 运行时命名卷写入失败 | 明确 uid 策略并在冒烟覆盖 |
| 密钥注入 | 中 | 三套外部 API key + Redis 密码 + API_TOKEN | 仅 env_file/环境注入，禁止 buildArg 烤入；VITE 令牌本就是低强度防滥用（现状不变） |
| CI 分钟与 GHCR 存储 | 低-中 | 私有仓库计费，镜像近 GB 级 | gha 缓存、tag 才 push、单架构 |
| 文档与能力漂移 | 中 | 容易提前在 README 写「支持 Docker 部署」 | 冒烟通过前不改部署形态描述，ADR-014 保持有效 |
| 数据备份误删 | 中 | down -v 清卷、孤儿向量 | runbook + 应用级联删除约束（AGENTS 5.3） |
| Windows 开发/Linux 容器差异 | 低 | 路径、信号、solo 池差异 | 容器目标只声明 linux/amd64，Windows 仍走本地进程 |

## 8. 立项触发条件、验收标准与任务拆分建议

### 8.1 什么时候才值得做

- 出现第二台部署机或团队成员需要一致环境；或
- systemd 方案无法满足弹性/隔离诉求；或
- 明确要把「容器化部署」写入可演示能力并愿意承担 CI 成本。
- 仅为简历措辞不构成触发条件（ADR-014 已能诚实讲清「为何不容器化」，本身是架构判断力素材）。

### 8.2 建议任务拆分（落地时再入任务台账编号）

1. `.dockerignore` + runtime Dockerfile（方案 A）+ app compose（api/worker 复用 redis 制品）；
2. CI：buildx 构建 + 第 6.3 节五步调烟，PR 门禁；
3. worker 单写者约束测试（pool 参数断言、api service 不挂 chroma 卷的 compose 校验）；
4. 卷备份/恢复 runbook 写入 deployment.md，非 root USER 评估；
5. （可选，独立规格）`UPLOAD_DIR` 可配置化重构；
6. （可选，二期）Chroma HttpClient 服务化评估，作为多副本前置。

### 8.3 验收标准草案

- CI 中镜像构建与三服务冒烟全绿，且重跑两次结果稳定；
- 容器重建后 upload/chroma/logs 数据不丢，文档库与标书历史可跨重建访问；
- 仓库内每个 Docker/compose 引用都有对应文件且被 CI 验证，README/ADR 表述与实际一致；
- 不引入新外部服务（Nginx/k8s/MinIO 仍属后续），不产生未验证命令；
- ADR-014 增补修订记录或新立 ADR 说明状态变更，历史决策可追溯。

## 9. 与现有决策的关系

- 本文**不推翻** ADR-014：第一期维持应用不容器化；本文是其「备选」条目（完整应用容器化）的
  可行性展开，触发条件满足前 ADR-014 继续有效。
- 与 ADR-005（嵌入式 Chroma）、ADR-003（Celery+Redis/local 兜底）、ADR-008（可选 PostgreSQL）、
  ADR-012（一期不接 Nginx）保持兼容；容器化只改变进程打包与编排方式，不改这些选型。