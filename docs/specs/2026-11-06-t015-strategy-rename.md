# 规格：章节策略 fill_template 改名为 llm_fill（T-015，路线 A）

## 1. 用户场景与业务目标

`fill_template` 策略名义上是"确定性模板填充"，实际代码路径与 `generate`
一样走 LLM Writer 自由生成（仅标题关键词不同、仍参与检索），名实不符，
违反 AGENTS.md 第 5.5 节。路线 A 将其改名为与真实行为一致的 `llm_fill`，
不引入确定性模板引擎，不改变任何运行时行为。

## 2. 输入、输出与副作用

- 策略枚举（`src/bid_writer/schema.py`）：
  - `generate`：检索原文生成长正文；
  - `llm_fill`（新名，替代 `fill_template`）：LLM 按固定格式填写投标函、
    授权委托书、声明等短章节，仍走 Writer 与检索；
  - `placeholder`：确定性占位 `【待填写：…】`，不调模型。
- 输出：`outline.json`、`report.json`、sections 索引中的 `strategy` 字段
  新写入值为 `llm_fill`；Planner 提示词枚举同步更新。
- 副作用：无数据库、无接口 Schema 变化；HTTP 响应中的 strategy 字符串取值变化。

## 3. 正常、异常与边界路径

- 正常：`choose_strategy("投标函")` 等命中 `_FILL_KEYWORDS` 的标题返回 `llm_fill`；
  Planner LLM 返回 `llm_fill` 时原样接受。
- 旧数据兼容：磁盘旧产物（`outline.json`、sections 索引、`report.json`）中
  的 `fill_template` 在反序列化时统一归一化为 `llm_fill`，补写（supplement）、
  章节重写与前端展示不出现旧值裸奔。
- 异常/边界：
  - LLM 返回未知 strategy 字符串：Planner 解析路径回退为
    `choose_strategy(title)`，不得因脏值变成占位章节而吞掉正文生成；
  - 反序列化路径遇到未知值：归一化为 `placeholder`（与 SectionPlan 默认值一致）；
  - `None`/空串：按各自调用点既有默认逻辑处理。

## 4. 兼容性

- 旧值 `fill_template` 保留为遗留别名，仅在归一化函数中识别，新代码不再写出；
- `SectionPlan.__post_init__` 统一归一化，覆盖所有构造入口（含
  `SectionPlan(**dict)` 磁盘还原）；
- 前端标签映射同时识别新旧两个键，旧产物未经后端重写也能显示中文标签。

## 5. 验收标准

1. 代码与测试中不存在把 `fill_template` 当作当前能力写出的地方（遗留兼容映射与文档修订记录除外）；
2. `normalize_strategy` 对新值、旧值、未知值、None 的行为有单元测试锁定；
3. 旧产物（sections 索引与 outline.json 含 `fill_template`）可正常加载且 strategy 归一化为 `llm_fill`；
4. Planner Fake 返回旧值/未知值的路径有测试；
5. 提示词、README、docs、前端标签、demo 产物全部同步；
6. `ruff`、后端全量测试、前端测试与构建通过。
