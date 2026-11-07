# 规格：覆盖报告升级为三层覆盖（计划/正文/证据，T-012）

## 1. 用户场景与业务目标

当前 `report.json` 的 `evaluation_coverage` 只做一件事：评分因素名称是否出现在某章节
计划的 `scoring_factors` 里（`assembly.build_report` 内联逻辑）。这只是**计划覆盖**，
无法证明正文真的响应了该因素，更无法证明响应有检索原文依据。AGENTS.md 第 5.2 节要求
区分三种互相不可替代的覆盖状态：

1. **计划覆盖（plan）**：评分因素是否分配给章节；
2. **正文覆盖（body）**：章节正文是否实际响应评分因素；
3. **证据覆盖（evidence）**：响应是否有有效检索片段与正文引用依据。

目标：把覆盖计算抽到独立纯函数模块，输出三层布尔结果、逐层缺口清单与漏斗统计，
全部确定性、零模型调用、可离线复算；保持旧字段与旧产物兼容。

## 2. 输入、输出与副作用

- 新增 `src/bid_writer/coverage.py`：
  - `compute_evaluation_coverage(tender, drafts, *, doc_id) -> tuple[list[dict], dict]`，
    返回逐因素明细与报告级 `coverage_summary`；纯函数，不调模型、不读文件、不联网。
- 逐因素 `evaluation_coverage[*]` 字段（在现有字段上扩展，不删除）：
  - 保留 `name`、`score`、`category`、`section_ids`；
  - 保留 `covered`，语义明确为**计划覆盖**（兼容补写链路 `_uncovered_factors`、
    e2e/既有测试与前端摘要，旧产物读取不受影响）；
  - 新增 `plan_covered`、`body_covered`、`evidence_covered`（布尔）；
  - 新增 `body_section_ids`、`evidence_section_ids`（命中的章节 id 列表）；
  - 新增 `gaps: list[str]`，取值 `"plan"`/`"body"`/`"evidence"`，列出该因素尚未
    达到的最高层级，便于 UI 与人工核对。
- 报告级新增 `BidReport.coverage_summary: dict`（dataclass 字段，带默认值，保证
  `BidReport(**旧数据)` 可加载）：

  ```json
  {
    "plan":     {"total": 4, "covered": 4, "gaps": []},
    "body":     {"total": 4, "covered": 3, "gaps": ["投标报价"]},
    "evidence": {"total": 4, "covered": 2, "gaps": ["投标报价", "企业业绩"]}
  }
  ```

- `BidReport.summary()` 新增 `body_factors_covered`、`evidence_factors_covered`
  两个摘要计数；`evaluation_factors_covered` 仍为计划层计数，语义不变。
- 无 HTTP 接口签名变化、无数据库变化、无 sections 索引格式变化。

## 3. 三层判定规则（确定性）

设因素 `f`，候选章节为 `plan.scoring_factors` 含 `f.name` 的草稿。

### 3.1 计划覆盖（plan）

- 与现状完全一致：精确名称匹配，候选章节非空即 `plan_covered=true`；
- 不做模糊匹配。

### 3.2 正文覆盖（body）

在计划覆盖前提下，至少一个候选章节同时满足：

1. `plan.strategy != "placeholder"`（占位章节不算实质响应，`llm_fill`/`generate` 可以）；
2. 把正文里的占位符（`【待填写…】`）剔除后，用 `normalize_title` 去空白，
   因素名（同样去空白）作为子串出现在正文中。

不满足则 `body_covered=false`。该规则是保守的关键词证据，不做语义判断；
语义充分性仍由 LLM Reviewer 评分承担，两套结论分开。

### 3.3 证据覆盖（evidence）

在正文覆盖前提下，至少一个**正文命中章节**同时满足：

1. `draft.retrieved` 非空，且至少一条片段的 doc_id 与当前 `doc_id` 一致
   （复用 T-013 的 `_chunk_doc_id`：metadata 优先、id 前缀兜底；无法识别的片段不算）；
2. 正文包含至少一个合法引用标记 `[n]`，且 `1 <= n <= len(retrieved)`
   （复用 T-013 的标记提取，越界标记不计入合法依据）。

两条同时成立才 `evidence_covered=true`。只检索了但正文没有引用，属于「有证据素材、
未在响应中使用」，不计证据覆盖，会进入 evidence 缺口。

### 3.4 漏斗不变量

构造上保证 `evidence_covered ⇒ body_covered ⇒ plan_covered`，三层 covered 数
单调不增；`gaps` 与各层缺口清单自洽。

## 4. 兼容性与接入

- `build_report` 改为调用新模块，原有「未覆盖且分值非空 → warning」逻辑保持不变，
  判定基准仍是计划层；
- 补写 `supplement._uncovered_factors` 继续读 `covered`，行为不变；
- 旧 `report.json` 缺少新字段时：`BidReport(**data)` 依赖 dataclass 默认值正常加载，
  前端新字段全部声明为可选；`rebuild_artifact` 重算后自动补齐三层结果；
- 前端只更新 `types.ts` 类型，不做 UI 呈现（三层覆盖的可视化属 T-017）；
- 测试全部离线，用手工构造的 `SectionDraft` 与 Fake 片段，不连真实 Chroma/模型。

## 5. 验收标准

1. 新增 `tests/test_coverage.py` 覆盖：仅计划覆盖（正文无因素名）、正文覆盖
   （含空白归一化匹配）、占位章节剔除因素名后不算正文覆盖、证据覆盖三条件组合
   （无片段/片段 doc_id 不符/无引用标记/标记越界/全部合法）、漏斗不变量、
   `gaps` 与各层缺口清单、`covered` 等于 `plan_covered`；
2. `tests/test_bid_writer.py` 集成断言：`build_report` 输出 `coverage_summary`、
   明细新字段与 `summary()` 新计数；既有 e2e/supplement 断言不破坏；
3. 旧报告加载兼容有测试或断言保障（默认值路径）；
4. `docs/algorithms.md`（8.3.4 重写三层规则、8.3.5/8.3.6 字段与摘要）、
   `docs/api-contract.md`（§6.4 report.json 字段）、`README.md`、前端类型同步；
5. `ruff`、后端全量测试、前端 `npm test` 与 `npm run build` 通过。
