# 服务器部署指南（单端口 + Redis 容器）

> **部署形态（诚实声明）**
>
> - **应用本身不容器化**：FastAPI（uvicorn）与 Celery worker 是宿主机上的两个进程，
>   运行在 tmux 会话中，SSH 断开不受影响；
> - **只有 Redis 运行在 Docker 容器**，仓库提供 `deploy/docker-compose.redis.yml`
>   可版本化复现该容器（也保留等价手工 `docker run` 与无 Docker 回退）；
> - FastAPI 挂载 `frontend/dist`，前端与 API 共用一个端口对外，不接 Nginx（ADR-012）；
> - 应用不容器化的理由与权衡见 `docs/tech-decisions.md` ADR-014。
>
> 本文从 README 部署章迁移，保留全部排障知识。文中 `admin@IP`、`~/group_2`、
> `Redis密码`、`自定义复杂令牌` 均为占位符，部署时全部替换，仓库不记录任何真实服务器地址。

## 1. 形态总览与约定

| 项 | 取值 | 说明 |
|---|---|---|
| Web 端口 | `8001` | 前端静态页与 `/api` 共用，uvicorn 监听 |
| Redis 宿主端口 | `6382` | 仅绑定 `127.0.0.1`，不对公网暴露 |
| 部署目录 | `~/group_2/CJS/bid-writing-multi-agent` | 示例路径，可整体替换 |
| 运行时目录 | `upload/`、`chroma_db/`、`logs/` | 均按仓库根目录锚定，不依赖当前工作目录 |
| 进程 | uvicorn ×1、celery worker ×1、redis 容器 ×1 | worker 与 API 必须能访问同一份 `upload/files/` |

## 2. 本地打包前端

访问令牌在构建期注入，必须与服务器 `.env` 的 `API_TOKEN` 一致：

```powershell
cd frontend
$env:VITE_API_BASE='http://<服务器地址>:8001'; $env:VITE_API_TOKEN='自定义复杂令牌'; npm run build
```

> 令牌会编译进前端 JS，防的是陌生脚本直接打 API，不是强保密手段；
> 高安全场景应改用反向代理 Basic Auth 或防火墙 IP 白名单。

前端代码有改动（新增页面、修复交互）时必须重新执行上面的构建，并把整个 `dist`
重新上传覆盖。只更新 `assets/` 而漏掉 `index.html`，浏览器会继续加载旧入口。

## 3. 服务器准备与取代码

```bash
docker ps                                      # 报 permission denied 则走第 4.4 节回退方案
ss -tln | grep -E ':(8001|6382)'               # 有输出说明端口被占，换端口
python3 -V                                     # 需 ≥3.12（pyproject 要求，3.11 会在 uv sync 失败）
# 连通性：只要拿到 HTTP 状态码就算通。401 是方舟网关要求鉴权（根路径未带密钥），属正常
curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 https://ark.cn-beijing.volces.com  # 期望 401/404，不是空或超时

cd ~/group_2
git clone git@github.com:chenjson59-jpg/bid-writing-multi-agent.git bidding && cd bidding
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
uv sync
```

仓库是私有的，服务器直接 `git clone` 会报 `Permission denied (publickey)`——那说明服务器
没有配 GitHub 的 SSH 私钥（首次提示 `Are you sure you want to continue connecting?` 只是
确认主机指纹，与权限无关）。三种取代码的方式任选其一：

| 方式 | 适用 | 操作 |
|---|---|---|
| 本地打包上传（推荐） | 服务器无 GitHub 凭据、或本地有未提交改动 | 见下方 `tar` + `scp` 命令 |
| 部署密钥 | 需要在服务器上 `git pull` 长期更新 | `ssh-keygen -t ed25519 -f ~/.ssh/bidding -N ""`，把 `~/.ssh/bidding.pub` 加到仓库 **Settings → Deploy keys**（只读），再 `GIT_SSH_COMMAND='ssh -i ~/.ssh/bidding' git clone ...` |
| HTTPS + PAT | 临时拉取 | `git clone https://github.com/chenjson59-jpg/bid-writing-multi-agent.git`，用户名填 GitHub 账号，密码填 Personal Access Token（`repo` 读权限） |

> **注意未提交改动**：`git clone` 只拉已提交的内容。本地若有尚未提交的改动（例如新增页面），
> 服务器上的后端与本地构建出的前端就会不一致，表现为新页面报 404 或功能缺失。
> 这种情况必须用打包上传，或先提交再 clone。
>
> 不要把 PAT 写进 clone 的 URL（会留在 shell 历史与 `.git/config`），也不要把私钥贴到聊天或截图里。
>
> PAT 的生成位置：GitHub 右上头像 → **Settings → Developer settings → Personal access tokens**。
> 选 Tokens (classic) 勾 `repo`；或选 Fine-grained tokens，仓库访问只选该仓库、权限给
> **Contents: Read-only**。令牌只在生成后显示一次，之后无法再查看，丢了只能重新生成。

本地打包上传（在仓库根目录执行，排除虚拟环境、依赖与运行时数据）：

```powershell
tar -czf bidding-src.tar.gz --exclude=.venv --exclude=node_modules `
  --exclude=frontend/node_modules --exclude=upload --exclude=chroma_db `
  --exclude=logs --exclude=.env .
scp bidding-src.tar.gz admin@IP:~/group_2/
```

服务器上解包后再补齐依赖（`uv sync` 会按 Linux 平台重建 `.venv`，不要上传 Windows 的 `.venv`）：

```bash
cd ~/group_2 && mkdir -p bidding && tar -xzf bidding-src.tar.gz -C bidding && cd bidding
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
uv sync
```

最后上传构建产物与已有数据（本地执行；后两者可选，省去重新解析的 LLM 费用）：

```powershell
scp -r frontend\dist admin@IP:~/group_2/bidding/frontend/
scp -r upload chroma_db admin@IP:~/group_2/bidding/
```

> **`admin@IP` 是占位符**：格式为「服务器登录用户名 @ 公网 IP」，换成你实际的账号（如 `admin`、`root`）。
> 本项目示例部署目录是 `~/group_2`，若你的目录是 `~/group_2/CJS`，把上面所有 `~/group_2` 换成 `~/group_2/CJS`。
> `scp` 提示密码时输入的是**这个账号**的密码；账号名写错（例如照抄 `user`）会一直提示密码错误。
> 全程用同一个账号操作：先用 `root` 建目录、再用 `admin` 上传，会导致属主不一致，
> 后续写文件、`git pull` 都会报 `Permission denied`。服务器禁用密码登录时 `scp` 也只能走密钥。

## 4. Redis

### 4.1 用 compose 编排（推荐）

仓库内的 `deploy/docker-compose.redis.yml` 是手工 `docker run` 的可版本化等价制品，
只编排 Redis 一个服务，应用进程不在其中。先准备环境文件（真实密码不入库，
`deploy/.env` 命中 `.gitignore` 的 `.env` 规则）：

```bash
cp deploy/.env.example deploy/.env
vi deploy/.env                                 # 把 REDIS_PASSWORD 改成强密码
```

在仓库根目录启动与验证：

```bash
docker compose -f deploy/docker-compose.redis.yml --env-file deploy/.env up -d
docker compose -f deploy/docker-compose.redis.yml --env-file deploy/.env ps
# 容器名固定为 bidding-redis，可直接用 docker 命令探活：
docker exec -it bidding-redis redis-cli -a 'Redis密码' PING   # 期望 PONG
```

compose 与手工命令产出同名资源（容器 `bidding-redis`、命名卷 `bidding-redis-data`），
因此第 8 节的 `docker logs`、`docker restart` 等运维命令两种方式通用。停止但保留数据：

```bash
docker compose -f deploy/docker-compose.redis.yml --env-file deploy/.env down
```

> 未设置 `REDIS_PASSWORD` 时 compose 会直接报错退出（`required variable ... is missing`），
> 不会启动无密码 Redis。CI 用 `docker compose config` 对该制品做语法与插值校验，不实际起容器。

### 4.2 等价手工 docker run（无 compose 时）

不想使用 compose 时，下面的命令与制品参数完全等价；**两种方式二选一，不要同时启动**
（容器名与宿主端口冲突）：

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

### 4.3 拉镜像与 docker daemon 代理排障

拉镜像报 `proxyconnect tcp: dial tcp 127.0.0.1:1081: connect: connection refused`，是 docker
daemon 配了本机代理而代理没在跑（常见于装过科学上网工具后残留）。排查：
`sudo systemctl cat docker | grep -i proxy`，删掉指向 `127.0.0.1:xxxx` 的
`Environment="HTTP_PROXY=..."`（对应 `/etc/systemd/system/docker.service.d/` 下的 conf），
或删 `daemon.json` 里的 `"proxies"` 段，再 `sudo systemctl daemon-reload && sudo systemctl restart docker`。
镜像源不可用时换阿里云容器镜像服务分配的专属加速地址。

### 4.4 无 Docker 时的用户态 Redis 回退

实在拉不下来镜像或 `docker ps` 无权限，可跳过 Docker：系统包装好 `redis-server` 后在
tmux 里以前台方式运行（`.env` 连接串保持不变）：

```bash
redis-server --port 6382 --bind 127.0.0.1 --requirepass '密码' --appendonly yes --dir ~/redis-data
```

## 5. 配置 .env

```bash
cp .env.example .env && chmod 600 .env
```

关键项：

```env
API_TOKEN=自定义复杂令牌                          # 与第 2 步 VITE_API_TOKEN 一致；留空不校验
CELERY_BROKER_URL=redis://:Redis密码@127.0.0.1:6382/0
CELERY_RESULT_BACKEND=redis://:Redis密码@127.0.0.1:6382/1
CELERY_WORKER_POOL=prefork                      # Linux 可用 prefork；Windows 必须 solo
DATABASE_URL=                                   # 留空则不启用 PostgreSQL（任务历史不持久化）
LOG_DIR=./logs                                  # 任务 JSONL 日志目录，trace 与 metrics 依赖它
DATA_RETENTION_DAYS=30                          # 0 表示不自动清理，配合 scripts/cleanup.py
```

**三个外部密钥必须填，否则解析、抽取、编写都会失败**（`.env.example` 里默认全部为空）：

| 键 | 缺失后果 | 获取地址 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 抽取与投标正文生成全部失败 | <https://console.volcengine.com/ark>（默认走火山方舟 OpenAI 兼容端点） |
| `EMBEDDING_API_KEY` | 无法建立检索索引，编写时检索为空、质量下降 | 同上 |
| `MINERU_API_KEY` | 云端解析不可用，自动回退 PyMuPDF 本地解析 | <https://mineru.net/apiManage/token>（有效期 90 天） |

若把 `DEEPSEEK_BASE_URL` 改回 `https://api.deepseek.com`，第 3 步的连通性检查需要
同时放通该域名。另外确认 `upload/`、`chroma_db/`、`logs/` 三个目录存在且进程可写
（它们都按仓库根目录锚定，不依赖当前工作目录）。

## 6. 启动应用（tmux）

```bash
tmux new -s bidding
uv run uvicorn api.main:app --host 0.0.0.0 --port 8001
# Ctrl+B " 分屏后：
uv run celery -A src.celery_app worker --loglevel=info
# Ctrl+B d 脱离会话；tmux attach -t bidding 重新进入
```

- **代码更新后 API 和 worker 都要重启**：worker 只在启动时注册任务，漏重启会出现
  「接口返回 202，任务一直 pending」。
- Chroma 的客户端是**进程内单例**，prefork 的多个子进程会各自持有连接、同时写同一个
  索引目录可能触发租户错误。单机部署建议 `celery ... --concurrency=1`，或直接
  `--pool=solo`（与本机一致，最稳）。

## 7. 验证

```bash
curl localhost:8001/api/health        # 健康检查（完整 6 字段见 docs/api-contract.md §7.2）
curl localhost:8001/api/documents     # 401（令牌生效）
curl -H 'X-API-Token: 自定义复杂令牌' localhost:8001/api/documents   # 200
curl -s localhost:8001/ | head -1     # <!doctype html>
```

`/api/health` 实际返回 6 个字段：`status`、`version`、`task_backend`、`broker_ok`、
`worker_ok`、`queue_depth`（完整 schema 见 `docs/api-contract.md` §7.2）。
`worker_ok` 在 worker 刚启动的十几秒内可能短暂为 `false`，属正常；持续 `false`
说明 worker 没起或连不上 Redis。密钥是否可用用一次真实探测确认（会发起一次嵌入调用）：

```bash
uv run python -m src.cli doctor --probe-embeddings   # 输出维度 2048 才算嵌入配置正确
```

浏览器打开后建议再手工确认两条前端链路：「文档库 → 抽取结果」能编辑保存并显示
已核对徽章，「编写标书」能提交并轮询到产物。

浏览器访问 `http://服务器IP:8001`。打不开说明防火墙未放行 8001，可联系管理员开放；
桌面端 App 直接请求该地址，无需 SSH 隧道。

## 8. 运维与回退

```bash
# Redis 容器（compose 与手工 docker run 产出的容器同名，命令通用）
docker logs -f bidding-redis
docker restart bidding-redis
tmux attach -t bidding           # 查看 API / worker
```

`docker ps` 无权限且未采用第 4.4 节前台方案时，也可用 conda 安装用户态 Redis 并
守护进程化，`.env` 连接串不变：

```bash
conda install -c conda-forge redis -y
redis-server --port 6382 --bind 127.0.0.1 --requirepass 'Redis密码' \
  --daemonize yes --logdir ~/group_2 --logfile redis.log
```