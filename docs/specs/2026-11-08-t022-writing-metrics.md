# 规格：编写阶段离线评测指标（T-022，eval/ 第三期）

## 1. 用户场景与业务目标

T-020/T-021 已为「PDF 解析 → 结构化抽取」链路建立 Golden Data 精确匹配基线与字段级
P/R/F1 指标。编写链路（章节规划 → 写作 → 评审 → 修订 → 汇总报告）目前只有单元测试
覆盖，缺少**固定样本上的端到端回归基线**，无法回答：

- 一次提示词或编排改动后，三层覆盖率、引用有效率、确定性规则违规数是否漂移；
- 单轮直写与「评审—修订」闭环在调用数、token、修订次数、规则结果上的真实差异；
- 占位符是否按资料缺失情况正确产出，有没有该写不写、不该写乱写。

T-022 在不改动抽取评测底座与生产代码的前提下，新增一套**全离线、全 Fake、可重复**的
编写评测 harness，输出锁定基线并接入 CI。真实模型的语义质量评测不在本任务范围，仍归
`eval/` 人工评测或后续任务；本任务所有指标均为确定性可复算口径，不使用「幻觉检测」之类
无法离线证伪的命名。

### 1.1 已确认的设计决策

1. **A/B 口径**：代码中不存在 simple/advanced 模式。A 臂 = `max_revisions=0`
   （单轮直写，writer + 一次 reviewer 打分、零修订）；B 臂 = `max_revisions=1`
   （最多一轮评审—修订闭环）。两臂除 `max_revisions` 外参数完全一致，使用同一套夹具与
   Fake 检索各跑一次。A/B 仅为评测标签，不向生产代码引入新模式名词。
2. **数据集布局**：在现有三份 Golden 样本下各增 `writing/` 子目录，复用同目录已冻结的
   `expected_extraction.json` 作为编写输入，不改动任何抽取夹具与抽取期望。
3. **「虚构事实数」诚实命名**：离线无语义 ground truth，只提供两个确定性代理指标：
   规则违规数（生产 `rule_checks` 四码汇总）与无依据信号数
   （`ungrounded_signals`，正文已覆盖但证据层缺失的评分因素）。禁止宣称幻觉检测。
4. **runner 形态**：新建 `eval/harness/writing_metrics.py`（纯函数指标）与
   `eval/harness/writing_runner.py`（夹具 caller、Fake 检索、CLI），独立命令
   `python -m eval.harness.writing_runner`，CI 新增独立步骤；不改变 T-020/T-021 runner
   的退出码语义与 `schema_version=1` 抽取信封。编写报告信封为另一家族：
   `{"schema_version": 1, "kind": "writing_eval", ...}`，版本号独立从 1 起。
5. **编写输入来源**：直接加载样本已冻结的 `expected_extraction.json` 构造
   `TenderDocument`（与生产 `load_extraction` 同构），不在 harness 内重跑抽取。
6. **planner 静默兜底的防护**：生产 `_planner_llm` 会吞掉异常回退确定性大纲，本任务不改
   生产行为；harness 在 `plan_sections` 返回后断言 planner 夹具恰好消费 1 次，否则显式
   失败，避免夹具异常被静默兜底掩盖。
7. **引用有效率为章节级口径**：含引用标记的章节为分母，不含任何 `CITE_*` 规则错误的章节
   为分子，完全复用 `rules.py` 结果，不实现标记级第二套口径。

## 2. 输入、输出与副作用

### 2.1 输入

每份样本目录 `eval/datasets/golden/<sample>/writing/`：

| 文件 | 必填 | 内容 |
|---|---|---|
| `fixtures.json` | 是 | 脚本化假模型返回。键：`"planner"`（单元素队列）、`"writer:<章节标题>"`、`"reviewer:<章节标题>"`、`"reviser:<章节标题>"`，值为按调用顺序消费的响应队列，响应可带合成 `usage` 块 |
| `retrieval.json` | 是 | 按章节标题键控的 Fake 检索片段列表（`{"id","text","metadata"}` 同构）；placeholder 章节生产侧跳过检索，无需提供 |
| `profile.json` | 否 | 投标人资料字典，缺省 `{}`（`BidderProfile.from_dict`） |
| `config.json` | 否 | `{"retrieval_degraded": bool}`，缺省 `false`，值透传给 `build_report` |
| `expected_writing_metrics.json` | 是 | 人工冻结的两臂期望指标、占位符期望集合与 A/B 差值 |

编写链的招标输入取自上级目录已冻结的 `expected_extraction.json`（含 `extraction_meta`，
`TenderDocument` 可直接接收）。资料 schema 推断复用生产
`infer_profile_schema(tender_dict, PROFILE_FIELD_LABELS)`，与 `src/pipeline.py` 一致。

### 2.2 输出

- 终端：逐样本逐臂 `[PASS]/[FAIL]` 与差异路径、汇总行、指标表（覆盖率/引用/规则/占位符/
  调用与 token/A-B 差值）；wall-clock 不进指标表；
- `--out PATH`（可选）：写出版本化 JSON 信封，默认不落盘，报告文件不入库；
- 退出码：全部样本两臂与冻结期望一致为 0，否则 1。

### 2.3 副作用与硬约束

- 全程零网络、零真实 LLM/Embedding/MinerU；不实例化 `ChromaRetriever`、不碰
  `chroma_db/`、不写 `upload/`；禁止调用 `src/pipeline.py::run_bid_writer`（其硬构造
  Chroma 并写 `upload/bids/`），harness 直接组合生产函数
  `plan_sections → run_write_pipeline → build_report`；
- 所有运行期产物只允许落在 `tempfile.TemporaryDirectory`（本 runner 实际不落任何文件，
  临时目录仅为与 T-020 约束对齐的保险，测试中使用）；
- 固定 `max_concurrency=1` 保证调用顺序与夹具消费确定；固定时钟
  `datetime(2026, 1, 1)` 传入 `build_report`，信封不含时间戳/随机数/环境相关字段；
- wall-clock（`llm_usage.duration_ms`）属于易变字段：`UsageMeter` 照常采集，但
  信封在组装前经易变键投影剔除，不写 `--out` 报告、不进冻结基线（类比抽取 runner
  的 `VOLATILE_META_KEYS`）；token/调用数来自合成 usage，必须逐次可复现。

## 3. 编写链执行口径

### 3.1 两臂固定参数

`WriteOptions`：`min_score=75`、`top_k=6`、`target_words=1200`、`max_sections=0`
（不截断）、`export_word=false`；A 臂 `max_revisions=0`，B 臂 `max_revisions=1`。
每臂使用独立的全新 caller（队列独立消费）与独立 `UsageMeter(price_input=0,
price_output=0)`，经 `MeteredCaller` 包装后接入生产函数，用量口径与生产完全一致。

### 3.2 WritingCaller 键控规则

- 角色从 system 消息固定字样识别：`编写策划助手`→planner、`文件编写助手`→writer、
  `评审助手`→reviewer、`修订助手`→reviser；
- planner 键固定为 `"planner"`；其余角色从 user 消息首行正则 `^章节标题：(.+)$` 取标题，
  键为 `"<role>:<title>"`；无法识别角色或解析标题失败 → 显式抛错；
- `json_mode` 守卫：planner/reviewer 必须为 `true`，writer/reviser 必须为 `false`，
  不一致抛 `TypeError`，防止提示词接线漂移被静默吞掉；
- 键缺失或队列耗尽 → 抛 `KeyError`（含键名），不做任何静默兜底；
- caller 记录每个键的消费次数，供 planner 守卫与测试断言。

### 3.3 planner 静默兜底守卫

`plan_sections` 返回后检查 planner 队列消费次数恰好为 1：

- 次数为 0：planner 调用抛错被生产 `except Exception` 吞掉并回退确定性大纲，harness 抛
  `RuntimeError` 明确报告「planner 夹具异常被静默兜底」；
- 次数 >1：生产不存在 planner 重试路径，出现即说明接线异常，同样抛错。

### 3.4 ScriptedRetriever

签名与生产检索回调一致：`__call__(query: str, top_k: int) -> list[dict]`。夹具按章节
标题键控（planner 夹具统一把 `retrieval_query` 写成章节标题，故 graph 传入的 query 即
标题）；键缺失抛 `KeyError`；返回列表的深拷贝，防止生产代码改写污染夹具。placeholder
章节由 `run_write_pipeline` 跳过检索，不提供片段是预期行为。

## 4. 指标定义与公式

全部指标由 `eval/harness/writing_metrics.py` 的纯函数基于 `BidReport`（或其 `to_dict`
结果）计算，不读取环境、不联网、不引入第二套业务规则。覆盖率/引用/占位符复用生产
`coverage.py`、`rules.py`、`extract_placeholders` 的产出，harness 只做汇总与比率。

设报告含章节集合 S、评分因素集合 F。

### 4.1 三层覆盖率（plan/body/evidence）

直接取 `report.coverage_summary` 各层 `covered/total`：

```text
coverage_rate[layer] = covered / total      # total=0 时为 null（JSON）/ n/a（终端）
```

同时保留各层 `gaps` 因素名列表。三层漏斗语义以生产 `coverage.py` 为准，harness 不重算。

### 4.2 引用有效率（章节级）

```text
D = { s ∈ S | rule_check.citation_markers 非空 }          # 含引用标记的章节
N = { s ∈ D | rule_check.errors 中不存在 code 以 CITE_ 开头的错误 }
citation_valid_rate = |N| / |D|                            # |D|=0 时为 null
```

输出 `sections_with_markers=|D|`、`valid_sections=|N|`、`invalid_sections=|D|-|N|`、
`citation_valid_rate`。`QUOTE_FORBIDDEN` 不影响引用有效率（归入 4.3）。

### 4.3 确定性规则违规数

- `rule_error_sections` = `report.rule_error_count`（存在 ≥1 条错误的章节数）；
- `rule_total_errors` = 各章节 `errors` 条数总和；
- `rule_errors_by_code`：按 `CITE_EMPTY / CITE_OUT_OF_RANGE / CITE_DOC_MISMATCH /
  QUOTE_FORBIDDEN` 计数，零命中码输出 0，保证键集合稳定。

### 4.4 无依据信号数（ungrounded_signals，确定性代理指标）

```text
ungrounded_signals = { f ∈ F | body_covered(f) 且非 evidence_covered(f) }
```

即正文已经响应、但缺少同 doc_id 片段与合法引用支撑的评分因素（含无检索、引用越界、
错 doc 三种成因）。输出计数与因素名排序列表。该指标**不是**幻觉检测：离线无法判断正文
语义是否虚构，只陈述「正文响应缺少有效证据」这一可复算事实。

### 4.5 占位符准确率（P/R/F1）

数据集在冻结文件中显式声明两个集合：

- `expected_present`：本臂正文中**应当**出现的占位符（完整字符串，如
  `【待填写：投标报价】`）；
- `expected_absent`：**禁止**出现的占位符。

实际集合 `actual = set(report.placeholders)`（生产侧已去重排序）：

```text
TP = |actual ∩ expected_present|
FN = |expected_present − actual|
FP = |actual − expected_present|           # 含未声明占位与禁现占位，不重复计数
forbidden_hits = sorted(actual ∩ expected_absent)
precision = TP / (TP + FP)                 # 分母 0 → null
recall    = TP / (TP + FN)                 # 分母 0 → null
f1        2TP / (2TP + FP + FN)            # 分母 0 → null
```

`expected_present` 与 `expected_absent` 必须互不相交，加载时校验，相交即数据构造错误。

### 4.6 模型调用与 token 成本

取 `report.llm_usage`：`calls`、`prompt_tokens`、`completion_tokens`、`total_tokens`、
`models` 进入冻结基线；`duration_ms` 在投影阶段剔除，不进信封与冻结基线。单价固定为 0，
不估算费用（离线夹具 token 是合成值，估算金额没有业务意义）。

### 4.7 修订与评分辅助项

- `total_revisions` = Σ 章节 `revisions`；
- `avg_score_after`：非 placeholder 章节（即经过 LLM reviewer 的章节）`score_after`
  的算术平均，无此类章节时为 null；
- `sections` 紧凑明细：每章节 `id/title/strategy/revisions/score_before/score_after/
  rule_codes/placeholders/retrieved_count`，供人工冻结核对，不做跨字段二次指标。

### 4.8 A/B 差值

对一组显式声明的整数指标做 B−A：`calls`、`prompt_tokens`、`completion_tokens`、
`total_tokens`、`total_revisions`、`rule_total_errors`、`rule_error_sections`、
`ungrounded_signal_count`、占位符 `tp/fp/fn`、三层 `covered`。比率类指标不做差值
（直接并排比较），避免 null 参与算术。

## 5. 报告信封与冻结比较

### 5.1 信封

```json
{
  "schema_version": 1,
  "kind": "writing_eval",
  "samples": [
    {
      "name": "01_engineering",
      "arms": {
        "A_single_pass": {"passed": true, "diffs": [], "metrics": {}},
        "B_review_revise": {"passed": true, "diffs": [], "metrics": {}}
      },
      "ab_delta": {},
      "expected_placeholders": {"present": [], "absent": []}
    }
  ],
  "aggregate": {
    "A_single_pass": {"calls": 0, "total_tokens": 0, "macro_rates": {}},
    "B_review_revise": {}
  }
}
```

同一运行结果两次序列化字节一致；`duration_ms` 在投影阶段已剔除，信封中不存在该字段。

### 5.2 冻结比较

期望文件只保存稳定字段。比较前对实际 metrics 做易变键投影（剔除
`llm_usage.duration_ms`），再与期望做递归比较，输出字段路径级差异（类型不一致、列表
长度不一致、值不一致分别表述）。任一差异即该臂 `passed=false` 并进入退出码 1。

### 5.3 防篡改

1. 指标纯函数测试全部使用内联数据，改数据集期望文件影响不到断言；
2. 单独的 Golden 锁定测试固定三份样本的调用数、token、规则错误数、占位符 TP/FP/FN 等
   绝对值，期望文件被改时冻结比较或锁定数值至少一处变红；
3. harness 对数据集目录只读，仓库不存在写期望文件的代码路径；
4. 期望变更必须走第 7 节人工冻结流程。

## 6. 正常 / 异常 / 边界路径

### 6.1 正常路径

三份样本两臂全部与冻结期望一致：终端全 PASS、退出码 0、`--out` 信封可重复序列化。

### 6.2 异常路径（均须有测试）

- 夹具键缺失 / 队列耗尽：`KeyError` 显式失败，错误信息含键名；
- 提示词无法解析角色或章节标题：显式抛错；
- `json_mode` 与角色不匹配：`TypeError`；
- planner 夹具异常被生产静默兜底：planner 守卫 `RuntimeError`；
- 检索夹具缺键（generate 章节）：`KeyError`；
- 期望文件 JSON 非法 / 占位符 present 与 absent 相交：带样本名的明确错误；
- 实际与冻结期望不一致：差异路径列表 + 退出码 1。

### 6.3 边界样本分配（三份 Golden）

| 样本 | 专门边界 | 预期确定性结果 |
|---|---|---|
| `01_engineering` | 干净基线：合法同 doc 引用、placeholder 章节零调用、B 臂一个章节初评 70 分触发一轮修订后升分 | 规则 0 错误；无依据信号 0；B 臂 revisions=1、调用数与 token 高于 A 臂 |
| `02_goods` | 引用编号越界（2 条片段输出 [3]）、检索片段混入错 doc_id、正文出现让利百分比触发报价禁令 | `CITE_OUT_OF_RANGE`、`CITE_DOC_MISMATCH`、`QUOTE_FORBIDDEN` 各 ≥1；对应章节引用有效率为 0；无依据信号 ≥1 |
| `03_service` | 检索降级（`retrieval_degraded=true`）且 generate 章节无片段却输出引用 → `CITE_EMPTY`；writer 故意产出禁现占位符 | `CITE_EMPTY` ≥1；占位符 FP≥1 且 `forbidden_hits` 非空；precision<1；报告降级标志为 true |

## 7. 期望指标冻结流程（禁止盲快照）

1. 先写 `fixtures.json`（含合成 usage 与刻意构造的脏输出）、`retrieval.json`、
   `profile/config`，并在本规格第 6.3 节登记意图；
2. 用 writing_runner 把实际指标输出到仓库外临时文件（`--out` 指向系统临时目录）；
3. 逐指标人工核对：章节清单与策略、调用数（按角色队列消费推算）、token 合计、规则码与
   违规正文、三层覆盖缺口、占位符实际集合、A/B 差值是否符合设计意图；
4. 确认无误后冻结为 `expected_writing_metrics.json`，并在 `eval/README.md` 记录锁定值；
5. 之后任何失配先判断代码回归还是样本演进，不得改期望掩盖回归；冻结中修过的夹具问题
   必须在工作日志与 README 冻结说明中记录。

## 8. 测试策略（TDD）

新增 `tests/test_eval_writing_metrics.py`（扁平 `test_*.py` 组织），全部离线、内联或
tmp_path 夹具，不依赖网络：

- 纯函数：占位符 P/R/F1（含空分母、禁现命中）、规则聚合与引用有效率（含无引用章节）、
  无依据信号、三层覆盖率、A/B 差值、易变键投影、递归比较；
- WritingCaller：四角色键控、队列耗尽、标题解析失败、json_mode 守卫、消费计数；
- planner 守卫、ScriptedRetriever 缺键与深拷贝；
- 内联迷你样本端到端：A/B 两臂调用数/token/修订差值、信封确定性字节、冻结失配 diff；
- 边界：无检索 CITE_EMPTY、错 doc、报价禁令、placeholder 章节、降级标志；
- Golden 锁定：三份真实样本两臂全部 PASS，并断言锁定绝对值（防篡改）。

## 9. 兼容性与文档

- 不改 `src/` 生产代码、不改抽取 harness 与其信封/退出码；新增 CI 独立步骤；
- `eval/README.md` 新增编写评测章节（目录结构、键控规则、指标公式、冻结流程、锁定
  基线、边界样本意图）；根 README 离线评测节同步；
- `AGENTS.md` 验证命令仅在命令集合确实变化时更新（本任务新增 writing_runner 命令，
  需同步第 4.3 节与第 12 节）。

## 10. 验收标准

1. `uv run ruff check api src tests scripts eval` 通过；
2. `uv run pytest -q` 全量通过（新增用例先红后绿）；
3. `uv run python -m eval.harness.runner` 保持 3/3、退出码 0，行为零变化；
4. `uv run python -m eval.harness.writing_runner` 三份样本两臂全部匹配冻结基线、
   退出码 0，重复运行结果一致；
5. 无依赖变更：`uv.lock` 与 `pyproject.toml` 零 diff；不写 `upload/`、不碰
   `chroma_db/`；
6. 规格、`eval/README.md`、根 README、AGENTS.md 验证命令、CI 步骤同步；
7. 本地 `任务清单.md` 与 `工作日志.md` 按规则更新（不入库）；
8. 分支 `codex/t022-writing-metrics` 提交并推送后停住，等待用户开 PR。