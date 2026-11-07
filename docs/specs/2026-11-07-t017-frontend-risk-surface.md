# 规格：前端显式展示检索降级、未溯源字段与覆盖风险（T-017）

## 1. 用户场景与业务目标

系统在多个环节已经产出「可信度信号」，但前端没有成体系展示，用户无法区分「有原文
依据的内容」与「降级/无依据内容」：

1. **检索降级静默**：`ChromaRetriever` 初始化失败（集合不存在、Embedding 不可用）或
   查询异常时静默返回空列表，`report.json` 与任务视图没有任何显式标记，违反
   AGENTS.md 5.3「检索失败……必须在任务状态或报告中显式标记」；
2. **未溯源字段无入口**：抽取阶段的 `ungrounded_fields`（原文核不上的字段）已写入
   `extraction.json` 的 `extraction_meta`，接口 `GET /documents/{doc_id}/extraction`
   也返回，但抽取校正页未展示；
3. **三层覆盖只有一个数字**：T-012 的计划/正文/证据漏斗在摘要里有三个计数，结果页
   仍只显示计划层「评分覆盖 x/y」，正文、证据缺口不可见；
4. **确定性规则结果不可见**：T-013 的 `rule_error_count` 与章节级 `rule_check` 已在
   摘要中，结果页没有任何提示。

目标：把这四类信号变成用户一眼可见的 UI 提示，前后端类型一致；后端只做检索降级标志
这一处必要补全，其余纯前端消费既有数据。

## 2. 输入、输出与副作用

### 2.1 后端变更（最小必要）

- `src/vectorstore/retrieval.py`：`ChromaRetriever` 新增 `degraded: bool` 属性。
  初始化异常时为 `True`；`search()` 查询异常时捕获、置 `True` 并返回空列表
  （与其 docstring「失败返回空列表」对齐，当前查询异常实际会向上抛，属既有缺口）；
- `src/bid_writer/schema.py`：`BidReport` 新增 `retrieval_degraded: bool = False`
  字段（`asdict` 自动进 `report.json`），`summary()` 输出同名键；旧产物缺省该键时
  反序列化默认 `False`，保持兼容；
- `src/bid_writer/assembly.py`：`build_report(..., retrieval_degraded: bool = False)`
  新增带默认值参数并写入报告；
- 三个调用方：
  - `pipeline.run_bid_writer`：透传 `retriever.degraded`；
  - `supplement.run_supplement`：`retriever is None or retriever.degraded` 时为 `True`；
  - `sections.rebuild_artifact`：从旧 `report_data` 保留该字段（人工编辑/重写不产生
    新检索器，标志粘性保留，避免刷新报告后信号消失）。
- 无 HTTP 路由、请求 Schema、数据库变化；`JobStatus.bid` 与 `BidHistoryItem.summary`
     均为 `dict[str, Any]`，自动携带新键。

### 2.2 前端变更

- `types.ts`：`BidSummary` 增加 `retrieval_degraded?: boolean`、
  `rule_error_count?: number`（旧产物缺省）；
- 新增纯函数模块 `src/lib/bid_quality.ts`（无 React 依赖，可单测）：
  - `coverageLayers(summary)`：输出计划/正文/证据三层 `{label, covered, total}`，
    缺省字段按 0 处理；
  - `isRetrievalDegraded(summary)`：布尔判定；
  - `ruleErrorSections(summary)`：筛出 `rule_check.passed === false` 的章节及错误；
  - `ungroundedFields(extractionMeta)`：从 `extraction_meta` 安全取字符串数组，
    非数组/缺省返回空数组；
- `ResultView.tsx`：
  - 检索降级时顶部展示红色 Banner，明确「本次生成无原文依据，引用与证据覆盖为空，
    需人工核对」；
  - 新增「三层覆盖漏斗」卡片：计划 → 正文 → 证据的 x/y，任一层收缩用 warn 色并列出
    已知缺口名称（计划层缺口复用 `uncovered_factors`）；
  - `rule_error_count > 0` 时展示 warn Banner，章节明细表「状态」列加规则错误 Badge
    （hover 展示错误码与文案）；
- `ExtractionEditor.tsx`：`ungrounded_fields` 非空时展示 warn Banner，列出未溯源
  字段，提示人工核对原文；
- 不新增路由、不改变既有交互。

## 3. 正常、异常与边界路径

- 正常（检索健康）：`degraded=False`，不出现降级 Banner；漏斗与规则卡片正常渲染；
- 集合不存在/Embedding 失败：初始化即 `degraded=True`，报告与摘要带标志，前端红色
  Banner；写作链路不中断（既有降级语义不变）；
- 查询中途异常：`search` 捕获后置 `degraded=True` 返回 `[]`，同一轮后续查询仍可尝试
  （不缓存失败状态之外的行为），最终报告如实标记；
- 旧产物：`report.json` 无 `retrieval_degraded` 键时加载为 `False`；前端可选链缺省
  不渲染降级 UI；`body/evidence_factors_covered` 缺省按 0 处理；
- 空评分因素（total=0）：漏斗显示 0/0，不报错、不制造虚假缺口；
- `extraction_meta.ungrounded_fields` 类型异常（非数组）：安全返回空数组，不渲染；
- 人工编辑/单章重写：标志从旧报告保留，不因重建而丢失。

## 4. 兼容性与验收标准

- 后端：
  1. `ChromaRetriever` 初始化失败与查询失败两条路径都有离线测试断言 `degraded=True`
     且返回 `[]`；健康路径 `degraded=False`；
  2. `build_report` 新参数默认 `False`，`summary()` 含 `retrieval_degraded` 键；
     旧报告 JSON（无该键）可正常 `BidReport(**data)` 加载；
  3. 既有 e2e/supplement 的 Fake Retriever 补齐 `degraded` 属性后全部通过；
- 前端：
  1. 新增 `tests/bid_quality.test.ts` 覆盖：三层计数（含缺省/0 因素）、降级判定、
     规则错误章节筛选、未溯源字段的正常/非数组/缺省输入；
  2. `npm test` 全过、`npm run build`（含 `tsc --noEmit`）通过；
- 全量：`uv run ruff check api src tests scripts`、`uv run pytest -q` 通过；
- 文档同步：`docs/api-contract.md` 摘要字段表、`docs/algorithms.md` 检索降级小节、
  `README.md` 可信度信号说明；
- 全部测试离线，不依赖真实 LLM、网络与 Chroma，不触碰本地 `chroma_db/`。
