# 规格：编写/解析链路进度回调补点（T-032）

## 1. 用户场景与业务目标

用户在前端提交标书编写任务后，底部任务栏的进度小字长期停留在「规划章节」，直到任务
终态才一次性消失，运行期无法判断任务处于检索、章节写作、评审修订、汇总还是 Word 导出
阶段。复核确认根因在后端：`src/tasks.py::run_bid_task` 仅在开头 `report("规划章节")`
一次，`run_bid_writer`（`src/pipeline.py`）与 `run_write_pipeline`
（`src/bid_writer/graph.py`）均不接收进度回调，章节扇出全程静默；解析链路
（`run_parse_task`）也只有「解析文档 / 结构化抽取 / 建立检索索引」3 条不带任何量化信息
的粗粒度消息。

业务目标：

1. bid 任务运行期 `JobView.progress` 覆盖规划完成、检索、逐章完成（含修订汇总）、
   汇总、Word 导出（含失败降级）各阶段，消息顺序与真实执行顺序一致；
2. parse 任务在既有 3 条消息基础上补入页数、评分因素数、入库片段数等已有返回值可携带
   的信息，不新增任何外部调用；
3. 进度回调为可选依赖注入：`report=None` 时与现状逐字节等价，eval 冻结基线、CLI、
   既有调用方零改动；
4. 线程池章节扇出下消息严格按 `1/N … N/N` 聚合，无重复、无跳跃、无多线程交叉；
5. 不改 HTTP 路由、请求/响应 Schema、`JobView` 结构、数据库与任何前端文件，progress
   仍是既有 `string[]` 字段，仅消息内容与数量变化。

## 2. 现状复核（开工前代码结论）

1. `ProgressReporter`（`src/tasks.py`）自带 0.5s 节流与最近 50 条快照，`report()` 内部
   先全量追加 `_messages`，再按节流窗口决定是否 flush 到 sink；节流只影响推送频率，
   不丢失快照记录，sink 异常被捕获且不阻断主任务。
2. Celery 入口（`write_bid` / `parse_document`）与 `LocalBackend._execute`
   （`api/dispatch.py`）都调用同一组核心函数 `run_bid_task` / `run_parse_task`，并传入
   同一个 `reporter.report`。因此核心函数内补的进度点对两后端天然一致，本任务不改
   `api/dispatch.py`。
3. 调用链：`run_bid_task → run_bid_writer`（内部 `plan_sections →
   run_write_pipeline[串行检索 + ThreadPoolExecutor 章节扇出 + as_completed] →
   assemble_markdown/build_report → 落盘 → markdown_to_docx`）。`section_agent`
   （`src/bid_writer/agents.py`）是单章写作—评审—修订循环，当前无 report 参数。
4. `run_supplement`（`src/bid_writer/supplement.py`）与 `rewrite_sections`
   （`src/bid_writer/sections.py`）均在各自的串行 for 循环中直接调用 `section_agent`，
   **不复用** `run_write_pipeline`，本期不接入细粒度回调（见第 8 节遗留结论）。
5. `run_bid_writer` 内的 `stage("规划章节") / stage("章节写作") / stage("合并与导出")`
   是 observability 轨迹打点，`tests/test_observability.py` 对这三个阶段名有精确断言，
   本任务全部保留不动；进度消息与轨迹阶段是两套机制。

## 3. 进度消息契约

### 3.1 bid 编写链路消息清单与顺序

`run_bid_task` 保留开头既有的 `规划章节` 消息不变；`run_bid_writer` 与
`run_write_pipeline` 新增可选 `report` 注入后，一次成功且默认导出 Word 的 bid 任务，
progress 严格按下列顺序追加：

| 序号 | 消息文案 | 产生位置 | 触发条件 |
| --- | --- | --- | --- |
| 1 | `规划章节` | `run_bid_task`（既有，保留） | 总是 |
| 2 | `规划完成：共 N 章` | `run_bid_writer`，`plan_sections` 返回后 | 总是，N 为规划章节数 |
| 3 | `章节检索` | `run_write_pipeline`，串行检索循环前 | 总是（单条，不按章节重复） |
| 4 | `章节完成 i/N` 或 `章节完成 i/N（含 R 次修订）` | `run_write_pipeline` 的 `as_completed` 消费循环 | 每个 future 完成一条，i 从 1 递增到 N；`draft.revisions > 0` 时使用修订后缀 |
| 5 | `汇总标书` | `run_bid_writer`，`assemble_markdown` 前 | 总是 |
| 6 | `导出 Word` | `run_bid_writer`，`markdown_to_docx` 调用前 | 仅 `write_options.export_word=True` |
| 7 | `Word 导出失败，已保留 Markdown 与报告` | 同上 try/except 的 except 分支 | 仅 Word 导出抛异常时 |

约束：

- 序号 4 的 N 与序号 2 的 N 为同一个 `len(plans)`；占位章节（`placeholder` 策略）不检索、
  不调模型，但同样占一个完成名额，产出一条 `章节完成 i/N`（`revisions=0`，无后缀）。
- 每条 `章节完成` 隐含该章 Writer→Reviewer 闭环已结束（占位章为确定性 100 分评审）；
  章节内部的评审/修订不单独打消息，修订次数在完成消息中以后缀汇总，避免多线程消息交叉。
- `export_word=False` 时序号 6、7 均不产生，其余消息不变。
- 序号 7 与既有降级语义并存：except 分支继续执行
  `report.warnings.append(f"Word 导出失败：{exc}")`，函数正常返回，「导出失败不阻断
  主流程」的语义与返回结构不变。

### 3.2 parse 解析链路消息清单

`run_parse_task` 的三个条件分支结构不变，仅把消息移到对应阶段拿到返回值之后，补入
量化信息，全部数据来自既有返回值，不新增外部调用：

| 阶段 | 消息文案 | 数据来源 |
| --- | --- | --- |
| 解析 | `解析文档（P 页）` | `run_parse` 返回的 `doc.pages` |
| 抽取 | `结构化抽取（评分因素 F 项）` | `run_extraction` 返回的 `len(extraction.evaluation_factors)`，仅 `options["extract"]` 为真时产生 |
| 入库 | `建立检索索引（片段 T 条，新增 A）` | `ingest_document(...).to_dict()` 的 `total` 与 `added`，仅 `options["ingest"]` 为真时产生 |

未开启 extract / ingest 时对应消息不产生，与现状条件分支一致。消息从阶段调用前移到
调用后，是携带页数/计数的前提；LocalBackend 在执行核心函数前已把任务置为 running，
Celery 入口同样先建立 reporter，故运行态可见性不受影响。

为不丢失失败阶段定位（AGENTS.md 第 6 节：任务失败必须能通过 job trace 定位阶段），
每个阶段在调用前另发一条稳定名称的 `stage("解析文档"/"结构化抽取"/"建立检索索引")`
轨迹打点，阶段成功后再发参数化 `report(...)` 进度消息。阶段中途抛异常时，trace 的
最后阶段仍是稳定入口名，`test_job_trace_locates_failure` 既有用例语义保持不变；
成功路径上参数化消息会经 `ProgressReporter.report` 内部的 `stage()` 形成更细的轨迹阶段。

### 3.3 文案格式论证

1. 全部消息为中文短字符串，前缀稳定（`规划完成：`、`章节完成 `、`解析文档（` 等），
   测试可按前缀与精确文案断言，前端按字符串原样展示，无需解析协议。
2. 完成消息只带聚合计数 `i/N` 与修订数 R，不带章节标题：标题长度不一且完成顺序由线程
   调度决定，带标题会引入不可重复的消息序列；聚合计数在主线程生成，顺序可严格断言。
3. 修订后缀采用条件追加（R>0 才出现），把无修订的常见路径噪音降到最低，同时保留
   「评审/修订」阶段的可观测性。
4. 数字统一使用半角阿拉伯数字，括号统一使用全角中文括号，与既有中文文案风格一致。
5. 消息可能超过 `ProgressReporter` 的 50 条快照窗口（章节数很多时），窗口截断语义保持
   不变；前端 T-033 已修复滚动窗口下的判等冻结，本任务不调整窗口大小与节流参数。

## 4. 回调注入与 `report=None` 兼容语义

1. 新增参数命名统一为 `report: Callable[[str], None] | None = None`，且一律放在
   keyword-only 段：
   - `run_write_pipeline(..., *, max_concurrency, schema=None, report=None)`；
   - `run_bid_writer(doc_id, *, profile=None, options=None, caller=None, report=None)`。
2. 两个模块各定义模块级 no-op 常量（如 `_NOOP_REPORT`），函数入口统一
   `report = report or _NOOP_REPORT`。不使用内联 lambda：模块常量可被测试引用比较、
   避免每个调用点新建闭包，也规避默认参数可调用对象的 ruff 告警类别。
3. `section_agent` 不增加 report 参数：修订次数可从返回的 `SectionDraft.revisions`
   在主线程读取，worker 线程零打点即可满足需求，侵入面最小。
4. `report=None`（含完全不传）路径除回调为空操作外，不引入任何新的条件分支、不改变
   返回值、落盘产物、模型调用次数与异常传播。eval 两个 runner 与 `src/cli/write.py`
   均不传 report，冻结基线必须零漂移（第 10 节门禁）。
5. `run_bid_task` / `run_parse_task` 的 report 仍是必填位置参数（两后端恒传
   `reporter.report`），签名不变；`run_bid_task` 内部把该回调原样透传给
   `run_bid_writer`。

## 5. 线程安全聚合方式

1. 章节并发模型不变：`ThreadPoolExecutor` + `as_completed`，worker 数仍取
   `settings.bid_writer_max_concurrency`（`max(1, …)`）。
2. worker 线程执行的 `section_agent` 不接收、不调用任何 report；线程内零消息副作用。
3. 进度聚合只允许发生在提交方主线程的 `as_completed` 消费循环：维护本地整数计数器
   `completed`，每消费一个 future 递增一次并发送一条 `章节完成 completed/N`。计数器为
   单线程变量，无共享写入、无锁即线程安全；future 实际完成顺序任意，但消息顺序恒为
   `1/N, 2/N, …, N/N`。
4. 草稿列表仍在循环结束后按 `plan.order` 排序返回，产物顺序与消息顺序解耦，既有行为
   不变。
5. 串行检索与汇总/导出阶段本来就运行在主线程，其消息天然无交叉。

## 6. Celery / local 两后端一致性

1. 两后端都经 `run_bid_task` / `run_parse_task` 复用同一核心逻辑与同一
   `ProgressReporter.report`，新增进度点位于核心函数内部，两后端得到的消息序列一致，
   无需修改 `api/dispatch.py`、Celery 任务包装或 JobView 映射。
2. `JobView.progress` 字段类型（`string[]`）、终态结果中的 `progress` 快照、50 条窗口
   与 0.5s 节流全部不变；本任务只改变消息内容与数量。
3. 测试通过直接向核心函数注入 `list.append` 形态的 Fake reporter 锁定消息契约，该契约
   与具体后端无关，故无需分别为 Celery / local 重复断言。

## 7. 异常与边界

1. **report 回调异常**：生产路径注入的是 `ProgressReporter.report`，其 sink 异常已被
   `_flush` 捕获、`stage()` 在无任务上下文时为空操作，不会向主链路抛异常；核心函数不
   再额外包裹 try/except，保持与现状直接调用 report 一致的异常边界。
2. **空规划**：`plan_sections` 在大纲为空时已抛 `BidWriteError`，正常不会以空 plans
   进入 `run_write_pipeline`；若直接单测该函数并传入空列表，行为定义为：发送
   `章节检索`，无章节完成消息，返回空列表，不抛异常。
3. **占位章节**：不触发检索与模型调用，仍产生一条无修订后缀的完成消息。
4. **Word 导出失败**：先发 `导出 Word`，异常时再发显式失败消息；Markdown、outline、
   sections、report.json 落盘与成功返回不受影响，warning 文案保持
   `Word 导出失败：{exc}` 不变。
5. **`export_word=False`**：不产生任何导出相关消息，也不创建 docx。
6. **extract / ingest 未开启**：parse 对应阶段消息缺失，属预期而非异常。
7. **50 条窗口**：消息总量超出窗口时只保留最近 50 条，早期消息在终态快照中被截断，
   与既有 `ProgressReporter` 语义一致；本任务不改窗口。
8. **进度消息与 observability 轨迹**：两套机制并行——`stage()` 负责稳定的阶段入口与
   失败定位（parse 三阶段在调用前显式打点，bid 三阶段沿用 `run_bid_writer` 既有
   `stage()` 调用，名称不变），`report()` 负责面向 JobView 的参数化进度消息，其内部
   仍会以消息名调用 `stage()`，在成功路径追加更细的轨迹阶段；除 parse 失败定位用例
   锁定的稳定入口名外，无测试或接口对动态阶段名做精确匹配。

## 8. supplement / rewrite 范围结论（遗留）

复核确认 `run_supplement` 与 `rewrite_sections` 不复用 `run_write_pipeline`，而是各自
串行直接调用 `section_agent`。本期范围限定为 bid 主链路与 parse 链路，不对这两个函数
追加回调参数，理由：

1. 二者是独立任务种类（`supplement` / `rewrite`），各自的任务包装
   （`run_supplement_task` / `run_section_rewrite_task`）当前只发一条起始消息，补点需要
   独立设计消息契约与测试，擅自扩大范围会触碰本任务门禁之外的产物；
2. 二者为串行循环，不存在多线程聚合问题，后续可直接以本规格的文案与 no-op 注入模式
   复用实现。

遗留安排：在 `任务清单.md` T-032 完成记录与本规格中显式登记，后续如需补点另立任务，
本任务不修改 `src/bid_writer/supplement.py`、`src/bid_writer/sections.py` 的函数签名。

## 9. 测试策略（TDD，全部离线）

新增 `tests/test_progress_callbacks.py`（扁平 `test_*.py` 结构），Fake reporter 统一为
`list.append`，禁止真实 LLM / Embedding / Chroma / 网络、禁止 sleep 与概率断言：

1. `run_write_pipeline` 单元级（内存对象 + ScriptCaller + 假检索）：
   - 占位章 + 生成章混合，断言消息序列为 `章节检索` 后严格 `章节完成 1/N … N/N`，
     计数无重复无跳跃；
   - 评审低分触发修订的章节，完成消息精确为
     `章节完成 i/N（含 R 次修订）`；
   - `max_concurrency>1` 并以线程事件屏障制造真实并发，断言 `1/N … N/N` 序列不变；
   - `report=None` 与完全不传 report 两次运行的草稿结果逐字段一致且不抛异常；
   - 空 plans 边界：仅 `章节检索` 一条、返回空列表。
2. `run_bid_writer` 集成级（本地 pymupdf 解析 + 假抽取 caller + 假写作 caller +
   空检索器，产物目录重定向 tmp_path）：
   - 断言第 3.1 节完整消息序列与相对顺序，完成消息条数等于报告章节数；
   - monkeypatch `markdown_to_docx` 抛错：断言出现 `导出 Word` 与显式失败消息、
     函数成功返回、warning 与 Markdown 产物仍在；
   - `export_word=False`：断言无导出消息；
   - `report=None` 与不传 report 两次运行的 Markdown / outline / 章节正文等价。
3. `run_bid_task` 透传级：monkeypatch `tasks.run_bid_writer` 捕获 report 实参，断言
   起始 `规划章节` 保留且同一回调被透传。
4. `run_parse_task` 级：monkeypatch `run_parse` / `run_extraction` /
   `ingest_document` 为内存假对象，断言三条参数化消息精确文案；再断言关闭 extract /
   ingest 时对应消息缺失。

红阶段要求：新增测试在旧实现下因「回调未被调用 / 消息缺失」失败，不得因导入错误或夹具
问题失败；绿阶段最小实现使其通过。

## 10. 验收标准

1. `uv run --frozen ruff check api src tests scripts eval` → All checks passed；
2. `uv run --frozen pytest -q` 全绿，新增用例数与场景在完成记录中列明；
3. `uv run --frozen python -m eval.harness.runner` 三份 Golden 样本全部匹配，退出码 0；
4. `uv run --frozen python -m eval.harness.writing_runner` A/B 两臂冻结基线全部匹配
   （调用次数 / token / 规则数不变），证明 `report=None` 路径零行为漂移；
5. `git diff` 确认 `uv.lock` 零变化、`frontend/` 零改动；HTTP/Schema/JobView/数据库
   无变化；
6. 本地提交（不推送、不开 PR），提交说明写清根因与回调注入方式；
7. `任务清单.md` T-032 追加完成记录并置「已完成」，`工作日志.md` 顶部按模板追加。

## 11. 明确范围外（越界即返工）

- 任何 `frontend/` 文件改动、HTTP 路由 / 请求响应 Schema / JobView 结构 / 数据库变更；
- `run_supplement` / `rewrite_sections` 及对应任务种类的细粒度进度补点（第 8 节遗留）；
- `ProgressReporter` 节流窗口、50 条快照、sink 异常策略的调整；
- `section_agent` 内部过程消息、模型调用与检索实现本身的行为变化；
- 依赖增删、uv.lock 变更、真实 LLM/Embedding/MinerU/网络相关测试。