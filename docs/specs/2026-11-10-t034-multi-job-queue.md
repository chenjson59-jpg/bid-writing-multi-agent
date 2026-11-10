# 规格：多任务排队与全局任务栏改造（含补写/重写接入）（T-034）

## 1. 用户场景与业务目标

T-030/T-031 完成后，App 层任务状态仍是按 `JobKind('parse'|'bid')` 划分的两个单槽：
`registerJob(kind, jobId)` 同类直接覆盖，第二个同类任务会顶掉第一个任务的轮询；
`GlobalTaskBar` 只从两个槽位取运行中任务；`ResultView` 的补写（supplement）与
章节重写（rewrite）各自持有私有 `setInterval` 轮询，完全不进全局任务栏，组件卸载后
轮询清理与回调安全只能靠组件内部兜底。

用户已明确产品决策：**允许排队多任务，不做提交按钮禁用**，任务进行中仍可填表再次
提交，全程无需浏览器刷新。业务目标：

1. 任务状态从「按 kind 单槽」改为「以 job_id 为键的多任务集合」，同类任务连续提交
   互不覆盖、互不丢轮询，新任务自动选中并置顶；
2. 全局 poller 统一轮询全部未终态任务，终态即停且快照留存（沿用 T-030，不恢复
   grace 定时器），终态任务经显式关闭才从集合移除，防止内存无限增长；
3. supplement/rewrite 纳入同一套全局任务状态与底部任务栏，移除 `ResultView` 的两个
   私有 `setInterval`，轮询职责上移 App；补写完成的列表刷新、重写完成的章节/摘要
   刷新通过终态回调注册机制保活，且组件卸载后不操作已卸载组件、无残留定时器；
4. 保持 T-030 内存态语义：整页 F5 仍重置，不引入 localStorage/IndexedDB；
5. 后端零改动、HTTP/Schema/JobView 结构不变、不新增依赖。

## 2. 后端排队容量事实复核（只读核实，2026-11-10）

- `api/dispatch.py::LocalBackend.available()` 恒返回 `True`，前端任何提交都不会被
  前端侧拒绝；本任务同样不设提交上限、不做禁用。
- `LocalBackend` 用 `ThreadPoolExecutor(max_workers=settings.api_max_concurrent_parses)`
  （默认 1）执行任务，线程池自带**无界等待队列**：默认单 worker 下多任务按提交顺序
  串行执行，先提交的任务先进入 running，其余保持 pending。
- Celery 后端走 broker 队列（`CeleryBackend.submit` → `apply_async`），并发度取决于
  worker 配置；前端对两种后端保持同一套 pending/running 展示，不做后端特判。
- supplement/rewrite 与 parse/bid 走同一个 `submit(job_id, kind, payload, meta)`
  入口，`source_name` 为 `bid_id`；终态 JobView 的 `bid` 字段：supplement 为**新产物**
  摘要（`supplement_of` 指向原产物），rewrite 为**原地更新后**的同产物摘要
  （`dispatch._execute` 对两类均返回 `{"progress", "bid"}`）。
- 容量取舍：local 无界队列在极端连续提交下存在任务堆积的产品风险，但当前会话级
  内存态 + F5 重置 + 任务栏显式关闭已构成边界；前端不新增拒绝策略（改拒绝属后端
  职责，本任务范围外），通过任务栏的「排队中」状态与计数让堆积可见。

## 3. 设计决策（逐条结论与理由）

### 3.1 决策一：数据结构——有序数组承载四类任务

结论：App 任务集合采用**按登记时间倒序（最新在前）的 `TrackedJob[]` 数组**，
不使用 `Map` 或 `Record<job_id, ...>` 作为 React state。

```ts
// task_state.ts
export type JobKind = 'parse' | 'bid' | 'supplement' | 'rewrite'
export type PageChannel = 'upload' | 'write'

export interface TrackedJob {
  jobId: string
  kind: JobKind
  /** 最近一次 getJob 快照；登记后首帧 poll 前为 null（对应 T-031 submitting 态） */
  job: JobView | null
  /** 终态已观测：停止轮询但快照留存，等待用户显式关闭 */
  settled: boolean
}
export type JobCollection = TrackedJob[]
```

理由：

1. 「新任务置顶」是硬需求，数组顺序直接表达展示顺序，任务栏与选择器无需额外排序；
   Map/Record 键控查找虽为 O(1)，但顺序必须再维护一份索引结构，React 不可变更新时
   Map 也要整体替换，两层结构增加边角。
2. 并发任务数量实际为个位数，按 id 查找的 O(n) 成本在 1.5s 轮询频率下可忽略；
   纯函数对数组做增删改查最易测试，且可在快照无变化时返回原数组引用，配合
   `jobEquals` 保持既有「相同状态不重渲染」能力。
3. `JobKind` 从两值窄联合扩展为四值。`JobView.kind` 在类型层仍是 `string`，但集合
   条目的 `kind` 只可能由四个内部登记入口（UploadPage/WritePage/ResultView×2）以
   字面量写入，不从快照反推，因此不需要 `normalize` 兜底；展示层 `kindLabel` 仍接受
   `string` 以兼容历史数据。
4. 页面通道只有两个：parse 归属 upload 通道，bid/supplement/rewrite 归属 write 通道
   （`jobChannel`）。选择态、聚焦信号按通道而不是按 kind 存储，三个 write 系 kind
   自然共享同一页面选择。

### 3.2 决策二：registerJob 语义——追加置顶 + 显式关闭拆分

旧 `registerJob(kind, jobId)` 的「同类覆盖」与 `registerJob(kind, null)` 的「槽位
复位」在多任务下语义不再成立，拆为三个职责单一的操作：

```ts
export interface RegisterJobInput {
  kind: JobKind
  jobId: string
  onSettled?: SettledCallback
  // 实现期补记，见下方 autoSelect 说明
  autoSelect?: boolean
}
// App 暴露
registerJob(input: RegisterJobInput): void
closeJob(jobId: string): void
closeSelectedOnChannel(channel: PageChannel): void
deselectChannel(channel: PageChannel): void
```

- `registerJob`：纯函数 `registerTrackedJob(jobs, entry)` 把新条目**前插**到数组
  （同 jobId 已存在时防御性替换并置顶，正常流程不会触发），同时
  `selectionAfterRegister` 把该通道选择态指向新 jobId——首帧 poll 未返回时选择态已
  就位，页面凭 `derivePageJobState` 显示「提交中」（T-031 语义不变）。
- `closeJob(jobId)`：显式关闭**指定**任务——从集合移除条目、删除 `pollPrevRef` 与
  回调注册表中的同键数据；若该任务正是其通道的当前选中项，
  `selectionAfterClose` 把选择态置 null，页面回表单；否则选择态不变。
- `closeSelectedOnChannel(channel)`：供页面复位按钮使用（「再写一份」「再上传一个」
  「重新上传」「返回修改参数」），关闭该通道当前选中任务并回表单；**不自动选中**
  集合内其他任务（用户显式切换才切换，避免页面内容跳变）。
- 页面 props 调整：`onRegisterJob(jobId: string)` 不再接受 null（提交成功路径才调
  用）；新增 `onCloseJob(): void` 承载原 null 复位路径。上传失败/取消时集合中本就
  没有条目（登记发生在 `await` 成功之后），`onCloseJob` 为空操作，保持安全。
- 失败任务的「重试（沿用原参数）」登记新 jobId 并自动选中；旧失败条目不自动消失，
  留在任务栏由用户显式关闭（多任务语义下任何任务都不被隐式移除）。
- 【实现期补记】`autoSelect`（默认 true）：parse/bid 顶层提交保持自动选中；
  ResultView 发起的 supplement/rewrite 必须传 `autoSelect: false`。根因：这类子任务
  与当前 write 通道选中的 bid 同通道，若登记即自动选中，选择态会从当前 bid 切走，
  注册 `onSettled` 回调的 ResultView 随即卸载，其 unmount cleanup 会立刻 revoke 刚
  登记的回调，终态刷新永远无法触发。子任务不抢占选择态，发起者保持挂载，回调链路
  才完整（组件用例「补写/重写终态只刷新一次」固化该约束）。

### 3.3 决策三：轮询生命周期——多 job 并发、终态即停、显式清理

- 保留 App 层**唯一**的 1.5s `setInterval`（T-030 已验证的全局 interval），每轮通过
  纯函数 `pollableJobs(jobs)` 取全部 `settled === false` 的条目，逐个 `await getJob`，
  顺序执行、互不影响；一个 job 的请求异常被局部 catch，不阻断同轮其他 job。
- 每个 job 的迁移观测独立存储：`pollPrevRef` 从 `Record<JobKind, JobView|null>`
  改为 `Map<string, JobView|null>`，登记时为该 jobId 写入 null，保证首帧即终态仍被
  识别为一次终态迁移（T-030 锁定语义）。
- 单 job 决策仍调用既有纯函数 `decidePollOutcome(prev, next)`，语义零变化：
  - `stopPolling`：纯函数 `markJobSettled` 置 `settled=true`，后续 tick 不再请求该
    job；终态快照保留在条目内，不恢复任何 grace 定时器；
  - `terminalTransition`：`historyRefreshNonce` 自增（任意 job 的迁移都触发
    HistoryPage 一次 listBids/listTasks 并发刷新，无任务时零请求的特性不变），并在
    该迁移点触发一次性终态回调（决策六）。
- poll 失败（reject）沿用静默跳过：prev 与 settled 均不变，下一轮继续；网络抖动不
  会误停轮询。
- 显式关闭是条目离开集合的**唯一**途径：任务栏终态行「关闭」按钮（决策五）与页面
  复位按钮均走 `closeJob`，同步清理集合、prev Map、回调 Map 与选择态，无残留定时器
  （interval 本身始终存活，停的是具体 job 的请求）。
- 副作用边界：终态回调在 `setState` updater **之外**调用，规避 StrictMode 下
  updater 双调用导致的重复副作用；回调抛错被局部 catch 并 `console.error`，不影响
  同轮其他任务与后续轮询。

### 3.4 决策四：页面任务选择——通道单选 + 任务栏切换，不做页内切换器

- 选择态 `ChannelSelection = Record<PageChannel, string|null>`：同一页面通道同一时刻
  只展示一个任务（当前选中项），与 T-031 受控派生完全兼容——App 仍向页面传
  `selectedJobId` 与 `activeJob`（= `selectedJobSnapshot(jobs, 选中id)` 的快照或
  null），`derivePageJobState` 三态、防串台 submitting 态原样工作。
- 切换入口统一为底部任务栏「查看」：`jumpToJob(jobId)` 按条目 kind 算通道，切到
  对应 tab、把该通道选择态指向该 jobId、自增该通道 `focusNonces`，页面既有滚动/高亮
  effect 无需改造即生效（同 tab 也有反馈）。
- **不新增页面内任务切换器**：任务栏在任何 tab 都常驻可见，已能完成任意任务定位；
  页内切换器会重复一套选择 UI 并扩大本任务改造面。集合内其他任务在任务栏持续可见，
  不丢失可达性。
- 新提交任务经 `registerJob` 自动选中，用户停在原页面即可看到最新任务进度。
- 【实现期补记】运行中回表单的显式入口 `deselectChannel(channel)`（页面 prop
  `onDeselect`，UI 为任务卡上的 ghost 按钮「返回表单（后台继续）」）：只把该通道
  选择态置 null，**不关任务、不停轮询、不动集合**。这是「任务进行中仍可填表再次
  提交」的必要配套——通道单选下页面被选中任务占据时没有表单可填，用户必须能先
  脱选回表单；脱选后任务仍在任务栏可达，「查看」可再次定位。它与 `onCloseJob`
  （级联关闭、移除集合）职责严格区分，不属于范围外的「页内任务切换器」：不提供
  任务列表，只做选择态脱选。

### 3.5 决策五：pending/running 区分、置顶规则与任务栏多任务布局

- 置顶：集合数组新条目恒在前插，任务栏按数组顺序渲染，后提交任务天然在最上方。
- 状态区分（沿用 `STATUS_LABEL` 既有文案与配色，不新增文案体系）：
  - 首帧快照为 null：「提交中」（amber）；
  - `pending`：「排队中」（amber），对应后端无界队列里等待 worker 的任务；
  - `running`：「运行中」（sky），running 且有 progress 时末行展示最新进度；
  - `succeeded`：「已完成」（emerald）；`failed`：「失败」（rose）。
- 任务栏从「只渲染运行中槽位」改为渲染**全部集合条目**（含终态留存行），头部展示
  总任务数与运行中/排队计数；每行：kind 标签、来源名（`source_name`，缺省回退
  job_id 前 8 位）、状态、运行中末条进度、「查看」按钮。
- 「关闭」按钮只出现在 `settled` 行：终态任务由用户显式关闭清理；pending/running
  任务不提供关闭入口（关闭跟踪会产生孤儿轮询且无法取消后端执行；后端取消仍在
  「任务与产物」页提供），杜绝活动任务被误移出集合。
- 底部 padding 条件从「有运行中任务」改为「集合非空」，终态留存行同样不遮挡页面。

### 3.6 决策六：终态回调注册机制——一次触发、卸载可撤销、无卸载后 setState

```ts
export type SettledCallback = (job: JobView) => void
// 对显式传入的 Map 做操作，便于纯函数离线测试；App 用 ref 持有该 Map
putSettledCallback(reg, jobId, cb): void      // 登记（同 id 覆盖以最新为准）
takeSettledCallback(reg, jobId): cb | undefined  // 取出即删
revokeSettledCallback(reg, jobId): void       // 卸载撤销，不影响任务本身
```

- 注册：`registerJob({kind, jobId, onSettled})` 时把回调写入 `settledCallbacksRef`
  的 Map；回调注册表与任务集合分离，闭包不进入 React state，纯函数集合操作不受
  函数引用影响。
- 触发：poller 在 `terminalTransition` 迁移点 `takeSettledCallback`（取出即删）后
  调用。一次性由双保险保证：迁移按 job_id 只发生一次（`decidePollOutcome` 对终态→
  终态不报迁移），且 Map 条目取后即删；即使终态被重放也不会二次调用。
- 卸载生命周期：`ResultView` 用 ref 记录本实例登记过的 jobId，卸载 cleanup 中逐个
  `revoke`。任务本身仍在全局集合中继续轮询、任务栏可见；撤销的只是「回调到这个已
  卸载组件实例」的链路。组件重挂后通过既有 mount 拉取（`listSections`）与终态后的
  HistoryPage 自动刷新获得最新数据，不依赖旧回调补账。
- 禁止卸载后 setState：终态文案（补写/重写完成或失败提示）**不由回调写入 state**，
  改为渲染期根据全局快照派生（状态机 + 快照字段）；回调只负责数据刷新，且这些回调
  在卸载时已被 revoke，不存在对已卸载组件 setState 的路径。
- 【实现期补记】两类子任务的实际接线（避免同一刷新被两条链路触发两次）：
  - rewrite：`onSettled` 调 `refreshSummary()`（`getBid` 重拉摘要 + `listSections`
    重拉章节 + `onChanged`），WritePage 与 HistoryPage 展开区共用同一 ResultView
    实现，组件用例断言终态恰好各刷新一次、重放不重复；
  - supplement：WritePage 传 `onSupplemented={onWritten}`，终态经回调刷新侧栏
    标书计数；HistoryPage **不再传** `onSupplemented`——该页已由 T-030 的全局
    `historyRefreshNonce` 在任意任务终态迁移时统一 `load()`（listBids/listTasks），
    再传本地回调会导致同一次终态双重请求。即「标书列表刷新」行为不丢，只是收敛为
    nonce 单路径，组件用例分别断言两条路径各自恰好一次。
- 适用范围：parse/bid 页面既有成功副作用（UploadPage 的 `shouldEmitJobSuccess`、
  WritePage 的 succeeded effect）保持原机制不动，本机制只承接 supplement/rewrite。

### 3.7 决策七：「查看」跳转归属——supplement/rewrite 均归 write 通道

- `kindTab` 既有映射保持：parse → upload，其余一律 write；新增同义窄化函数
  `jobChannel(kind): PageChannel` 供选择态使用，`kindTab` 内部委托它，输出不变。
- 归属论证（结合产物实际位置）：
  - supplement 终态生成的是**新标书产物**（新 bid_id、新 output_dir），JobView.bid
    携带新产物摘要，write 页选中该任务后可直接渲染 `ResultView`，与普通 bid 任务同
    一分支；其成功提示引导用户去「标书历史」对比覆盖率，HistoryPage 也会由终态事件
    自动刷新出新产物；
  - rewrite 是对**同一 bid 产物原地更新**，JobView.bid 为更新后摘要，write 页
    ResultView 同样可渲染，且重写回调刷新的章节与摘要本就属于该页面；
  - 两类任务都从 write 页（或 HistoryPage 展开的 write 系 ResultView）发起，归 write
    通道与用户心智、产物位置一致；history 页只做列表与展开查看，不承载运行态任务
    视图，不作为跳转目标。
- `kindLabel` 细化为四值区分：parse→解析、bid→编写、supplement→补写、rewrite→重写，
  未知字符串兜底返回原值或「任务」。旧实现把三类写作任务都标「编写」，本任务需要在
  任务栏区分补写/重写，因此同步迁移 `task_state.test.ts` 中两条既有断言
  （supplement/rewrite 的期望值由「编写」改为「补写」/「重写」），断言强度不弱化；
  `kindTab` 的既有断言全部保持。

## 4. 输入、输出与副作用

- 无 HTTP API、请求/响应 Schema、JobView 结构、数据库或配置变化；无新增/删除依赖。
- 网络副作用变化：
  - 全局 poller 从每轮固定查 2 个槽位变为遍历全部未终态条目（N 个活动任务每轮 N 次
    getJob）；终态即停，语义与 T-030 一致；
  - 删除 `ResultView` 两个 2s 私有 interval，补写/重写统一为 1.5s 全局轮询；
  - HistoryPage 终态自动刷新机制不变，触发源从单槽迁移改为任意条目迁移。
- 状态全部为 App 内存态：F5 重置集合、选择态、聚焦信号、回调注册表与 prev Map；
  不引入任何持久化。
- 页面表单本地 state（输入项、lastBody 等）不随任务关闭清空，便于改参数再提交。

## 5. 正常、异常与边界路径

1. 连续提交两个同类任务：两条目并存、互不覆盖，任务栏两行，poller 每轮请求两个
   jobId；后提交者置顶且自动选中，「查看」可切回前一个任务。
2. 跨 kind 并行：parse 在 upload 通道、bid/supplement/rewrite 在 write 通道，选择态
   互不干扰，两个 nav 蓝点按通道独立派生。
3. pending→running：后端单 worker 串行时先提交者 running、后提交者 pending，任务栏
   文案与颜色明确区分。
4. 终态即停：某条目终态后 getJob 调用次数冻结，其他条目轮询不受影响；终态行留存并
   出现「关闭」，点击后从集合移除；若关闭的是选中项，页面回表单。
5. 补写终态：一次性终态迁移、回调取后即删，重放终态不二次触发。编写页挂载时经
   `onSettled → onSupplemented → onWritten` 刷新侧栏计数；历史页由全局 nonce 统一
   reload（单路径，不双刷），新产物在 HistoryPage 自动出现。
6. 重写终态：一次性触发 `getBid`+`listSections` 刷新与 `onChanged`；失败终态展示
   `job.error`，不触发刷新回调。
7. poll 失败：静默跳过、下一轮重试；与旧 ResultView 私有轮询「一次 getJob 失败即
   标失败」相比，瞬时网络抖动不再误报失败（行为改进，组件测试锁定新语义）。
8. 组件卸载：切 tab 卸载 ResultView → 撤销回调、无私有 interval 残留；任务继续全局
   轮询，重挂后 mount 拉取恢复数据，终态文案由快照重新派生。
9. F5：全部内存态重置回无任务状态；后端落盘产物经 HistoryPage 首载可见。
10. 上传取消/上传请求异常：登记尚未发生，`onCloseJob` 空操作，集合保持不变。
11. 失败任务重试：新 jobId 登记置顶并选中，旧失败条目留存待显式关闭。
12. 同 id 重复登记（防御）：替换条目并置顶，不产生重复行。
13. 运行中脱选（「返回表单（后台继续）」）：仅选择态置 null，集合/轮询/任务栏不
    变，回表单可再次提交；脱选后经任务栏「查看」可重新选中同一任务。
14. supplement/rewrite 以 `autoSelect:false` 登记：不切换当前通道选择态，发起者
    ResultView 保持挂载，避免卸载 cleanup 撤销自身终态回调。

## 6. 纯函数清单（task_state.ts，无 React 依赖）

新增（命名以实现为准，语义不变）：类型 `JobKind`（四值）、`PageChannel`、
`TrackedJob`、`JobCollection`、`SettledCallback`、`ChannelSelection`、`JobBridge`、
`RegisterJobInput`（含实现期补入的 `autoSelect`）、`PageJobState` 沿用既有定义；
集合操作 `registerTrackedJob`、`upsertJobSnapshot`（jobEquals 相等返回原引用）、
`markJobSettled`、`closeJobById`、`findTrackedJob`、`pollableJobs`、
`selectedJobSnapshot`；选择态 `selectionAfterRegister`、`selectionAfterClose`；
通道 `jobChannel`；回调注册表 `putSettledCallback`、`takeSettledCallback`、
`revokeSettledCallback`。

保持既有语义不动：`isRunning`、`isTerminal`、`terminalOutcome`、`statusLabel`、
`derivePageJobState`、`planJobJump`、`decidePollOutcome`、`nextHistoryRefreshNonce`、
`shouldEmitJobSuccess`、`filterRunning`、`hasRunning`、`jobEquals`、
`pickJobForKind`（类型随 JobKind 扩展自然兼容）。仅 `kindLabel` 按决策七调整输出并
迁移对应断言；`kindTab` 输出不变。

## 7. 测试策略（全部离线，禁止真实网络/真实定时器）

### 7.1 纯函数（tests/task_state.test.ts 新增 describe，只增不删）

- 集合：追加不覆盖且置顶、同 id 防御替换、快照更新与相等引用 bail-out、终态标记、
  按 id 关闭、查找、pollable 过滤、选中快照派生；
- 选择态：登记后通道选中、关闭选中项置 null、关闭非选中项不变；
- 通道：四 kind 的 channel/kindTab 映射、未知 kind 兜底；
- 回调注册表：put/take 取到同一引用、二次 take 为 undefined（重放不重复）、revoke 后
  take 为 undefined（卸载清理）、同 id 覆盖以最新为准；
- kindLabel 四 kind 新映射（迁移 2 条旧断言，保留未知兜底用例）。

### 7.2 组件（新增 tests/app_multi_job_queue.test.tsx）

沿用 T-030/T-031 约定：`vi.mock('../src/api')` 全离线、`vi.useFakeTimers` +
`advanceTimersByTimeAsync`、fireEvent 派发事件（禁 user-event）、每例有断言、禁
`@ts-ignore`。至少覆盖：

1. 连续提交两个同类编写任务：任务栏两条、两个 jobId 都被轮询（mock 调用断言）、
   「查看」可互相切换且页面显示对应任务；
2. 后提交任务自动选中置顶；pending 显示「排队中」、running 显示「运行中」；
3. 任务终态后该 jobId 的 getJob 调用次数冻结、快照留存（结果视图仍在）、任务栏
   「关闭」后条目移除且集合行消失；
4. supplement 提交后出现在任务栏（标签「补写」，运行中无「关闭」），停留编写页
   终态时 `onSettled → onSupplemented → onWritten` 刷新侧栏计数恰好一次、重放不
   重复，进入历史页后无新迁移则不产生额外请求；rewrite 同理（标签「重写」，
   `getBid`/`listSections` 刷新恰好一次），并 spy 断言全程无 2s 私有 interval；
5. ResultView 卸载（切 tab）后任务终态：回调不触碰已卸载组件、全程不存在 2s
   interval（spy 断言）、重挂后无报错；
6. 回归：切 tab 任务视图恢复、「查看」高亮、HistoryPage 终态自动刷新在多任务结构下
   仍然通过；F5 语义以纯内存结构说明（jsdom 不模拟刷新，不做组件用例）。

### 7.3 手动验证（写入工作日志，未实际执行不得标「已验证」）

启动前后端：① 连续两个编写任务 + 一个解析任务并行，观察任务栏置顶/排队/切换；
② 补写与重写终态回调（列表刷新、章节刷新）；③ 终态行关闭清理与页面复位；
④ F5 内存重置；⑤ 切 tab 卸载 ResultView 后任务继续轮询、重挂数据正确。

## 8. 验收标准

1. `npm test` 全绿，用例数在 114 基线上只增不减（kindLabel 两条为断言值迁移，非
   删例），新增用例清单随提交说明列出；
2. `npm run build`（`tsc --noEmit && vite build`）通过，无 `@ts-ignore`；
3. 本规格覆盖第 3 节七项设计决策、第 5 节边界路径与第 9 节范围外/兼容语义；
4. README 桌面前端章节同步多任务排队、任务栏关闭、补写/重写接入与 F5 语义；
5. 手动验证步骤入工作日志，未执行不标「已验证」。

## 9. 明确范围外（越界即返工）

- 任何 `src/`、`api/` 后端改动、依赖增删、uv/pytest 运行、`frontend/` 与本规格/本地
  台账之外的文件改动；工作区既有的 `uv.lock` 本地改动不暂存、不提交、不还原；
- 后端 supplement/rewrite 细粒度进度回调（T-032 已登记的后端遗留）、后端排队拒绝
  策略与容量限制改造；
- 跨刷新持久化（localStorage/IndexedDB）、grace 定时器复活、页面内任务切换器；
- 运行中任务的任务栏关闭/取消跟踪（后端取消维持 HistoryPage 既有入口）；
- 弱化或删除 T-030/T-031/T-033 既有用例断言（kindLabel 期望值迁移除外，须随本规格
  决策七同步说明）。