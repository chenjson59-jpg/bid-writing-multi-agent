# 规格：投标人资料表单瘦身与 prompt 外发白名单收敛（T-040）

## 1. 用户场景与业务目标

现状资料表单（frontend/src/pages/WritePage.tsx 的 buildProfile）收集 11 类字段，整包
写入 bidder_profile.data（JSON 列），并经 src/bid_writer/prompts.py:_profile_text 的
固定清单全部外发到 Planner/Writer/Reviser 的提示词。其中类似项目业绩、项目人员、财务
数据、联系方式属于高敏或单标书相关信息，跨标书复用价值低，且不应进入 LLM prompt。

T-039 标书级占位符填写工作台已落地（PR #27），高敏信息有了实例级填写入口（默认只写本
标书章节、不入库、不进 prompt），本任务将资料表单与 prompt 外发面同步收敛到低敏、跨标书
复用字段。

用户已拍板（2026-11-12，方案 B）：资料表单保留 7 个字段——

- core 5：company_name、legal_representative、registered_capital、
  established_date、address；
- 数组 2：qualifications、certifications。

移出表单与 prompt 外发的 4 个字段：similar_projects、personnel、financials、
contact。其填写去向为 T-039 工作台（实例级）。

业务目标：

1. 提示词外发面收敛为上述 7 字段白名单，移出字段即便存在于旧资料 JSON 也不再外发；
2. 前端表单不再采集移出字段，消除高敏数据的入库入口；
3. 不删除任何历史数据、不做数据库迁移，旧资料反序列化与资料库加载保持兼容；
4. 不引入新依赖，HTTP 路径/方法/鉴权不变。

## 2. 事实基线（2026-11-12 只读复核）

### 2.1 数据链路

1. 前端 WritePage.tsx:buildProfile() 组装对象 → POST/PUT /api/profiles；
2. 仓储 src/db/repository.py:save_profile/update_profile 将整包 dict 透传给
   BidderProfile.data（SQLAlchemy JSON 列，src/db/models.py），不做字段级校验；
3. 编写链路 BidderProfile.from_dict()（src/bid_writer/schema.py:129）用
   raw.get(key, 默认值) 构造 dataclass，忽略未知字段、缺失按空处理；
4. _profile_text(profile, schema) 将 core 5 标【必填】、other 6
   （qualifications/certifications/similar_projects/personnel/financials/contact）按
   schema 级别标【建议】/【可选】外发，末尾追加 notes 补充说明；
5. build_planner_prompt/build_writer_prompt/build_reviser_prompt 均以 {profile} 占位
   注入该文本（prompts.py 三处模板）。

### 2.2 无 Alembic、无表结构变更

data 为整包 JSON 列，删表单字段与收敛外发白名单均不触发表结构；旧 JSON 中的移出字段
原样保留在库中，from_dict 容错已具备，只需补测试锁定兼容行为。

### 2.3 writing_runner 冻结基线预计零漂移（修正任务卡第 4 条预设）

任务卡预设「白名单收敛必然改变 prompt 文本 → 六臂 token 失配 → 需重冻」。代码核实结论为
预计零漂移、无需预改基线，依据：

1. eval/harness/writing_runner.py:WritingCaller.generate 返回 fixtures 队列中的固定响应，
   UsageMeter.record 累加的 usage.prompt_tokens 取自夹具，不按真实 prompt 长度计费；
2. 角色识别 _ROLE_MARKERS 只匹配 system prompt 中的角色名（编写策划助手等），与 profile
   白名单无耦合，不会因白名单变化导致键控失败；
3. 三份 golden writing/profile.json 实际只含 company_name/legal_representative
   （03_service 为空 {}），均在保留白名单内，_profile_text 对这些样本的输出文本不变。

处置：实施后实跑 python -m eval.harness.writing_runner 验证六臂全匹配；若实跑失配（与
预期不符），再按任务卡原流程人工核对差异是否全部由白名单收敛引起，确认后重冻三份
expected_writing_metrics.json 并记录新旧数值对照，禁止静默改基线。抽取链路
eval.harness.runner 与本任务无交集，须保持三样本匹配。

### 2.4 前端对 profile-schema 的消费具备少键兼容

GET /api/documents/{doc_id}/profile-schema 响应 fields 为字典；WritePage 经
schemaItemOf(key)（返回 undefined 防御）与 levelOf 消费，T-039 工作台
placeholder_workbench.ts 同样按标记动态查键并有空 schema 防御。后端收缩 label_map 后
响应少键不会导致前端崩溃，但必须有回归测试锁定。

## 3. 字段决策清单

| 字段 | 表单 | prompt 外发 | 缺失提示 | schema 推断 | 旧数据存储/dataclass |
|---|---|---|---|---|---|
| company_name | 保留 | 保留（必填） | 保留 | 保留（core） | 保留 |
| legal_representative | 保留 | 保留（必填） | 保留 | 保留（core） | 保留 |
| registered_capital | 保留 | 保留（必填） | 保留 | 保留（core） | 保留 |
| established_date | 保留 | 保留（必填） | 保留 | 保留（core） | 保留 |
| address | 保留 | 保留（必填） | 保留 | 保留（core） | 保留 |
| qualifications | 保留 | 保留（按 schema 级别） | 保留 | 保留 | 保留 |
| certifications | 保留 | 保留（按 schema 级别） | 保留 | 保留 | 保留 |
| similar_projects | 移除 | 停止外发 | 移除 | 移除 | dataclass/JSON 保留 |
| personnel | 移除 | 停止外发 | 移除 | 移除 | dataclass/JSON 保留 |
| financials | 移除 | 停止外发 | 移除 | 移除 | dataclass/JSON 保留 |
| contact | 移除 | 停止外发 | 移除 | 移除 | dataclass/JSON 保留 |
| notes | 表单本无独立录入 | 建议同步停止外发（见 3.1） | 本就不参与 | 不涉及 | dataclass/JSON 保留 |

### 3.1 待评审确认项：notes 停止外发

notes 为自由文本数组，前端表单无独立录入入口（仅存在于旧数据/CLI JSON），当前
_profile_text 末尾会原样外发，存在不可控文本进入 prompt 的风险，与本任务治理目标一致。
建议本任务同步停止 notes 外发（dataclass 字段、旧 JSON 与 from_dict 兼容保留，缺失判定
维持不参与）。评审可否决；若否决，notes 外发逻辑保持不动并在完成记录中说明。

## 4. 输入输出与副作用

### 4.1 后端改动点

1. src/bid_writer/prompts.py：_profile_text 的 other_fields 收缩为
   qualifications/certifications 两项；删除四个移出字段的外发行；_SCHEMA_LABEL_CN 同步
   收缩（仅保留实际外发键）；按 3.1 决策处理 notes 行。函数签名与返回类型不变。
2. src/bid_writer/schema.py：PROFILE_FIELD_LABELS、MISSING_FIELD_ORDER 收缩为 7 键；
   BidderProfile dataclass 字段一个不删（旧 JSON 反序列化依赖），
   missing_fields() 随 MISSING_FIELD_ORDER 自然收缩。
3. src/bid_writer/profile_schema.py 与 api/library.py：infer_profile_schema 遍历的
   label_map 由路由传入，路由改传收缩后的 PROFILE_FIELD_LABELS 后响应自然只含 7 键；
   _FIELD_RULES 中四个移出字段的规则块同步删除（避免死规则误导），_CORE_REQUIRED_KEYS
   不变。
4. HTTP 协议：路径、方法、请求体、鉴权均不变；profile-schema 响应 fields 字典减少
   4 个键属响应内容收缩，前端已具备动态兼容（2.4）。

### 4.2 前端改动点

1. WritePage.tsx：删除业绩/人员/财务/联系方式四个结构化区块的 UI、state、行模型
   （FinancialsForm/ContactForm 等 interface 与 empty* 常量）、compactRows/compactForm
   相关收集、buildProfile 中四段构造、PROFILE_FIELD_ORDER 收缩为 7 项、
   ADVANCED_FIELD_KEYS 及 precheck 高级字段引导逻辑、goToField 两个分支；
2. frontend/src/types.ts：BidderProfile interface 保留全部旧字段（资料库载入旧
   资料、编辑回显不报错），仅表单不再采集；
3. T-039 工作台（PlaceholderWorkbench.tsx/placeholder_workbench.ts）逻辑不改；其低敏
   白名单 5 字段是保留 7 字段的子集，profile-schema 少键后跑回归测试验证；
4. 不引入依赖、不改 Electron、不改 Vite 配置。

### 4.3 文档与示例

1. docs/api-contract.md §9：9.1 字段表标注各字段外发状态与去向、9.2 缺失判定收缩为 7 项、
   9.3 归一化规则删除高级字段相关条目、附录 B 示例同步；
2. README.md 资料库/编写流程描述与实际保留字段对齐；
3. examples/bidder_profile.example.json 与 examples/demo/bidder_profile.demo.json：
   精简为 7 字段示例（旧字段兼容能力由测试承载，示例不再演示高敏入库）。

## 5. 正常 / 异常 / 边界路径

1. 正常：新表单提交只含 7 字段内数据；编写 prompt 仅出现白名单字段；报告
   missing_profile_fields 只列 7 字段内的缺失项。
2. 旧资料兼容：库中旧 JSON 含全部 11+notes 字段时，资料库列表/载入/编辑不报错；
   from_dict 正常构造；_profile_text 输出不含四个移出字段与 notes（按 3.1）的值，
   断言具体高敏值字符串不出现在 prompt 文本中。
3. 仅含旧字段的 profile（无任何白名单值）：prompt 白名单行以「（空）」占位，不抛异常。
4. profile-schema 少键：WritePage.levelOf/schemaItemOf 与工作台 canReuseToProfile 在缺
   键时走既有 optional/空 schema 防御，UI 不崩。
5. CLI --profile 指向含旧字段的 JSON：兼容加载，旧字段不外发。
6. 空 profile {}：行为与现状一致（全空占位），golden 03 样本覆盖。

## 6. 权限与安全

- 不新增接口、不改鉴权与令牌机制；
- 本任务是收窄数据外发面：以测试断言 financials/contact/similar_projects/personnel 的
  具体值不可能出现在三类 prompt 文本中；
- 不删除、不迁移库内历史数据，避免破坏性操作；高敏旧数据的物理清理不在本任务范围
  （如需清理另立任务，需单独授权与备份方案）；
- 无密钥、服务器地址、Electron 信任域变化。

## 7. 测试计划（严格 TDD，先红后绿）

### 7.1 后端 pytest（离线、tmp 目录、无 LLM/网络）

1. tests/test_prompts.py 新增：构造含全部移出字段及具体值的 BidderProfile，断言三类
   prompt 文本不含这些值与字段标签；白名单 7 字段正常出现；（按 3.1）notes 值不外发；
2. tests/test_profile_schema.py 调整：infer_profile_schema 结果键集合恰为 7 个；
3. schema 测试：missing_fields 只可能返回 7 字段中文名；from_dict 喂入含全部旧键的
   dict 不报错且旧值可在 dataclass 实例上读取（兼容锁定）；
4. 既有测试中引用移出字段的断言同步修正，禁止跳过或删除有效断言。

### 7.2 前端 vitest（vi.mock('../src/api') 全离线、fake timers、fireEvent）

1. WritePage 渲染测试（当前为空白覆盖，本任务补齐）：不渲染四个移出字段区块；
2. 表单交互后 buildProfile 产出对象不含移出键；
3. 工作台回归：profile-schema 仅返回 7 键时，低敏白名单标记仍出现复用勾选、其余不出现。

### 7.3 全量门禁

- uv run --frozen ruff check api src tests scripts eval 全过；
- uv run --frozen pytest -q 全绿，用例数只增不减（修正既有断言除外，数量变化需说明）；
- eval.harness.runner 三样本匹配；eval.harness.writing_runner 六臂全匹配（预期零漂移，
  失配则按 2.3 流程重冻并记录对照）；
- npm test 全绿、npm run build 通过（tsc --noEmit）、无 @ts-ignore；
- uv.lock 不暂存不提交，无依赖增删。

## 8. 验收标准

1. 规格经用户评审确认（含 3.1 notes 决策）后再写实现；
2. 前端表单仅含 7 个白名单字段，移出字段无任何录入入口；
3. 三类 LLM prompt 不可能出现移出字段值（有确定性测试断言）；
4. 旧 profile JSON 反序列化、资料库加载/编辑、CLI 加载均兼容，有测试锁定；无 Alembic；
5. writing_runner 实跑零漂移并在完成记录写明依据；若重冻则附新旧数值对照与成因说明；
6. 全量门禁绿、无 @ts-ignore、无新依赖；api-contract/README/示例同步；
7. 手动验证范围：资料库新建/编辑页面走查（表单字段、precheck 提示）、T-039 工作台回归
   走查，结果写入工作日志；不起真实 LLM。

## 9. 不做项（边界）

- 不删除/迁移/清洗库内历史高敏数据；
- 不改 T-039 工作台交互与批量回填接口；
- 不改 HTTP 路径、鉴权、任务状态协议、Electron 信任配置；
- 不做真实模型效果评测（属 eval/人工评测范畴）；
- T-037 等 P3 卫生项不在本任务夹带。