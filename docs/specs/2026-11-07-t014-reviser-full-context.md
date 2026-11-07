# 规格：Reviser 修订携带完整上下文（T-014）

## 1. 用户场景与业务目标

章节写作循环为「Writer → Reviewer → Reviser → Reviewer…」。当前 Reviser 的提示词
（`build_reviser_prompt(plan, draft, review)`）只包含章节标题、当前草稿与评审意见，
缺少 Writer 实际拥有的四类上下文：章节计划（策略/目标字数/写作要求）、评分因素、
检索原文片段、投标人资料与招标摘要。模型在修订时无法回看证据与事实边界，容易：

- 丢掉初稿中基于检索片段的有效内容或引用编号；
- 为回应评审意见而编造资料/业绩/报价（AGENTS.md 5.1 禁令）；
- 偏离章节计划与评分因素，越改越偏。

AGENTS.md 第 5.4 节要求：「Reviser 必须保留章节计划、评分因素、检索证据与投标人资料
上下文，不能只基于草稿和评审意见修改」。本任务把 Reviser 的输入对齐到 Writer 的
上下文级别；修订上限与最佳草稿保护已存在，本任务补测试锁定。

## 2. 输入、输出与副作用

- 提示词签名变更（内部函数，唯一调用方是 `agents._reviser`，无外部/HTTP 消费方）：

  ```python
  build_reviser_prompt(plan, draft, review, tender, profile, retrieved,
                       *, schema=None) -> list[dict[str, str]]
  ```

- `_reviser` 与 `section_agent` 调用链透传 `tender`、`profile`、`retrieved`、`schema`
  （`section_agent` 现有入参已全部具备，无需改公开签名）。
- 输出仍是修订后的章节 Markdown 字符串，模型协议、任务状态、HTTP API、数据库与
  产物结构均无变化。
- 系统提示词 `_REVISER_SYSTEM` 继承的 `_build_system_rules()` 已含不编造、报价占位、
  `[n]` 引用等硬规则，保持不变；用户消息新增上下文并明确「不得新增无检索依据的引用、
  保留有效引用编号与占位符」。

## 3. 修订提示词必须包含的上下文

用户消息按固定顺序包含：

1. 章节计划：标题、策略、目标字数、关联评分因素、写作要求；
2. 项目与评分摘要：复用 `_tender_summary(tender)`（项目名、招标编号、全部评分因素、
   初步评审项、技术需求）；
3. 投标人资料：复用 `_profile_text(profile, schema)`，保留必填/建议/可选标注；
4. 检索原文片段：与 Writer 完全相同的编号渲染 `[1] … [n]`，空列表显式输出
   「（无检索结果）」，禁止模型在无片段时编造引用；
5. 当前草稿与评审意见（得分、问题、建议），以及修订指令。

## 4. 正常、异常与边界路径

- 正常：评分低于 `min_score` 且 `revisions < max_revisions` 时，Reviser 带着完整
  上下文生成修订稿，再走 Reviewer 复评。
- 修订上限：`max_revisions=0` 时不产生任何 Reviser 调用；`max_revisions=N` 时
  最多 N 轮，即使分数始终不达标也停止（既有循环条件，补测试锁定）。
- 最佳草稿保护：复评分数低于当前最佳时不覆盖（既有 `>=` 策略，补测试锁定），
  返回的草稿始终是最高分版本。
- 无检索：`retrieved=[]` 时提示词显式标注无片段，修订只能基于已有资料与草稿。
- 占位章节：`placeholder` 策略不进入写作/修订循环，行为不变。
- 模型返回空内容：沿用 `_generate_text` 的 `BidWriteError`，行为不变。

## 5. 兼容性与验收标准

- 无 API/DB/产物 Schema 变化；`build_reviser_prompt` 为模块内部函数，全仓唯一调用方
  同步修改，不保留旧签名兼容层（无外部消费方，避免摆设接口）。
- 验收标准：
  1. `tests/test_prompts.py` 新增 Reviser 提示词断言：含评分因素名、项目名、投标人
     公司名、检索片段原文与编号、草稿文本、评审问题；空检索时出现「（无检索结果）」；
  2. `tests/test_bid_writer.py` 用记录消息的 Fake caller 断言：发生修订时 Reviser
     消息确实携带检索片段、资料与 tender 摘要；`max_revisions=2` 且持续低分时限两轮
     修订后停止；降分修订稿不覆盖最佳草稿（既有行为补断言）；
  3. 既有 292 个测试全部保持通过，不修改其断言语义；
  4. `docs/algorithms.md`（8.2 伪代码与设计要点补 Reviser 完整上下文）、`README.md`
     同步描述；
  5. `ruff`、后端全量测试、前端 `npm test` 与 `npm run build` 通过（前端无逻辑改动，
     仍跑一遍类型检查与构建门禁）。
