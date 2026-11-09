# 规格：任务终态生命周期修复——结果留存、页面复位与历史页自动刷新（T-030）

## 1. 用户场景与业务目标

T-031 把活跃任务选择态上移 App 后，仍遗留三个终态生命周期缺陷：

1. **结果被 grace 定时器抹掉**：App 全局 poller 在任务终态后启动 8 秒 `TERMINAL_GRACE_MS`
   定时器，到期清空 `activeJobIdRef`、`activeJobs[kind]` 与 `selectedJobIds[kind]`；
   WritePage 的 ResultView 依赖 activeJob 渲染，终态 8 秒后结果消失、页面回退表单，
   用户无法持续查看与下载产物，必须 F5 才能再次提交。
2. **没有显式复位出口**：WritePage 成功后只有 ResultView，无「再写一份」；UploadPage
   成功后只有「去编写标书」，无「再上传一个」。
3. **历史页不自动刷新**：HistoryPage 仅在挂载时 `load()` 一次，无轮询、无终态事件订阅；
   任务终态后标书/任务列表不更新，只能手动点「刷新」。App 的 `onWritten`/`onUploaded`
   只刷新侧栏计数，不刷新历史页。

业务目标：

1. 终态 job 的最后一次快照在 App 内存中持续保留，结果视图不受任何定时器清理，直到用户
   显式复位；
2. 终态即停止该 job 的轮询，不再用「8s 后清空」间接停轮询；
3. 两个页面提供显式复位入口，统一经 `registerJob(kind, null)` 回表单并可再次提交，全程
   无需浏览器刷新；
4. HistoryPage 由 App 终态事件驱动自动重新加载，无活跃任务时零网络请求，禁止空转轮询；
5. 失败分支的返回/重试链路不回归。

## 2. 终态保留与轮询停止

### 2.1 数据保留位置

- 终态 JobView 仍保存在 App 的 `activeJobs[kind]`（内存单槽
  `Record<JobKind, JobView | null>`，单槽语义不变，多任务集合属 T-034）；
- `selectedJobIds[kind]` 在终态后保持指向该 jobId，页面凭 T-031 的
  `derivePageJobState` 继续派生 `job` 视图；
- 不引入 localStorage/IndexedDB 持久化：整页刷新（F5）后内存态重置、回表单，这是既有
  内存态语义，本任务不改变；后端落盘产物仍可在历史页查看。

### 2.2 轮询停止时机

poller 某次 `getJob` 成功返回终态 job（succeeded/failed）时：

1. 终态快照写入 `activeJobs[kind]`（`jobEquals` 判等不变，避免无意义重渲染）；
2. **立即** 将 `activeJobIdRef.current[kind]` 置 null，后续 tick 跳过该槽位，不再发送
   getJob 请求；
3. 不启动任何 grace 定时器，不清空 activeJobs 与 selectedJobIds。

poll 失败（getJob reject）沿用静默跳过：ref 与状态均不变，下一轮继续；只有成功拿到终态
响应才停轮询，网络抖动不会误停。1.5s 全局 interval 本身保留（另一槽位或后续新任务仍需
轮询），停止的是「该 job 的请求」，不是销毁 interval。

### 2.3 grace 机制的去留

- **删除** `TERMINAL_GRACE_MS` 常量与终态 `setTimeout` 清理路径；
- `activeJobs[kind]` / `selectedJobIds[kind]` 的清理只允许由 `registerJob(kind, null)`
  触发，调用场景限定为：
  - 用户点击页面显式复位入口（本任务新增）；
  - UploadPage 取消上传、上传请求异常（既有 catch 路径）；
  - WritePage 失败后「返回修改参数」（既有路径）；
- 定时器不再能改变页面状态，页面不会自动回退表单。

## 3. 页面复位动作

### 3.1 统一复位通道

所有复位一律调用 App 既有 `registerJob(kind, null)`：清 `activeJobIdRef`、
`activeJobs[kind]`、`selectedJobIds[kind]`（T-031 语义），并复位 poller 的迁移观测
ref（见 4.2）。页面表单本地 state（输入项、选项、lastBody）不随复位清空，便于改参数
再提交。

### 3.2 WritePage（kind=bid）

- succeeded 且 bid 存在：ResultView 下方新增「再写一份」按钮，调用
  `onRegisterJob(null)`，回表单后可再次提交；
- failed：保留既有「重试（沿用原参数）」（`startBid(lastBody)`）与「返回修改参数」
  （`onRegisterJob(null)`）两个出口，行为不变；
- 重试提交新 jobId 后，旧终态快照在首帧新 job 返回前仍留在 activeJobs，但
  `derivePageJobState` 按 id 不匹配派生 submitting，不会渲染旧 job（T-031 已锁定，
  本任务不改其语义）。

### 3.3 UploadPage（kind=parse）

- succeeded 且 result 存在：解析结果卡新增「再上传一个」按钮，调用
  `onRegisterJob(null)`，表单区恢复、可重新选择 PDF；「去编写标书」既有出口保留；
- failed：grace 删除后失败任务不再自动消失，**必须**提供显式出口，任务卡失败分支新增
  「重新上传」按钮调用 `onRegisterJob(null)`——这是 grace 移除的必要配套，否则失败后
  页面将无任何出口；
- 修复成功回调闩锁缺陷：现有 `fireRef` 布尔闩锁在「成功 → 复位 → 再次成功」周期内不会
  第二次触发 onUploaded；改为按 jobId 记录（firedJobRef），由纯函数
  `shouldEmitJobSuccess` 判定，每个 jobId 最多触发一次，复位后新 job 可再次触发。

## 4. HistoryPage 自动刷新（事件驱动，非轮询）

### 4.1 方案选择

采用 App 终态事件驱动，不引入 HistoryPage 轮询：

- App 新增 `historyRefreshNonce: number`（初值 0）；
- poller 仅在观测到「非终态 → 终态」迁移时把 nonce +1（纯函数
  `nextHistoryRefreshNonce`），parse/bid 两个槽位共用一个 nonce；
- nonce 作为 `refreshNonce` prop 传给 HistoryPage；HistoryPage 用 ref 记录上一次看到的
  nonce，effect 检测到变化才调用既有 `load()`（listBids + listTasks 并发）；挂载首帧
  前后相等，不产生重复加载；
- 无 interval、无 setTimeout；无活跃任务时零网络请求，满足「无任务不空转」；
- HistoryPage 未挂载期间错过的 nonce 无需补偿：挂载时既有首载 `load()` 即拿到最新
  列表。

### 4.2 迁移判定的纯函数化

poller 用 `pollPrevRef: Record<JobKind, JobView | null>` 保存每槽位上一次观测值，与本次
响应一起交给纯函数 `decidePollOutcome(prev, next)`，返回：

- `stopPolling`：next 是否终态（终态即停）；
- `terminalTransition`：next 终态且 prev 非终态（含 prev 为 null 的首帧即终态，例如
  任务在两次轮询间隔内快速完成）。

`registerJob` 时把该槽位的 pollPrevRef 复位为 null，保证新任务的终态迁移一定能被观测
到。禁止在 setState updater 内做 nonce 自增等副作用（StrictMode 双调用会重复计数）。

### 4.3 降级行为

- DB 不可用时后端 listTasks 返回空数组的既有降级不变，自动刷新走同一个 `load()`，不
  引入新错误路径；
- listBids/listTasks reject 时沿用 `load()` 既有 error Banner 行为；非终态迁移不会触发
  刷新，不放大故障请求量。

## 5. 纯函数（src/lib/task_state.ts）

新增（不修改 T-031 已锁定的 `derivePageJobState` / `planJobJump`）：

1. `decidePollOutcome(prev, next): { stopPolling: boolean; terminalTransition: boolean }`；
2. `nextHistoryRefreshNonce(current, terminalTransition): number`：迁移才 +1，否则原样
   返回；
3. `shouldEmitJobSuccess(activeJob, selectedJobId, firedJobId): boolean`：id 匹配 +
   succeeded + 未对该 jobId 触发过。

删除：`TERMINAL_GRACE_MS`（grace 机制取消，仓库内无其他调用方保留）。

## 6. 输入、输出与副作用

- 无 HTTP API、请求/响应 Schema、任务协议、数据库或配置变化；纯前端行为修复；
- 网络副作用变化：终态 job 的 getJob 在终态观测后立即停止（旧实现终态后仍每 1.5s 请求
  直到 8s grace 清空，存在冗余请求）；HistoryPage 在每次终态迁移且页面挂载时新增一次
  listBids + listTasks 并发；
- 不新增依赖、不改动后端任何文件、不引入 pnpm/yarn 锁文件。

## 7. 正常、异常与边界路径

1. 编写成功：终态快照保留 → ResultView 持续可查看（推进任意时长不消失）→ 点「再写一份」
   回表单 → 可再次提交，无需刷新；
2. 解析成功：解析结果卡持续保留 →「再上传一个」回表单可重新选文件；「去编写标书」不受
   影响；
3. 编写失败：失败卡与「重试（沿用原参数）」「返回修改参数」保持可用；重试注册新 jobId
   后首帧前显示提交中、不串台；
4. 解析失败：任务卡显示错误与「重新上传」，复位后可再次上传；
5. poll 失败：运行中 getJob reject 静默跳过、下一轮重试；终态观测前的网络抖动不会停止
   轮询、不会丢结果；
6. 终态后停轮询：终态观测后再推进多个轮询周期，getJob 调用次数不再增长（组件测试断言
   mock 调用次数）；
7. 历史页自动刷新：任务在任意 tab 进入终态 → 已挂载的 HistoryPage 自动 reload，
   listBids/listTasks 调用次数增加；无任务时推进时间，调用次数不变（无空转）；
8. 整页刷新：内存态全部重置回表单（既有语义），持久产物经历史页首载可见；
9. 重复终态观测防护：同一终态 job 被再次观测（prev 已终态）不产生第二个
   terminalTransition，nonce 不重复自增；
10. 切 tab：终态保留后切走再切回，T-031 的受控恢复继续生效，结果视图仍在。

## 8. 测试策略（全部离线，禁止真实网络）

### 8.1 纯函数（tests/task_state.test.ts）

- `decidePollOutcome`：running 观测不停止不迁移；running→succeeded/failed 停止且迁移；
  首帧 null→终态视为迁移；终态→终态不重复迁移；
- `nextHistoryRefreshNonce`：迁移 +1、非迁移不变（连续多次不变）；
- `shouldEmitJobSuccess`：匹配成功未触发→true；同 jobId 已触发→false；id 不匹配→
  false；运行中→false；空 activeJob/选择态→false；旧 jobId 已触发、新 jobId 成功→
  true（复位后重新武装）；
- 删除 `TERMINAL_GRACE_MS` 旧用例。

### 8.2 组件（新增 tests/app_terminal_lifecycle.test.tsx；改造 app_active_job.test.tsx 1 例）

沿用 T-031/T-035 约定：`vi.mock('../src/api')` 全离线、`vi.useFakeTimers` +
`advanceTimersByTimeAsync`、fireEvent 派发事件（假定时器下禁用 user-event）、每例有断言、
禁 `@ts-ignore`。

1. bid succeeded 后推进超过 8 秒，ResultView 仍在、不回表单（旧实现会消失）；且终态后
   getJob 调用次数不再增长（轮询停止）；
2. 点「再写一份」回表单，可再次走提交弹窗触发 createBid（第二次调用），新任务视图出现；
3. parse succeeded 后「再上传一个」恢复文件选择区，可再次选择文件并提交（uploadParse
   第二次调用）；
4. HistoryPage 挂载后任务在后台进入终态，listBids/listTasks 调用次数自动增加；无活跃
   任务时继续推进时间，调用次数不变（无空转）；
5. bid failed 分支：「返回修改参数」回表单、「重试（沿用原参数）」以原 body 再次
   createBid 并恢复任务视图；
6. 改造 T-031 旧 grace 用例：parse succeeded 后推进 8.1s，任务进度/结果卡仍在、文件
   选择区不出现。

### 8.3 手动验证（补充，记入工作日志，未执行不标已验证）

启动前后端人工跑一次编写与一次解析，验证：结果长时间留存、两个复位入口、历史页自动
出现新产物、失败分支返回/重试；步骤写入工作日志。

## 9. 验收标准

1. `npm test` 全绿，用例数在 T-031 后 88 例基线上只增不减；
2. `npm run build`（`tsc --noEmit && vite build`）通过，无 `@ts-ignore`；
3. 本规格覆盖终态保留、复位、历史页刷新、轮询停止、异常路径；
4. README 前端说明同步更新（终态留存/复位/历史页自动刷新，内存态语义说明更正）；
5. 手动验证步骤入工作日志，未实际执行不得标注「已验证」。

## 10. 明确范围外（越界即返工）

- 单槽改多任务集合、排队语义、supplement/rewrite 接入全局任务栏（T-034）；
- 后端进度回调补点（T-032）、`jobEquals` 判等修复（T-033）；
- ResultView 私有 supplement/rewrite 轮询改造；
- 任何 `src/`、`api/` 后端改动、依赖增删、uv/pytest 运行；
- 跨刷新（localStorage 等）的任务态持久化。