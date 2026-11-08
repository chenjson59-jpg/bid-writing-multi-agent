# 规格：LLM 与 MinerU 调用的自动重试/退避机制（T-026）

> 状态：已定稿（2026-11-08，用户确认五个决策点全部按推荐方案执行）。基线代码：
> main `8850c45`（openai 3.35.1、httpx 0.28.1）。

## 1. 用户场景与业务目标

解析、抽取、编写链路依赖三个外部服务：MinerU 云端解析、火山方舟 DeepSeek 文本模型、
火山方舟 Embedding。网络抖动、429 限流与服务端 5xx 属于瞬时故障，当前行为不对称：

- Embedding 已显式配置 SDK 级重试（`EMBEDDING_MAX_RETRIES=10`）；
- 文本 LLM 隐式继承 openai SDK 默认重试，但次数不可配置、代码未显式声明；
- MinerU 四个 httpx 调用零传输重试，任何一次瞬时错误都会立即终结云端解析机会，
  `auto` 模式直接降级本地 PyMuPDF（解析质量下降），`cloud/strict` 模式直接任务失败。

目标：

1. MinerU 传输层统一获得「分类明确、次数与退避可配置、可离线故障注入测试」的重试，
   重试发生在 `mineru_client` 内部、pipeline 降级判断之前，瞬时抖动不再浪费云端解析；
2. 文本 LLM 的 SDK 重试次数显式配置化，消除隐式默认值，同时杜绝自建包装层与
   SDK 内建重试叠加形成的双层重试；
3. 异常层级、CLI 退出码、降级链路、eval 冻结基线、对外 API 契约全部不变。

## 2. 事实复核结论（对任务卡背景的纠正）

任务卡背景称「当前文本 LLM 调用无自动重试」。**该表述与代码事实不符**，开工复核如下：

| # | 复核项 | 代码事实（8850c45） | 结论 |
|---|---|---|---|
| 1 | `DeepSeekModelCaller`（`src/extraction/extractor.py:622-660`） | 构造 `OpenAI(...)` 未传 `max_retries`；实测 openai 3.35.1 构造默认 `max_retries=2`。SDK `_should_retry` 对 408/409/429/≥500 及连接错误/超时重试，其余 4xx 不重试；退避 `0.5*2^n`（上限 8s，0-25% 抖动），尊重 `Retry-After`（上限 120s） | 文本 LLM **已有隐式 SDK 重试**，缺口是「未显式声明、不可配置」，而非「无重试」 |
| 2 | `src/vectorstore/embedding.py:47` | 显式 `max_retries=settings.embedding_max_retries`（默认 10） | 任务卡所述属实，本任务不改动 |
| 3 | `src/parsing/mineru_client.py` | `request_upload_urls`(POST)/`_upload_file`(PUT)/`_poll_batch`(GET)/`_download`(GET) 均裸调 httpx 后 `raise_for_status()`；`_poll_batch` 循环只处理业务 `state`，不处理传输错误 | MinerU **确实零传输重试**，是本任务主要缺口 |
| 4 | `src/pipeline.py:177-185` `_do_parse` | 捕获云端异常：`cloud/strict` 包 `ParseError` 抛出；`auto` 回退本地并写 warning | 重试必须内置于 mineru_client，耗尽后异常语义不变，降级链路自然保持 |
| 5 | `extractor._extract_unit` | 空结果重试一次 + 截断二分递归，作用于模型**内容语义**，与 HTTP 传输无关 | 属既有内容级容错，本任务不混淆、不改动 |
| 6 | httpx 0.28.1 异常层级（实测 MRO） | `TimeoutException → TransportError → RequestError → HTTPError`；`HTTPStatusError` 与 `RequestError` 并列，不同支 | 捕获 `TransportError` 即覆盖全部超时与连接类错误；状态码错误走 `HTTPStatusError` 分支 |
| 7 | 可复用模式 | `UsageMeter/MeteredCaller` 装饰 `ModelCaller`；测试侧 `_TruncatingCaller` 等脚本化 Fake；`parse_cloud`/`request_upload_urls`/`_poll_batch` 支持注入 `httpx.Client` | 故障注入可用 `httpx.MockTransport` 挂真实 Client，零网络 |
| 8 | 日志体系 | 全仓标准库 `logging.getLogger(__name__)`（如 `src/observability.py`、`src/db/repository.py`），无 structlog | 重试事件沿用标准库 logging，不新造观测体系 |
| 9 | 配置门禁 | `tests/test_config.py` 强制 `.env.example` 与 `Settings` 字段双向一一对应 | 新增配置必须三处同步并有行为测试，否则测试直接红 |
| 10 | 现有 MinerU 测试 | 不存在 `test_mineru*`；全 tests 无 httpx 使用；`_do_parse` 的 auto 降级路径当前无直接测试 | 本任务新增 `tests/test_mineru_client.py`，并补降级回归测试 |

## 3. 输入、输出与副作用

- 新增三个配置项（均有代码读取、默认值与现行为等价或在第 4 节显式说明变化）：

| 配置键 | Settings 字段 | 默认 | 读取位置 |
|---|---|---|---|
| `LLM_MAX_RETRIES` | `llm_max_retries: int` | `2` | `DeepSeekModelCaller` 构造显式传入 `OpenAI(max_retries=...)` |
| `MINERU_MAX_RETRIES` | `mineru_max_retries: int` | `3` | mineru_client 重试策略（含义：**重试次数**，总尝试 = 次数 + 1；`0` 关闭重试，行为等同现状） |
| `MINERU_RETRY_BACKOFF_BASE_SECONDS` | `mineru_retry_backoff_base_seconds: float` | `1.0` | mineru_client 退避基数 |

- 对外 HTTP API、请求/响应 Schema、任务状态协议、数据库结构、文件产物结构：**零变化**。
- 异常类型与消息语义：重试耗尽时裸 `raise` 最后一个异常，保持原类型/消息/traceback；
  `pipeline._do_parse` 的捕获与包装逻辑不改，CLI 退出码（4 解析失败等）不变。
- 副作用面：仅多产生若干次等价的外部 HTTP 请求与 warning 日志；不新增依赖
  （只用 httpx 既有异常体系与标准库 `logging`/`random`），`pyproject.toml`/`uv.lock` 零变更。

## 4. 决策点（已确认：全部按推荐方案）

### 4.1 决策点 1：LLM 侧重试策略——推荐方案 A（配置化 SDK 内置重试）

- **方案 A（推荐）**：新增 `LLM_MAX_RETRIES`（默认 2 = SDK 当前默认），在
  `DeepSeekModelCaller` 构造中显式传 `max_retries=settings.llm_max_retries`，与
  `EMBEDDING_MAX_RETRIES` 完全对称；重试分类、退避、`Retry-After` 处理全部沿用
  openai SDK 已验证实现（429/5xx/408/409/超时/连接错误可重试，4xx 鉴权类不重试）。
  - 收益：零新增包装层；默认值 2 与现状逐字节等价，无行为漂移；测试只需断言
    构造参数（monkeypatch `openai.OpenAI` 记录 kwargs），离线确定性。
  - 代价：分类逻辑不自控，但 SDK 分类恰是本项目需要的标准语义，自控无业务收益。
- 方案 B（不推荐）：自建 `RetryingCaller` 包装层并把 SDK `max_retries=0`。
  - 问题：重写 SDK 已验证的分类/退避；`MeteredCaller` 等所有 caller 接触面多一层；
    一旦某处漏设 0 即形成双层重试，调用次数与退避时间相乘放大；收益（完全自控）
    在本项目没有对应需求。**除非用户有明确的自定义分类需求，否则不采纳。**

### 4.2 决策点 2：MinerU 重试范围与错误分类

四个 httpx 调用（POST 申请链接、PUT 上传、GET 轮询、GET 下载）统一经一个内部
重试辅助函数包装，分类表：

| 分类 | 具体错误 | 处理 | 依据 |
|---|---|---|---|
| 可重试 | `HTTPStatusError` 且状态码 429 或 ≥500 | 退避后重试 | 限流/服务端临时故障 |
| 可重试 | `httpx.TransportError`（含 `ConnectTimeout`/`ReadTimeout`/`WriteTimeout`/`PoolTimeout`/`ConnectError`/`NetworkError`/`ProtocolError` 等） | 退避后重试 | 实测 `TimeoutException` 是 `TransportError` 子类，一次捕获全覆盖 |
| 不可重试 | 其余 `HTTPStatusError`（4xx，尤其 401/403 鉴权、400、404） | 立即原样抛出 | 重试不会改变鉴权/参数错误 |
| 不可重试 | 2xx 下的业务错误：`batch_id`/urls 缺失、URL 数量不符、`state=failed`、缺 `full_zip_url`、zip 越界、（现状下）`extract_result` 为空 | 立即 `ParseError`，不进入传输重试 | 业务语义错误，重试无意义 |
| 耗尽 | 可重试错误达到 `MINERU_MAX_RETRIES` 后仍失败 | 裸 `raise` 最后一个异常 | 类型/消息/traceback 不变，降级判断与退出码不变 |

四个调用的幂等性结论（写入规格）：

- **GET 轮询/下载**：天然幂等，重试安全。
- **PUT 预签名 URL 上传**：HTTP PUT 为替换语义，重试使用同一 URL、同一份文件字节，
  即使首次请求已被服务端接收，重复 PUT 也是用相同内容覆盖，幂等安全；预签名 URL
  若过期会返回 403，属不可重试错误，会按原语义立即失败（且重试窗口秒级，远小于
  预签名有效期，实际不会触发）。
- **POST 申请上传链接**：非严格幂等。仅在「未收到响应」类错误（超时/连接中断/5xx）
  时重试，理论上服务端可能已建单，产生一个会自行过期的孤儿 batch，无数据损坏、
  不影响本次解析（只有确认 200 返回的 `batch_id` 才会被后续使用）；相较整单云端
  解析失败降级，该代价可接受。

### 4.3 决策点 3：退避算法与时间预算

- 公式（第 n 次重试，n 从 0 开始）：
  `delay_n = base * 2**n + random() * base`，其中 `base = MINERU_RETRY_BACKOFF_BASE_SECONDS`，
  `random()` 为 [0,1) 均匀抖动；默认 base=1、max_retries=3 时，三次睡眠约
  [1,2)、[2,3)、[4,5) 秒，单调用额外等待上限约 10 秒。
- 抖动函数与 sleep 函数经策略对象注入（默认 `random.random` / `time.sleep`），
  测试注入固定 rng（如恒返回 0）与 Fake sleep，断言精确退避序列且零真实等待。
- **Retry-After（子项，推荐采纳）**：429/503 响应携带可解析的 `Retry-After`
  （delta-seconds 或 HTTP 日期）且 `0 < 值 ≤ 120` 时，当次睡眠改用该值；
  不可解析或超 120 秒则回退公式。120 秒与 openai SDK 的 `MAX_RETRY_AFTER_DELAY`
  对齐，作为固定安全上限写死并注释，不新增配置项（避免摆设配置）。
- 与既有超时的关系：`MINERU_TIMEOUT=180`/`MINERU_DOWNLOAD_TIMEOUT=300` 是
  **单次 httpx 请求**超时，不是全局截止时间；启用重试后单调用最坏耗时 =
  `(max_retries+1) × 单次超时 + 退避总和`。轮询循环总体仍受
  `MINERU_MAX_POLLS × MINERU_POLL_INTERVAL` 约束（重试发生在单次 poll 内部，
  不增加 poll 计数）。不引入全局 deadline 配置。
- `MINERU_MAX_RETRIES=0` 时完全不重试、不 sleep，与当前代码行为等价（逃生开关）。

### 4.4 决策点 4：可观测性——推荐仅标准库 logging，不进 ParseStage.warnings

- 在 `mineru_client` 建 `logger = logging.getLogger(__name__)`，每次重试打
  `warning`：操作名（POST/PUT/GET-poll/GET-download）、第几次/上限、异常类型，
  `HTTPStatusError` 附状态码、Transport 异常附类名。
- **不**把单次重试写入 `ParseStage.warnings`/job trace：重试是可恢复的过程事件，
  逐条进任务产物会制造噪声，且需要把收集器穿过 client 层，改动面过大。
- 最终失败的可观测性维持现状：auto 降级 warning「云端解析失败，回退本地解析：…」
  与 cloud/strict 的 `ParseError` 依旧进入 stage warnings / job trace（error_type、
  error 文案不变），失败前的重试细节可在进程日志按 logger 名检索。
- 不新造观测体系，不引入 structlog。

### 4.5 决策点 5：`_poll_batch` 空 `extract_result` 是否容错——推荐本任务不扩大范围

- 现状（`mineru_client.py:79-80`）：200 响应但 `extract_result` 为空 → 立即
  `ParseError("MinerU 批量结果为空")`。
- 方案 (a)（推荐）：**维持现状**。空列表是 2xx 业务响应，不是传输错误；没有证据表明
  MinerU 会短暂返回空后自愈，把它当瞬时故障会在「永久无效 batch」场景平白等待
  最长 `MAX_POLLS × POLL_INTERVAL`（默认 10 分钟）才失败，丧失快速失败特性。
  本任务边界锁定传输层重试，内容级容错另案处理。
- 方案 (b)（备选，不推荐本任务做）：把空结果视为未就绪，sleep 一个 poll 间隔后继续，
  受 `MAX_POLLS` 总预算约束，并区分「始终为空→批量结果为空」与「曾有结果后停滞→
  解析超时」两套终态文案。若后续生产环境出现真实空轮询案例，再单独立项、单独测试，
  且不得改变 `state=failed` 终态处理。

## 5. 正常、异常与边界路径

### 5.1 正常路径

- MinerU 首次调用成功：不 sleep、不打 warning，行为与现状完全一致；
- 前 N 次可重试错误、随后成功（N ≤ max_retries）：共 N+1 次尝试、N 次退避睡眠，
  返回值与无重试时一致，调用方无感知；
- LLM 正常调用：SDK 内置行为不变，仅 `max_retries` 由隐式 2 变为显式配置值 2。

### 5.2 异常路径

- 不可重试 4xx：一次请求即抛出原始 `httpx.HTTPStatusError`，零 sleep；
- 可重试错误耗尽：裸抛最后一个异常；`_do_parse` 按原逻辑 auto 降级 / cloud 抛
  `ParseError`，退出码 4 不变；
- 2xx 业务错误：`ParseError` 立即抛出，不消耗重试预算；
- LLM 401/403 等：SDK 不重试直接抛其原生异常，由既有抽取/编写异常处理承接，不变。

### 5.3 边界路径

- `MINERU_MAX_RETRIES=0`：无重试、无睡眠，与 8850c45 行为等价；
- `LLM_MAX_RETRIES=0`：显式关闭 SDK 重试（SDK 支持 0），用于排查/限流严格场景；
- 抖动注入固定值时退避序列必须精确可断言；`Retry-After` 非法/超限回退公式；
- 分片上传多个文件时，重试预算按「每个调用独立」计算，不跨调用共享；
- 轮询内单次 GET 的重试不改变 `mineru_max_polls` 计数，不改变终态 state 判定。

## 6. 安全、兼容与影响分析

- **安全**：不触碰密钥、路径校验、解压安全（`_safe_extract` 不改）；重试不新增
  header、不记录 Authorization（日志只记异常类型与状态码）；无公网 IP/令牌入库。
- **降级链路**：重试内置于 mineru_client，对 `pipeline._do_parse` 透明；补充
  auto 降级与 cloud 失败的回归测试锁定该行为。
- **eval 基线**：两个 eval runner 全程 Fake 模型、不触达 mineru_client 与真实
  OpenAI 构造；改动不影响 Fake 路径，冻结基线预期不变，仍按门禁实跑两份 runner。
- **配置兼容**：旧 `.env` 不含新键时取默认值；旧代码（回滚后）遇到新键因
  `extra="ignore"` 直接忽略，向前向后均安全。
- **数据库/API/产物**：无结构变化，无迁移、无 Schema 改动。
- **依赖**：零新增依赖，`uv.lock`/`pyproject.toml` 零 diff；全部 uv 命令内置
  `$env:UV_DEFAULT_INDEX='https://pypi.org/simple'` 并 `--frozen`，若锁文件被改写
  立即 `git checkout` 还原。

## 7. TDD 测试清单（全部离线、零真实 sleep）

新增 `tests/test_mineru_client.py`，以 `httpx.MockTransport` + 真实 `httpx.Client`
脚本化故障注入：

1. POST 连续两次 503 后 200：成功，断言 3 次尝试、2 次 Fake sleep、退避序列
   `[base, 2base]`（rng 恒 0）；
2. POST 429 携带 `Retry-After: 3`：当次 sleep=3 而非公式值；非法/超限头回退公式；
3. GET 轮询连续抛 `httpx.ReadTimeout` 后成功：传输异常可重试且最终成功；
4. PUT 上传先 500 后 200：成功（幂等重试场景）；
5. GET 下载 500 持续失败：尝试 4 次（max_retries=3）、sleep 3 次后裸抛
   `HTTPStatusError`，异常消息与单次失败一致；
6. 401/403/400 参数化：各仅 1 次尝试、零 sleep、原异常抛出；
7. 2xx 业务错误（缺 batch_id、state=failed、zip 越界）：立即 `ParseError`、零重试；
8. `mineru_max_retries=0`：一次尝试即抛、零 sleep，锁定与现状等价；
9. 退避公式单测：Fake rng 返回固定值，断言 `base*2**n + jitter` 逐次精确值。

`tests/test_extraction.py` 增加 LLM 侧用例：

10. monkeypatch `openai.OpenAI` 为记录 kwargs 的 Fake，分别在
    `llm_max_retries` 默认/自定义/0 下实例化 `DeepSeekModelCaller`，断言显式传入
    的 `max_retries` 与配置一致；默认值断言为 2（与 SDK 默认等价的回归锁）。

`tests/test_pipeline.py` 增加降级回归：

11. monkeypatch `pipeline.parse_cloud` 抛异常：`parser="auto"` 走本地解析且
    `stage.warnings` 含回退文案；`parser="cloud"` 抛 `ParseError`（退出码语义不变）。

`tests/test_config.py` 既有双向键对应测试自动覆盖新配置；新增三个字段默认值断言。

## 8. 文档同步清单

- `.env.example`：三个新键带注释，与 `Settings` 字段双向对应；
- `README.md`：设计要点/配置相关位置补重试策略说明（可重试分类、配置键、默认行为）；
- `docs/api-contract.md` §12 配置规范：云端解析/模型服务分组补三个键；
- `docs/deployment.md`：`.env` 配置说明补重试与退避键含义；
- `docs/tech-decisions.md`：技术债表「文本 LLM/MinerU 无自动重试」行标记解决；
  云端解析配置表补新键；新增 ADR-015（传输重试策略：SDK 配置化 + MinerU 分类退避，
  含方案 B 被否理由与 PUT/POST 幂等性结论）；
- `docs/algorithms.md`：修正 6.4.1「文本 LLM 调用层没有自动指数退避」的过时表述；
  参数表与伪代码中遗留的 `MAX_RETRIES=2/RETRY_BACKOFF=2.0`（T-010 已删配置的
  陈旧描述）改为真实配置键与默认值；
- `docs/architecture.md` 17.3：超时与重试按最终实现改写。

## 9. 验收标准

1. 五个决策点有明确结论并经用户确认；
2. MinerU 四类调用统一传输重试，分类表逐项有故障注入测试；LLM 重试显式配置化且
   有构造参数断言测试；auto 降级与 cloud 失败语义有回归测试；
3. `uv run ruff check api src tests scripts eval`、`uv run pytest -q`
   （361 基线 + 新增用例全绿）、两个 eval runner 退出码 0；
4. `git status` 确认 `uv.lock`/`pyproject.toml` 零 diff、无密钥/公网 IP；
5. 前端无改动，书面说明跳过 npm 验证；
6. 异常层级、CLI 退出码、API 契约、eval 冻结基线不变；
7. 分支 `codex/t026-retry-backoff` 提交（中文信息带 T-026）并推送，停住等用户开 PR；
8. 本地 `任务清单.md` 完成记录与 `工作日志.md` 倒序条目更新（两文件 gitignore 不入库）。

## 10. 回滚方式

- 纯增量提交、无数据库/API/产物结构变更：直接 revert 该分支合并提交即可完全回滚；
- 回滚后 `.env` 中遗留的新键被 `Settings(extra="ignore")` 忽略，无启动失败风险；
- 回滚后 MinerU 恢复零重试、LLM 恢复 SDK 隐式默认 2 次，即 8850c45 行为。