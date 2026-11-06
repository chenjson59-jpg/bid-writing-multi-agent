# 规格：章节评审接入确定性规则检查（T-013）

## 1. 用户场景与业务目标

当前章节质量评审只有 LLM Reviewer 的语义评分（`ReviewResult`：score、issues、
suggestions、placeholder_count），引用编号是否越界、无检索是否虚构引用、片段是否
属于当前文档、正文是否出现被禁止的具体报价，全部依赖模型自觉，无法离线验证，也不
满足 AGENTS.md 第 5.4 节「合规性、占位符、报价禁令、引用有效性、覆盖状态由确定性
规则检查」。

目标：新增一个**纯确定性、零模型调用**的规则检查模块，与 LLM 语义评审**分开输出、
互不替代**：LLM 分数仍标注为模型语义评审，规则结果以独立字段进入章节草稿与
`report.json`，让无检索引用、引用越界、错误 doc_id、报价违规成为可断言、可测试、
可在 UI（后续 T-017）单独展示的客观事实。

## 2. 输入、输出与副作用

- 新增 `src/bid_writer/rules.py`：
  - `check_section_rules(markdown, plan, retrieved, *, doc_id="") -> RuleCheckResult`，
    纯函数，不调模型、不读文件、不联网。
- `src/bid_writer/schema.py` 新增 dataclass `RuleCheckResult`：
  - `section_id: str`
  - `passed: bool`——无任何规则错误即为 true；
  - `citation_markers: list[str]`——正文中出现的引用标记（事实，如 `["[1]", "[3]"]`）；
  - `errors: list[dict]`——每项 `{"code": <规则码>, "message": <中文说明>}`；
  - `placeholders: list[str]`——确定性提取的占位符（复用 `extract_placeholders`）；
  - `to_dict()` 供 JSON 序列化。
- 规则码（稳定字符串，供测试与前端使用）：
  - `CITE_EMPTY`：无检索结果（`retrieved` 为空）却出现 `[n]` 引用；
  - `CITE_OUT_OF_RANGE`：引用编号大于本轮实际传入的片段数；
  - `CITE_DOC_MISMATCH`：检索片段 metadata 的 `doc_id`（缺省时取片段 id 的
    `doc_id:` 前缀）与当前 `doc_id` 不一致；仅在传入 `doc_id` 且片段自带标识时检查；
  - `QUOTE_FORBIDDEN`：非占位策略章节出现具体金额/让利/单价决策（见 §3 模式）。
- 数据结构变化：
  - `SectionDraft` 新增 `rule_check: RuleCheckResult | None = None`，`to_dict()`
    输出 `rule_check`（无检查时为 `null`）；
  - `BidReport` 新增 `rule_checks: list[dict] = []`（每章节一条）与
    `rule_error_count: int = 0`；`summary()` 增加 `rule_error_count`；
  - `sections[*]` 字典内嵌 `rule_check` 对象。
- 无 HTTP 接口签名变化、无数据库变化；`report.json` 新增字段均有默认值，旧产物
  反序列化不受影响。

## 3. 规则定义与边界

### 3.1 引用标记识别

- 正则 `\[(\d+)\]`，只匹配括号内纯数字；Markdown 链接 `[文字](url)` 不会命中；
- 同一标记重复出现只计一次，按编号升序输出；编号 0 或负数不存在（正则不匹配负数），
  `[0]` 视为越界（片段编号从 1 开始）。

### 3.2 引用规则

1. `retrieved` 为空且存在标记 → 一条 `CITE_EMPTY`，message 列出全部标记；
2. 存在片段时，编号 `n > len(retrieved)` 或 `n < 1` → 汇总一条
   `CITE_OUT_OF_RANGE`，message 含越界编号与片段总数；
3. 片段 doc_id 校验：优先 `chunk["metadata"]["doc_id"]`，缺失时从
   `chunk["id"]` 的 `{doc_id}:{index}` 前缀取；与当前 `doc_id` 不等即一条
   `CITE_DOC_MISMATCH`（按片段去重）；未传 `doc_id` 或片段无任何标识时跳过，不臆造；
4. 引用与片段全部匹配时无错误，标记仍如实记录。

### 3.3 报价禁令（保守检测，允许技术误报由人工复核）

命中以下任一模式即一条 `QUOTE_FORBIDDEN`（同类型去重，message 给出命中片段）：

- 货币符号加数字：`[¥￥]\s*\d`；
- 数字加金额单位：`\d+(?:\.\d+)?\s*(?:万元|元)\b`（`\b` 排除「元素」等组合，
  年月日不受影响）；
- 决策关键词邻近百分比：`(?:让利|下浮|优惠|报价|单价)[^。；;\n]{0,12}?\d+(?:\.\d+)?\s*%`。

不命中的合法表达：评分描述「30 分」、日期「2026 年」、不带数字的「报价部分详见
报价表」。资质、证书编号、人员姓名、业绩等 AGENTS 5.1 禁令**无法用确定性规则可靠
识别**，本期不做检测，在文档中如实说明边界，继续由 LLM 评审与人工核对承担。

### 3.4 占位符

- 占位符是 AGENTS 5.1 要求的合规表达，本身**不是错误**；规则结果只做确定性提取与
  计数，作为事实输出，不进入 `errors`。

## 4. 接入点与兼容性

- `section_agent`（agents.py）：在最终草稿选定后统一执行规则检查（含 placeholder
  分支，其标准文本不会触发任何错误），结果挂到 `SectionDraft.rule_check`；规则检查
  不参与修订循环的分数比较，不改变现有写作/修订行为。
- `build_report`（assembly.py）：以报告时重算为准（`draft.rule_check` 存在则复用，
  否则现场计算），把结果注入 `sections[*].rule_check`，并填充报告级
  `rule_checks` 与 `rule_error_count`；**不写入 `warnings`**，保持两套输出分离。
- 三条生成路径（pipeline 主链路、supplement 补写、sections 重写）都经过
  `section_agent`，自动获得规则检查；`load_drafts` 还原旧产物时 `rule_check`
  默认 `None`，`rebuild_artifact` → `build_report` 会基于磁盘正文重算补齐。
- 前端 `types.ts` 增加可选 `rule_check` 类型字段；本期不做 UI 呈现（属 T-017），
  不改变现有页面行为。
- 旧 `report.json`、旧 sections 索引缺少新字段时按缺省处理，不报错。

## 5. 验收标准

1. 新增 `tests/test_rules.py` 离线覆盖：无引用无金额通过、正常引用通过、
   `CITE_EMPTY`、`CITE_OUT_OF_RANGE`（含 `[0]`）、`CITE_DOC_MISMATCH`
   （metadata 与 id 前缀两种来源）、合法 doc_id 通过、三类报价模式命中、
   「30 分/2026 年/报价表」等不误伤、占位符事实提取、重复标记去重；
2. `tests/test_bid_writer.py` 集成断言：`section_agent` 产出的草稿带 `rule_check`
   且 FakeCaller 输出越界引用时被判定；`build_report` 的 `rule_checks`、
   `rule_error_count` 与 `sections[*].rule_check` 正确，且 `warnings` 不被规则结果污染；
3. LLM `ReviewResult` 字段与语义评分流程保持不变，规则结果独立字段输出；
4. 旧产物加载与 `rebuild_artifact` 路径有兼容测试或断言，不新增模型调用；
5. `docs/algorithms.md`（8.2 增补确定性规则检查）、`docs/api-contract.md`（§6.4
   report.json 字段）、`README.md`、前端类型同步；
6. `ruff`、后端全量测试、前端 `npm test` 与 `npm run build` 通过。
