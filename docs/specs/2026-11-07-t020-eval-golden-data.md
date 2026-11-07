# 规格：eval/ Golden Data 评测集与离线评测底座（T-020）

## 1. 用户场景与业务目标

当前离线测试全部是单元/集成测试（Fake Caller + 局部夹具），缺少**固定样本驱动的
端到端回归基线**：抽取链路（单元切分 → 提示词 → 归一化 → 合并 → 溯源核对）一旦改坏，
只能靠分散的单测间接发现。AGENTS.md 4.2 要求真实模型效果放入 `eval/`，与单元测试隔离。

T-020 建立评测底座：手工构造小而精的中文合成招标样本（不超过 10 份，本任务交付 3 份
覆盖工程/货物/服务三类采购场景），每份包含「解析后 Markdown 输入 + 脚本化模型夹具 +
人工核定的期望抽取产物」，配套离线 harness 可重复运行、零网络依赖。T-021/T-022 在此
底座上叠加指标，本任务只建数据集、harness 与精确匹配基线，不实现指标计算。

## 2. 目录结构与输入输出

```text
eval/
├── README.md                     # 方法论、合成声明、扩样规范、运行方式
├── harness/
│   ├── __init__.py
│   ├── loader.py                 # 样本加载、Markdown→ParsedDocument、GoldenCaller
│   └── runner.py                 # 离线跑 run_extraction、投影对比、CLI 入口
└── datasets/golden/
    ├── 01_engineering/
    │   ├── meta.json             # 样本元信息与场景说明
    │   ├── tender.md             # 手工构造的招标 Markdown（解析产物形态）
    │   ├── llm_fixtures.json     # 按章节标题键控的脚本化模型返回（可含脏数据）
    │   └── expected_extraction.json  # 人工核定的期望 TenderDocument 稳定投影
    ├── 02_goods/
    └── 03_service/
```

- **输入**：`tender.md`（`# 第X章 标题` 一级标题分章）与 `llm_fixtures.json`
  （键为精确章节标题，值为该章按调用顺序消费的 `generate()` 返回列表，支持模拟重试）；
- **输出**：runner 对每份样本输出实际/期望的字段级 diff；全部匹配退出码 0，失配退出码 1；
  `--out PATH` 可选写 JSON 报告，默认不落盘（评测结果不入库）；
- **副作用**：harness 在临时目录运行 `run_extraction`（缓存与落盘限定在
  `tempfile.TemporaryDirectory`），不写 `upload/`、不碰 `chroma_db/`。

## 3. 关键设计

### 3.1 Markdown → ParsedDocument（loader）

- 仅识别 `# ` 一级标题作为章节（标题须匹配 `第X章` 才会被 `chapter_indices` 选为抽取
  单元，与生产逻辑一致）；`##` 等下级标题保留在章节正文内；
- 每章生成 `TocEntry(level=1)` 与等长 `Section`，页码占位为 1；`ParsedDocument` 的
  doc_id/doc_type/parser 取自 `meta.json`；
- 样本章节正文均远小于 `MAX_CHARS_PER_CALL=12000`，保证每章恰好 1 个抽取单元，
  夹具键控关系稳定。

### 3.2 GoldenCaller（脚本化模型，零网络）

- 实现 `ModelCaller` 协议：从 `build_prompt` 用户消息首行 `章节：{面包屑}` 解析当前
  单元标题（一级章节面包屑即标题本身），按标题取夹具队列，每次调用弹出队首；
- 队列耗尽或标题无夹具：抛 `KeyError` 并明确提示缺失键，使数据集漂移显式失败；
- 夹具可表达：脏 JSON/代码围栏（`extract_json` 容错）、空响应（触发 `_extract_unit`
  重试）、`"30分"`/`"是"/null` 等归一化边界、缺 name 条目（被丢弃）。

### 3.3 稳定投影与精确匹配（runner）

- 实际产物与期望文件都经同一投影函数剔除易变键后再比较：
  - 剔除 `extraction_meta.elapsed_seconds`、`cache_hit`、`model`、`content_sha256`；
  - 保留并比较 `calls`、`json_mode_used`、`ungrounded_fields`、`warnings`、
    `prompt_version` 与全部业务扁平字段；
- 期望文件由「先运行、再人工对照 `tender.md` 原文逐项核定、最后冻结」产生，禁止盲快照；
  冻结流程与扩样规范写入 `eval/README.md`。

### 3.4 三份样本的覆盖意图

| 样本 | 场景 | 专门覆盖 |
|---|---|---|
| `01_engineering` | 工程施工，干净基线 | 5 评分因素 + 资格 thresholds + 格式条目；全部 source_quote 逐字可溯源，期望 `ungrounded_fields=[]` |
| `02_goods` | 货物采购，脏数据归一化 | `"30分"`→数值、`null` 列表、basic 包在子对象、缺 name 因素丢弃、1 条假 quote → 期望出现未溯源因素与预算字段 |
| `03_service` | 服务/培训，容错与缺字段 | 代码围栏 JSON、空响应重试、服务/师资类别、basic 缺字段为空串、1 条不可溯源资格 |

## 4. 正常、异常与边界路径

- 正常：`uv run python -m eval.harness.runner` 三份样本全匹配，退出码 0 并打印汇总；
- 失配：任一字段不一致，打印样本名 + 字段路径 + 实际/期望，退出码 1；
- 数据集缺文件/JSON 非法：loader 抛带样本名的明确错误；
- 新增样本：放入 `datasets/golden/` 并在 `meta.json` 声明，runner 自动发现，无需改代码；
- 与单测隔离：pyproject `testpaths=["tests"]` 不变，pytest 不收集 `eval/`；harness 自身
  的机械正确性（loader 解析、GoldenCaller 队列、投影函数）由 `tests/test_eval_harness.py`
  用内联迷你样本覆盖。

## 5. 兼容性与验收标准

1. `tests/test_eval_harness.py` 先红后绿，覆盖：Markdown 分章、夹具队列消费/重试/
   缺键报错、投影剔除易变键、runner 对失配返回非零；
2. 三份 Golden 样本在 runner 下全匹配；`uv run pytest -q` 全量通过且数量只增加 harness 单测；
3. CI 后端任务新增「Golden Data 离线评测」步骤；Ruff 检查范围加入 `eval`；
4. AGENTS.md 4.3 验证命令与 README 增补 eval 运行方式；
5. 全程离线：不实例化真实 DeepSeek/Embedding/MinerU，不访问网络，不触碰 `chroma_db/`；
6. 全部样本为虚构合成数据，`eval/README.md` 含合成与脱敏声明。
