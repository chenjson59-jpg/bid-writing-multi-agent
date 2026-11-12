# 规格：标书级占位符填写工作台（含批量回填接口、人工填写加锁确认、下载残留警告）（T-039）

## 1. 用户场景与业务目标

标书生成结束后，正文中的 `【待填写：…】` 占位符只能由人工填写（AGENTS 5.1：报价、
资质、业绩等禁止模型虚构，资料缺失只能产出占位符）。现状只支持在 `ResultView` 的单章
编辑器里逐章保存：`PUT /bids/{id}/sections/{sid}` 每次保存都执行 `save_section` →
`rebuild_artifact`，触发一次 Markdown 重合并、docx 重导与 report.json 重写。真实标书
占位符量大，逐章处理不可用；前端若循环调用单章 PUT，N 章保存会触发 N 次 docx 重导。

用户已确认的产品决策（侧边对话 2026-11-11，主对话 2026-11-12 复核修正）：

1. 以**标书级占位符填写工作台**为主入口，吸收「章节内高亮/跳转」作为辅助入口；不引入
   富文本依赖；
2. 高敏且单标书相关信息（报价、工期、开竣工日期、合同信息、银行账号等）走**实例级
   填写**：默认只写入本标书章节文件，不进 `bidder_profile` 库、不进任何 LLM prompt；
   「存入资料库复用」是默认不勾的可选项；
3. 工作台保存触及人工填写章节时，**弹窗明示并由用户确认后才加锁**，不静默自动锁；
4. 未填完**不阻断下载**，在下载入口与报告区给出剩余占位符数量的醒目警告。

业务目标：一个去重标记对应一个输入框、全文一次替换；一次保存只产生一次产物重建；
填写链路全程离线确定性执行、无 LLM 调用、无敏感数据外泄面。

## 2. 事实基线（只读复核，2026-11-11/12）

- 对 5 份非空本地产物实测：单份占位符 21–159 个，去重后 15–88 种；行内占位占比
  95.2%–99.4%；跨章节重复标记仅 2–4 种，但同章节内同标记重复普遍，单章最高出现
  12 次（`07-section_7.md`，标记为带标签的 `【待填写：实测值】`）。
- **不存在真正的空标记**（6 份产物中 `【待填写：】` 计数为 0）。侧边对话「空占位单章
  12 次、需按章节+序号拆分输入框」的前提与实测不符，本规格不采纳序号拆分方案。
- 正确聚合语义：按**完整标记文本去重**，一个标记一个输入，服务端对每章做全文替换，
  同标记的全部实例天然一次覆盖；空标记仅作为防御性边界写入第 5.2 节校验规则。
- 同源正则已存在：`src/bid_writer/schema.py:36` `PLACEHOLDER_RE = 【待填写[^】]*】`，
  `extract_placeholders()` 已做按首次出现顺序去重；前端须定义同形正则并在注释中互相
  指向，禁止另立口径。
- 可复用现成能力：
  - `GET /bids/{id}/sections` 返回 `SectionInfo`，含 `placeholder_count`、`locked`；
  - `GET /bids/{id}/sections/{sid}` 返回含 `markdown` 的 `SectionDetail`（只读，不触发
    重建）；
  - 章节锁机制已存在：index.json 的 `locked` 字段 + `save_section(locked=...)`，本任务
    复用而非新建；
  - 报告级占位符口径为 `summary()['placeholder_count'] = len(report.placeholders)`，即
    **去重标记种数**（`BidReport` 本身无 placeholder_count 属性，章节级
    `SectionInfo.placeholder_count` 同为 `extract_placeholders` 去重种数）；
    `ResultView` 的「占位符」指标已展示该值，下载警告无需新统计接口；实例总数仅由
    前端工作台聚合函数按章节正文自行统计，不进入报告口径；
  - 产物文件下载白名单在 `api/library.py:_ALLOWED_BID_FILES`，本任务不新增下载入口。

## 3. 范围与非目标

**范围内**

1. 新增**一个** HTTP 批量回填接口（含请求/响应 Schema、路由、服务端纯函数）；
2. 前端新增占位符聚合纯函数模块、工作台组件（挂在 `ResultView` 内）、保存确认弹窗、
   章节编辑器内标记高亮与跳转、下载残留警告；
3. README 与 `docs/api-contract.md` 文档同步；
4. 后端 pytest（tmp 产物目录、全离线）与前端 vitest（mock api、假定时器、fireEvent）。

**非目标（明确不做）**

- 不引入富文本/ contentEditable 编辑，不新增任何前端依赖；
- 不做空标记的「章节+出现序号」拆分输入；
- 不改数据库表结构、不碰 Alembic、不改任务状态协议、不改 Celery/local 分发；
- 本接口不写 `bidder_profile`、不调用任何 LLM/检索；资料复用复用既有 profile 接口；
- 不做 T-040 的资料表单瘦身与 prompt 白名单收敛、不做 writing_runner 重冻；
- 不新增 GET 接口（数据源限定既有两个 sections 接口）；
- 不做实时推送、不做跨产物的填写模板。

## 4. 总体方案与数据流

```text
工作台打开：
  GET sections（placeholder_count/locked）→ 对 placeholder_count>0 的章节逐个
  GET sections/{sid}（只读）→ 前端同源正则聚合去重标记 → 渲染输入框列表

保存（用户填了至少一个值）：
  前端组装 items（仅含非空输入）→ 弹窗明示受影响章节与加锁后果 → 用户选择：
    取消 / 仅保存不加锁(lock=false) / 保存并加锁(lock=true)
  POST /bids/{id}/sections/fill-placeholders（一次请求）
    服务端：全量校验（不过则零写入）→ 逐章 str.replace 全部实例 →
    tmp+原子替换落盘 → index.json 一次性更新（edited/locked）→
    仅调用一次 rebuild_artifact（bid.md/bid.docx/report.json 整体刷新）
  前端：用响应 summary 刷新既有 override 与章节列表，剩余计数即时更新
```

## 5. HTTP 接口设计（新增，属 Schema 变更，按 AGENTS 3.1 先规格后 TDD）

### 5.1 路由与模型

- 路由：`POST /api/bids/{bid_id}/sections/fill-placeholders`
  （命名与既有 `POST .../sections/rewrite` 同级；`bid_id` 沿用 `_BID_ID_PATTERN`）
- 请求模型（`api/schemas.py` 新增）：

```json
{
  "items": [
    { "marker": "【待填写：投标报价】", "value": "人民币壹佰万元整", "section_ids": null }
  ],
  "lock": true
}
```

| 字段 | 约束 |
|---|---|
| `items` | 1..100 项；`marker` 1..200 字符；`value` strip 后 1..2000 字符；`section_ids` 为 null 或缺省表示全文，否则 1..N 个章节 id |
| `lock` | 布尔，默认 false；true 时对**实际发生替换**的章节加锁 |

- 响应模型（新增 `PlaceholderFillResponse`）：

```json
{
  "summary": { "……": "report.summary() 原样返回，结构同单章 PUT" },
  "filled": [
    { "marker": "【待填写：投标报价】", "replacements": 3, "section_ids": ["section_3", "section_7"] }
  ],
  "skipped_locked": [
    { "marker": "【待填写：工期】", "section_ids": ["section_9"] }
  ],
  "remaining_placeholder_count": 12
}
```

### 5.2 校验规则（先全量校验，任一不过则零写入，保证原子性）

1. `marker` 必须被 `PLACEHOLDER_RE.fullmatch` 完整命中，否则 422；标记只用于内存文本
   替换，**永不参与文件路径拼接**；
2. `value` strip 后非空，且 `PLACEHOLDER_RE.search(value)` 必须为 None（禁止回填值本身
   再引入占位符），否则 422；
3. `section_ids` 中每个 id 必须存在于 `sections/index.json`，否则 422（白名单校验，
   杜绝注入/路径穿越；既有 `_body_path` 的 `Path(name).name` 校验继续保留为纵深防御）；
4. 同一请求内 `marker` 不得重复，否则 422（语义歧义）；
5. `marker` 必须在产物至少一个章节正文中实际存在（含锁定章节），否则 422；
6. 某标记的全部命中章节都处于锁定状态时记为 no-op：单项计入 `skipped_locked` 不报错；
   但当**所有 items 均为 no-op**（没有任何可写替换）时整体 422，错误信息列明锁定章节；
7. 真正的空标记 `【待填写：】` 本期不特殊处理：它与其他标记同形、按完整文本去重，
   一个输入框替换全部同名实例；未来若出现需要差异化填写的空标记，另立任务做序号拆分。

### 5.3 服务端处理流程（`src/bid_writer/sections.py` 新增纯函数）

新增 `fill_placeholders(bid_dir, items, *, lock=False) -> dict`，职责：

1. `ensure_sections(bid_dir)`（旧产物自动反推补齐的既有行为不变）；
2. 加载 index 与各章正文，完成 5.2 全部校验（此阶段只读）；
3. 按 item 计算写入计划：候选章节 = 白名单章节或全部章节；其中正文含标记的未锁定
   章节为写入目标，命中的锁定章节进 `skipped_locked`；
4. 对每个写入目标章节，按 items 顺序对正文做 `str.replace(marker, value)`（同章全部
   实例一次替换），并在 index entry 标记 `edited=True`；
5. 章节正文统一走「临时文件 + 原子替换」落盘（不接受直接覆写）；`lock=true` 时把
   实际写入章节的 `locked` 置 true；index.json 全量重写一次；
6. **仅调用一次** `rebuild_artifact(bid_dir, extra_warnings=...)`，warning 注明本次
   填充标记数与加锁章节数；
7. 汇总响应：`filled`（含每个标记的替换实例数与实际写入章节）、`skipped_locked`、
   `remaining_placeholder_count`（取重建后 `len(report.placeholders)`，去重种数口径，
   等同 `summary.placeholder_count`）与 `summary`。

路由层 `api/routes.py` 只做参数解析与 `BidWriteError → HTTPException` 映射：产物不存在
404，校验类错误 422（与现有单章 PUT 的错误码风格一致）。

### 5.4 错误码一览

| 场景 | 状态码 |
|---|---|
| 产物目录不存在 | 404 |
| items 为空、marker 不合规、value 空/含占位符、section_id 不存在、marker 重复或不存在、全部命中均锁定 | 422 |
| 校验通过、部分命中在锁定章节被跳过 | 200（明细在 skipped_locked） |
| 正常完成 | 200 |

## 6. 前端设计

### 6.1 聚合纯函数（新增 `frontend/src/lib/placeholder_workbench.ts`，可单测）

- 定义与后端同形的正则 `/【待填写[^】]*】/g`，注释指向 `schema.py:36`，禁止分叉；
- `aggregatePlaceholders(details: SectionDetail[])`：按完整标记文本去重、按首次出现
  顺序返回 `MarkerAgg[]`：`{ marker, total, sections: [{id,title,count,locked}] }`；
- `buildFillItems(aggs, values)`：只收集非空输入，输出接口请求体 items。

### 6.2 工作台组件（新增 `pages/PlaceholderWorkbench.tsx`，挂在 ResultView 内）

- 入口：ResultView 章节卡片旁新增「占位符填写工作台」按钮，展开/折叠工作台区块
  （不新增顶层 tab，保持单页内存态语义，不引入 localStorage）；
- 打开时先取 `listSections`，仅对 `placeholder_count>0` 的章节并发一次 `getSection`
  （只读请求，不触发重建），会话内缓存；
- 每个去重标记一行：标记文本、总出现次数、涉及章节名、一个普通文本输入（无富文本）；
  同标记多处实例共享该输入；
- 全部输入为空时保存按钮禁用；保存中按钮 busy、失败走既有 `describeError` 横幅。

### 6.3 加锁确认弹窗（不静默自动锁）

点击保存弹出确认框，明示：将填写 N 个标记、触及 M 个章节、各章节当前锁定状态。
三个动作：

- **取消**：不发请求、不写不锁；
- **仅保存不加锁**：`lock=false`；
- **保存并加锁**（推荐主按钮）：`lock=true`，对实际写入章节加锁。

### 6.4 高敏数据与资料库复用边界

- 批量回填接口与工作台默认数据流只写本标书章节文件；请求体不含 profile 标识，服务端
  不 import 资料仓储、不构造任何 prompt；
- 「存入资料库复用」按标记行展示勾选框，**默认不勾**；仅当标记标签能匹配
  `getProfileSchema` 返回的既有低敏字段时才显示勾选，报价/工期/开竣工/合同/银行账号等
  高敏标记不显示该勾选；
- 勾选数据在回填接口成功返回后，由前端**单独**调用既有 `POST/PUT /profiles` 完成；
  未勾选时前端测试断言不产生任何 profile 请求；该映射在 T-040 白名单收敛后以 T-040
  为准，本期不改动 profile 接口与 Schema。

### 6.5 章节内高亮与跳转（辅助入口）

- 既有单章编辑弹窗中，把正文里的占位标记用轻量 `<mark>` 样式包裹（按正则 split 渲染，
  无 contentEditable、无新依赖）；
- 点击标记跳转到工作台并聚焦对应输入框（同页滚动 + focus，不引入路由）。

### 6.6 下载残留警告（不阻断）

- `ResultView` 三个下载按钮（Markdown / Word / 覆盖报告）上方与报告指标区，当
  `placeholder_count > 0` 时展示 amber 警告横幅：「产物仍含 N 种占位符未填写，下载
  文件将保留占位标记，请先在工作台填写」（N 取报告去重种数口径；工作台内部可另展示
  实例总数，但警告横幅与既有指标保持同口径）；为 0 时不渲染任何警告；
- 不拦截下载点击、不改下载接口与白名单。

## 7. 安全与合规

- 路径安全：marker 不入路径；section_id 走 index 白名单 + 既有文件名校验；bid_id 走
  既有正则 pattern；
- 数据安全：本链路无 LLM、无 Embedding、无外部网络；高敏实例数据默认不出本产物目录；
  对应 AGENTS 5.1 报价/资质禁虚构，填写只能来自人工输入；
- 原子性：校验失败零写入；正文落盘 tmp+replace；index 与产物重建顺序固定，异常时保留
  上一版 report.json/docx（重建异常沿用既有 warning 降级，不吞异常）；
- 不新增密钥、配置项、环境变量与依赖。

## 8. 兼容性

- 纯增量接口 + 增量前端组件：既有单章 GET/PUT、rewrite、下载、任务协议、DB 结构全部
  不变；无 Alembic、无 uv.lock/ package-lock 变化；
- 旧产物（无 sections/）经既有 `ensure_sections` 反推后同样可用工作台；
- `report.json` 结构不变（复用 summary），旧前端忽略新接口无影响；
- 不影响 eval 冻结基线：本任务不改 prompt、不改 Writer/Reviewer/Reviser 行为，两个
  eval harness 预期零漂移，仍须实跑确认。

## 9. 测试计划（严格 TDD，先红后绿）

### 9.1 后端（`tests/test_sections.py`、`tests/test_api.py`，tmp 产物目录、全离线）

1. 红灯先行：`fill_placeholders` 与路由不存在时新测试全部失败；
2. 正常路径：多章节多标记一次回填，断言**只调用一次** `rebuild_artifact`
   （monkeypatch 计数）与一次 docx 导出；同标记多实例全部替换；响应
   replacements/section_ids/remaining_placeholder_count 准确，可到 0；
3. 校验路径：marker 不合规、value 为空/纯空白/含占位符、section_id 不存在、marker
   重复、marker 不存在、items 为空 → 422；
4. 锁定语义：锁定章节跳过并计入 skipped_locked；全部命中锁定 → 422；`lock=true` 后
   实际写入章节 locked=true 且 edited=true，`lock=false` 不改锁定；
5. 原子性：构造校验失败请求，断言所有章节 md、index.json、report.json 内容与 mtime
   语义不变（无写入痕迹）；
6. 安全：恶意 marker（含 `../`、路径分隔符）只作为文本处理、不产生文件访问；
7. 无 LLM：断言执行路径不实例化/调用 ModelCaller（spyon default_caller 零调用）；
8. 旧产物：仅 bid.md/outline.json 的产物经 ensure_sections 后回填成功；
9. API 层 TestClient：404/422/200 状态码与响应体字段。

### 9.2 前端（vitest，沿用 `vi.mock('../src/api')` 全离线、fake timers、fireEvent）

1. 纯函数：去重、计数、首次出现顺序、锁定章节标注、空详情边界；
2. 组件：夹具下每个去重标记恰好一个输入；全空时保存禁用；弹窗三动作分别产生
   lock=true / lock=false 请求体与取消零请求；一次保存只发一次 fill 请求；
3. 复用勾选默认不勾时断言零 profile 调用；勾选后回填成功才调既有 profile 接口；
4. 警告横幅：placeholder_count>0 显示且含数量，=0 不渲染；下载按钮仍可点击；
5. 高亮渲染与点击聚焦跳转；
6. 既有 167 个用例只增不减，`npm run build` 通过、零 `@ts-ignore`。

## 10. 文档与交付物

- `README.md` 桌面前端章节增补工作台工作流与「不阻断下载、残留警告」语义；
- `docs/api-contract.md` 增补新接口契约、错误码与本地存储口径（不新增本地存储键）；
- 提交序列建议：test 红灯提交 → 后端实现提交 → 前端实现提交 → 文档提交；
- 手动 E2E 步骤写入 `工作日志.md`，未执行前不得标注「已验证」。

## 11. 验收标准

1. 规格经用户评审通过后才动工；后端/前端各自先红后绿；
2. N 章填充全程一次请求、服务端一次 `rebuild_artifact`、一次 docx 重导，有测试锁定；
3. 5.2 全部异常/边界路径有 422/跳过语义与测试；原子性与路径安全有测试；
4. 高敏数据不入库不进 prompt 有前后端测试锁定；加锁必须经弹窗确认，无静默锁；
5. 下载不阻断且残留警告在 count>0 的三个入口与报告区可见，count=0 不打扰；
6. `ruff check`、`pytest -q`、两个 eval harness、`npm test`、`npm run build` 全绿；
   无 `@ts-ignore`、无新依赖、无 Alembic、`uv.lock` 不暂存；
7. 文档只描述实际实现的能力。

## 12. 决策记录（2026-11-12 用户确认，按主对话推荐方案执行，不再保留备选）

1. **弹窗动作集合**：三按钮——取消 / 仅保存不加锁（lock=false）/ 保存并加锁
   （lock=true，主按钮），保留不加锁出口；不做强制加锁。
2. **资料库复用的本期粒度**：最小实现——仅对能匹配 profile_schema 既有低敏字段的
   标记显示默认不勾的复用勾选，报价/工期/开竣工/合同/银行账号等高敏标记不显示勾选；
   本期不做更宽的复用面，其余留待 T-040。
3. **工作台入口**：内嵌 ResultView 卡片区块（展开/折叠），不新增顶层 tab、不引入
   localStorage/IndexedDB，保持单页内存态语义。
