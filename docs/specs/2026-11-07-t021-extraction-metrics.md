# 规格：抽取阶段字段级评测指标（T-021）

## 1. 用户场景与业务目标

T-020 建立的 Golden Data 基线只有「精确匹配 / 失配」二元结论：一份样本只要任一
字段不同即整体失败，无法回答「错了多少、错在哪类字段、溯源可信度如何」，也无法
支撑后续抽取链路改动的趋势对比。

T-021 在不破坏精确匹配基线的前提下，为同一份离线 Golden Data 增加可重复运行的
字段级指标：

- Precision / Recall / F1（字段级 TP/FP/FN）；
- 未溯源字段率（复用生产侧确定性 `ground_check`，不另立规则）；
- 单样本指标 + 全量 micro/macro 聚合；
- 终端摘要与可选的版本化 JSON 报告。

精确匹配仍是 runner 退出码的唯一依据，指标不设阈值门禁（仓库未配置覆盖率/指标
门禁，不臆造阈值）。

## 2. 输入输出与副作用

- **输入**：T-020 既有 Golden 样本（`tender.md`、`llm_fixtures.json`、
  `expected_extraction.json`、`meta.json`），本任务不修改任何样本数据；
- **输出**：runner 在原有 `[PASS]/[FAIL]` 与「N/N 份样本匹配」之后追加字段指标
  表；`--out PATH` 写出版本化 JSON 信封（默认不落盘，报告文件不入库）；
- **副作用**：无新增。评测仍在 `tempfile.TemporaryDirectory` 内运行完整
  `run_extraction`，不写 `upload/`、不碰 `chroma_db/`，零网络、零真实模型。

## 3. 展平与比较口径

指标代码为 `eval/harness/metrics.py`，采用 **schema 感知的字段声明表**，不做通用
黑盒递归；未声明字段不参与指标（归一化产物只可能含声明字段，未声明键由精确匹配
基线守门）。

### 3.1 字段声明

| 字段 | 类型 | 比较单元 | 身份键 |
|---|---|---|---|
| `project_name`、`tender_no`、`budget_amount`、`bid_deadline`、`bid_open_place` | 标量 | 1 字段 = 1 单元 | — |
| `preliminary_review`、`contract_highlights`、`technical_requirements`、`service_requirements` | list[str] | 1 元素 = 1 单元 | 字符串本身 |
| `evaluation_factors[]` | list[obj] | 配对后逐叶子 | `name` |
| `score_table[]` | list[obj] | 配对后逐叶子 | `factor` |
| `qualifications[]` | list[obj] | 配对后逐叶子（内含 `thresholds` 按 list[str] 处理） | `requirement` |
| `bid_format_items[]` | list[obj] | 配对后逐叶子 | `title` |

显式排除出 P/R 口径：`doc_id`、`doc_type`（输入回显，非抽取产物）与整个
`extraction_meta`（过程遥测；其中 `ungrounded_fields` 由溯源指标承接）。

### 3.2 比较规则

- **标量**：精确相等（`==`），不做语义/模糊匹配。空值（`None`/`""`/`[]`/`{}`）
  视为缺位：双方皆空不进统计（vacuous），避免空字段稀释指标；
- **list[str]**：按 multiset（`collections.Counter`）比较，重复元素按次数配对；
- **list[obj]**：先按身份键确定性配对，重复身份键按出现次序第 k 个配第 k 个；
  配对成功后逐叶子按标量/list[str] 规则比较；未配上的期望元素整条计 FN，未配上
  的实际元素整条计 FP（元素级，不再拆叶子，避免重复计数）；
- **类型不一致**（同路径两侧类型不同）：打 `type_mismatch` 标签，计 1 FP + 1 FN。

### 3.3 差异分类

| 类别 | 触发条件 | 计数 |
|---|---|---|
| `missing_field` | 期望非空、实际缺位 | FN ×1 |
| `spurious_field` | 实际非空、期望缺位 | FP ×1 |
| `value_mismatch` | 两侧皆非空但不等 | FP ×1 + FN ×1 |
| `element_missing` | 列表身份键仅期望侧存在 | FN ×1 |
| `element_spurious` | 列表身份键仅实际侧存在 | FP ×1 |
| `type_mismatch` | 同路径类型不一致 | FP ×1 + FN ×1 |

每条 issue 记录 `category/path/actual/expected`；对象叶子路径形如
`evaluation_factors[name=企业综合实力].score`，与列表顺序无关。

## 4. Precision / Recall / F1 口径

- TP：相等的标量、list[str] 交集元素、配对对象中相等且非空的叶子；
- FP：实际侧无对应（多余字段/多余元素/错误值的实际半边）；
- FN：期望侧无对应（缺失字段/缺失元素/错误值的期望半边）；
- `Precision = TP/(TP+FP)`，`Recall = TP/(TP+FN)`，
  `F1 = 2TP/(2TP+FP+FN)`；分母为 0 时 JSON 输出 `null`，终端显示 `n/a`。

值不匹配采用对称的 FP+FN 双计数（集合差口径），使 Precision 与 Recall 同时对
错误值敏感。

## 5. 未溯源字段率

- 指标层直接 import 并调用生产函数
  `src.extraction.extractor.ground_check(actual, tender.md 全文)`，不复制规则；
- **分母（证据单元）**：5 个非空基础标量 + 含非空 `source_quote` 的
  `evaluation_factors`/`qualifications` 元素，与 `ground_check` 现有契约一致；
- **分子**：`ground_check` 返回的未溯源标签数（如 `预算金额`、`评分因素[3]`、
  `资格条件[1]`）；
- 空 quote 的因素/资格不参与检查，仅以 `empty_quote_units` 单独披露；
  `preliminary_review`、`score_table`、`bid_format_items`、三类 requirements
  因 Schema 无 quote 槽位，整体不参与（扩大证据覆盖属于 Schema 变更，不在本任务）；
- 测试锁定：每份 Golden 样本重算的 `ungrounded_fields` 必须与
  `extraction_meta.ungrounded_fields` 完全一致。

## 6. 单样本与聚合

- 单样本输出 `counts(tp/fp/fn)`、`precision/recall/f1`、`issues` 与
  `grounding(evidence_units/ungrounded/ungrounded_rate/ungrounded_fields/empty_quote_units)`；
- **micro**：先汇总全部样本 TP/FP/FN 与证据单元再整体计算（按字段量加权）；
- **macro**：各样本比率等权平均（按文档加权），`null` 样本跳过；
- 当前 Golden 精确匹配下的锁定值：

| 样本 | TP | FP | FN | P/R/F1 | 证据单元 | 未溯源 | 未溯源率 |
|---|---|---|---|---|---|---|---|
| 01_engineering | 47 | 0 | 0 | 1.0 | 13 | 0 | 0.0% |
| 02_goods | 25 | 0 | 0 | 1.0 | 9 | 2 | 22.22% |
| 03_service | 31 | 0 | 0 | 1.0 | 8 | 1 | 12.50% |
| micro | 103 | 0 | 0 | 1.0 | 30 | 3 | 10.00% |
| macro | — | — | — | 1.0 | — | — | 11.57% |

## 7. 报告格式

终端在精确匹配汇总后追加固定列宽指标表（比率四位小数、百分比一位）；
`--out` JSON 升级为带版本号的信封：

```json
{
  "schema_version": 1,
  "samples": [
    {"name": "...", "passed": true, "diffs": [],
     "field_metrics": {"counts": {}, "precision": null, "issues": [],
                       "grounding": {}},
     "actual": {}}
  ],
  "aggregate": {"micro": {}, "macro": {}, "grounding": {"micro": {}, "macro": {}}}
}
```

报告为纯函数产物，无时间戳/随机数/环境变量，同一结果两次序列化字节一致。
T-020 的 `--out` 旧格式为裸列表；该文件 README 已声明不入库、无外部契约。

## 8. 离线、确定性与防篡改

- 沿用 T-020 `GoldenCaller` 与临时目录，不实例化真实 DeepSeek/Embedding/MinerU，
  不访问网络；指标是字典上的纯计算，`ground_check` 是纯字符串函数；
- 指标逻辑测试全部使用内联手写故障注入数据，与 Golden 期望文件物理隔离；
- 端到端测试锁定 §6 表中的绝对数值（TP 47/25/31、micro 103、未溯源 3/30 等），
  期望文件被篡改时要么精确匹配先红，要么锁定数值变红；
- 溯源重算一致性测试使期望文件无法虚构 `ungrounded_fields`；
- 代码库不存在任何写 `expected_extraction.json` 的路径，冻结流程仍受
  `eval/README.md` 第 5 节约束。

## 9. 验收标准

1. TDD：先写失败测试（模块缺失红盘），再实现转绿；
2. 新增 `tests/test_eval_metrics.py` 覆盖标量五种情形、multiset、重复元素、
   身份键配对、重复键按序配对、嵌套 thresholds、类型不一致、零分母、micro/macro、
   溯源口径与 Golden 锁定值、报告确定性，共 20 个用例；
3. T-020 既有 6 个 harness 测试零改动通过，3/3 精确匹配基线与退出码语义不变；
4. `uv run ruff check api src tests scripts eval`、`uv run pytest -q`、
   `uv run python -m eval.harness.runner` 全部通过；
5. 不改 `src/` 生产代码（仅 import `ground_check`），不涉及 API/DB/配置变更。