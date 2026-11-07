# eval/ — Golden Data 离线评测集

与 `tests/` 单元测试隔离的**固定样本回归基线**：用手工构造的中文合成招标文件，驱动
完整抽取链路（单元切分 → 提示词 → 归一化 → 合并 → 溯源核对），把实际产物与人工核定的
期望产物做精确比较。T-021（抽取指标）、T-022（编写指标）将在本底座上叠加指标计算。

## 1. 合成与脱敏声明

- 全部样本为**虚构合成数据**：项目名称、编号、金额、机构名均为示例值，不对应任何真实
  采购项目、单位或人员，不含真实招标文件原文，可随仓库公开再分发；
- 样本刻意小而精（每份 3–4 章、正文数百字），目的是稳定覆盖具体工程边界，而非模拟
  真实长文档；
- 模型夹具（`llm_fixtures.json`）是**脚本化的假模型返回**，评测全程不发起任何网络请求，
  不实例化真实 DeepSeek / Embedding / MinerU。

## 2. 目录结构

```text
eval/
├── harness/                     # 评测代码（loader / runner，含单元测试见 tests/test_eval_harness.py）
└── datasets/golden/<样本>/
    ├── meta.json                # doc_id、doc_type、parser、场景说明
    ├── tender.md                # 手工构造的招标 Markdown，一级标题「# 第X章 …」分章
    ├── llm_fixtures.json        # 按章节标题键控的假模型返回队列
    └── expected_extraction.json # 人工核定的期望 TenderDocument（稳定投影）
```

## 3. 运行方式

```powershell
uv run python -m eval.harness.runner            # 全部样本，全匹配退出码 0，失配退出码 1
uv run python -m eval.harness.runner --out x.json  # 额外写出 JSON 报告（结果文件不入库）
```

pytest 的 `testpaths=["tests"]` 不收集本目录；CI 在离线测试之外单独执行本 runner。

## 4. 样本设计意图

| 样本 | 场景 | 专门覆盖的边界 |
|---|---|---|
| `01_engineering` | 工程施工，干净基线 | 5 评分因素、3 资格条件、确定性格式扫描（1 夹具 + 3 扫描）；全部 source_quote 逐字可溯源，期望 `ungrounded_fields=[]` |
| `02_goods` | 货物采购，脏数据归一化 | basic 包在 `project` 子对象、`"30分"` 字符串分值、`null` 列表、缺 name/非字典条目丢弃、预算与 1 条 quote 故意造假 → 期望 `ungrounded_fields=["预算金额","评分因素[3]"]` |
| `03_service` | 服务/培训，容错与缺字段 | 代码围栏 JSON（围栏剥离）、空对象触发重试、服务/师资类别、basic 三字段缺失为空串、1 条资格 quote 造假 → 期望 `ungrounded_fields=["资格条件[1]"]` |

## 5. 期望文件的冻结流程（禁止盲快照）

1. 先写 `tender.md`（原文事实的唯一来源）与 `llm_fixtures.json`（含刻意构造的脏数据）；
2. 用 harness 跑出实际产物（临时目录，不写 `upload/`）；
3. **逐字段对照 `tender.md` 原文人工核定**：字段值、归一化结果、被丢弃条目、
   `ungrounded_fields`、`calls`（含重试次数）是否符合预期；
4. 确认无误后再冻结为 `expected_extraction.json`；
5. 之后任何实际/期望失配都应先判断是代码回归还是样本需要演进，不得直接改期望文件掩盖回归。

期望文件只保留稳定字段：`extraction_meta` 中的 `elapsed_seconds`、`cache_hit`、`model`、
`content_sha256` 由 runner 投影剔除（耗时与环境相关，不参与基线比较）。

## 6. 新增样本规范

- 在 `datasets/golden/` 下新建 `序号_场景/` 目录，runner 自动发现，无需改代码；
- 章节标题必须以「第X章」开头，否则不会被识别为抽取单元；
- 单章正文保持在 12000 字符以内，保证每章恰好一个抽取单元、夹具键控稳定；
- 可溯源的 `source_quote` 必须是 `tender.md` 的逐字片段（溯源核对会剔除空白后做连续匹配，
  注意句末标点也要一致）；不可溯源样本必须在 `meta.json` 场景说明中写明意图；
- 夹具按章节标题键控，值为按调用顺序消费的返回队列（可表达重试、围栏 JSON、空响应）；
- 新增样本后按第 5 节流程冻结期望文件，并在第 4 节登记设计意图。

## 7. 字段级评测指标（T-021）

精确匹配回答「产物是否与人工核定期望逐字段一致」，字段指标进一步回答「错了多少、
错在哪类字段、溯源可信度如何」。两者共用同一次离线运行，精确匹配仍是退出码唯一
依据，指标不设阈值门禁。实现见 `harness/metrics.py`，纯函数、零网络、可重复。

### 7.1 展平与比较口径

- **标量字段**（5 个基础字段）：精确相等；空串/`None`/空列表视为缺位，双方皆空
  不进统计；
- **字符串列表**（`preliminary_review`、三类 requirements、`contract_highlights`
  以及资格 `thresholds`）：按 multiset 比较，重复元素按出现次数配对；
- **对象列表**（评分因素/评分表/资格/格式条目）：按身份键配对——
  `evaluation_factors.name`、`score_table.factor`、`qualifications.requirement`、
  `bid_format_items.title`；重复身份键按出现次序第 k 个配第 k 个；配对后逐叶子
  比较，未配上的整条计元素缺失/多余；
- `doc_id`、`doc_type` 是输入回显，`extraction_meta` 是过程遥测，均不进 P/R
  （仍由精确匹配守门）；未在 schema 声明的键不参与指标。

差异标签：`missing_field`（FN）、`spurious_field`（FP）、`value_mismatch`
（FP+FN）、`element_missing`（FN）、`element_spurious`（FP）、
`type_mismatch`（FP+FN）。值不匹配对称双计数，Precision 与 Recall 同时受损。

### 7.2 未溯源字段率

直接复用生产函数 `src.extraction.extractor.ground_check`，不复制规则：

- 分母（证据单元）= 5 个非空基础标量 + 含非空 `source_quote` 的评分因素/资格；
- 分子 = 未溯源标签数；空 quote 条目不参与，仅以 `empty_quote_units` 披露；
- 无 quote 槽位的列表字段（评分表、格式条目、requirements 等）整体不参与。

### 7.3 聚合与当前锁定基线

micro 先汇总计数再算比率（按字段量加权）；macro 对各样本比率等权平均（按文档
加权）；分母为 0 时 JSON 为 `null`、终端显示 `n/a`。当前 Golden 基线锁定值：

| 样本 | TP | FP | FN | P/R/F1 | 证据单元 | 未溯源 | 未溯源率 |
|---|---|---|---|---|---|---|---|
| `01_engineering` | 47 | 0 | 0 | 1.0000 | 13 | 0 | 0.0% |
| `02_goods` | 25 | 0 | 0 | 1.0000 | 9 | 2 | 22.2% |
| `03_service` | 31 | 0 | 0 | 1.0000 | 8 | 1 | 12.5% |
| micro | 103 | 0 | 0 | 1.0000 | 30 | 3 | 10.0% |
| macro | — | — | — | 1.0000 | — | — | 11.6% |

精确匹配通过时 P/R 必然为 1.0；指标的区分度由 `tests/test_eval_metrics.py`
中的手写故障注入用例保证，而非由 Golden 现状证明。

### 7.4 JSON 报告（`--out`）

`--out` 写出版本化信封（T-020 的裸列表格式已升级，报告文件不入库、无外部契约）：

```json
{
  "schema_version": 1,
  "samples": [
    {"name": "...", "passed": true, "diffs": [],
     "field_metrics": {"counts": {"tp": 0, "fp": 0, "fn": 0},
                       "precision": null, "recall": null, "f1": null,
                       "issues": [],
                       "grounding": {"evidence_units": 0, "ungrounded": 0,
                                     "ungrounded_rate": null,
                                     "ungrounded_fields": [],
                                     "empty_quote_units": 0}},
     "actual": {}}
  ],
  "aggregate": {"micro": {}, "macro": {},
                "grounding": {"micro": {}, "macro": {}}}
}
```

同一结果两次序列化字节一致；报告不含时间戳、随机数或环境相关字段。

### 7.5 防止篡改期望文件制造指标

1. 指标逻辑测试全部使用内联手写数据，改 `expected_extraction.json` 影响不到断言；
2. Golden 锁定测试固定 TP 47/25/31、micro 103、未溯源 3/30=10% 等绝对值，期望
   文件被改时精确匹配或锁定数值至少一处变红；
3. 每份样本重算 `ground_check` 的结果必须与期望文件中的
   `extraction_meta.ungrounded_fields` 一致，无法虚构溯源状态；
4. harness 对数据集只读，仓库不存在写期望文件的代码路径；期望文件变更仍须走
   第 5 节人工冻结流程。