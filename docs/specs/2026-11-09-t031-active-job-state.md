# 规格：活跃任务状态上移 App 层与底部任务栏「查看」修复（T-031）

## 1. 用户场景与业务目标

当前活跃任务的数据虽然已保存在 App 层（`activeJobs` 单槽 Record 与全局 poller），
但「页面当前是否展示任务视图」这一选择态仍由页面自己持有：

- `WritePage` 用本地 `useState jobId` 决定 `if (jobId)` 渲染任务视图还是表单；
- `UploadPage` 用本地 `useState localJobId` 做同样的视图切换。

App 按 tab 条件渲染页面，切走即卸载，本地 state 随之销毁。用户提交任务后切到其他
tab 再切回，App 仍持有 `activeJob` 并在后台轮询，页面却回到表单态，任务进度视图
无法恢复。底部 `GlobalTaskBar` 的「查看」按钮只调用 `setTab(kindTab(kind))`：
跨 tab 时落到的是普通表单（用户感知为「跳转无效」），已在目标 tab 时点击更是没有
任何可见反馈。

业务目标：

1. App 成为活跃任务**及其选择态**的单一数据源，页面任务视图由 App props 受控派生，
   不再依赖页面本地 state，tab 切换卸载/重挂不丢失任务上下文；
2. 「查看」按钮在跨 tab 时切换到目标 tab 并展示对应任务视图，在已处于目标 tab 时
   给出明确反馈（滚动定位到任务卡片并短暂高亮）；
3. 终态 8 秒 grace 清理后，受控选择态指向的 job 消失时页面安全回退到表单态，不白屏、
   不报错。终态结果持久留存属于 T-030，本任务不实现。

## 2. 状态归属与受控派生

### 2.1 App 层持有的状态

- `activeJobs: Record<JobKind, JobView | null>`（既有）：poller 拉回的任务数据；
- `activeJobIdRef: Record<JobKind, string | null>`（既有）：poller 跟踪的 jobId；
- `selectedJobIds: Record<JobKind, string | null>`（**新增**）：每种 kind 当前在页面
  上选中的 jobId，即原 WritePage `jobId` / UploadPage `localJobId` 的上移形态；
- `focusNonces: Record<JobKind, number>`（**新增**）：「查看」聚焦信号，每次点击
  自增，页面通过 effect 响应滚动/高亮；不携带业务数据，仅用于触发反馈。

单槽结构 `Record<JobKind, ...>` 保持不变，不引入任务集合、排队语义
（多任务集合属 T-034）。

### 2.2 registerJob 语义（调整）

`registerJob(kind, jobId)`：

- `jobId` 非空：写入 `activeJobIdRef` 并把 `selectedJobIds[kind]` 置为该 id
  （首帧 poll 尚未返回时选择态已就位，页面可展示「提交中」）；
- `jobId` 为 null（取消上传、编写失败后「返回修改参数」、注册期异常）：清
  `activeJobIdRef`、`activeJobs[kind]` 与 `selectedJobIds[kind]`，页面回表单态。

### 2.3 纯函数派生（`src/lib/task_state.ts`，无 React 依赖）

`derivePageJobState(selectedJobId, activeJob)` 返回三态判别联合：

- `{ mode: 'form' }`：`selectedJobId` 为 null，展示表单；
- `{ mode: 'submitting', jobId }`：选择态已置位但 `activeJob` 为空或 job_id 不匹配
  （首帧未返回或旧 job 残留），展示任务容器与加载指示，**不得渲染 id 不匹配的旧
  job**；
- `{ mode: 'job', job }`：`activeJob.job_id === selectedJobId`，展示该任务的进度/
  结果。

`planJobJump(kind, currentTab)` 返回 `{ tab, changedTab }`：`tab = kindTab(kind)`，
`changedTab` 表示是否需要切换 tab。App 据此区分「跨 tab 跳转」与「原地聚焦」。

### 2.4 页面受控关系

- `WritePage` / `UploadPage` 删除本地 `jobId` / `localJobId` state，新增受控 props
  `selectedJobId: string | null` 与 `focusNonce: number`；任务视图分支改由
  `derivePageJobState` 驱动；
- 页面不再有任何决定「展示哪个任务」的本地 state；表单输入类本地 state 不变；
- `WritePage` 的「暂无已抽取的文档」提前返回必须让位于任务视图分支（重挂时
  documents 异步加载期间不得用 EmptyState 覆盖任务视图）。

## 3. 输入、输出与副作用

- 无 HTTP API、请求/响应 Schema、任务协议、数据库或配置变化；纯前端重构与缺陷修复；
- 副作用仍集中在 App：1.5s 轮询 `getJob`、终态 `TERMINAL_GRACE_MS`（8s）grace 清理、
  5s 健康检查，间隔与退避语义不变；
- grace 清理触发时同时清空 `selectedJobIds[kind]`，使页面受控回退到表单态；
- 「查看」点击的副作用为 `setTab`（跨 tab 时）与 `focusNonces[kind] += 1`；
  页面 effect 调用任务卡片 DOM 的 `scrollIntoView` 并加短暂高亮样式，高亮 2s 后
  自动清除，组件卸载时清理定时器。

## 4. 正常、异常与边界路径

1. 正常提交：`registerJob(kind, id)` 后选择态立即置位 → 首帧 poll 前显示「提交中」
   → poll 返回匹配 job 后显示进度；
2. 切 tab 恢复：任务运行中切到其他 tab 再切回（含点击「查看」），页面重挂后凭 App
   的 `selectedJobIds` 与 `activeJobs` 直接恢复任务视图，无需重新提交；
3. 「查看」跨 tab：从其他 tab 点击，切换到 `kindTab(kind)` 对应页并展示任务视图；
4. 「查看」同 tab：不发生 tab 变化，任务卡片滚动到可视区并高亮，禁止静默无变化；
5. grace 清理回退：终态 8s 后 App 清空该 kind 的 activeJob 与选择态，页面回到表单；
   不白屏、不抛错；
6. poll 失败：沿用既有静默跳过、下一轮重试；选择态保持，页面停留在当前任务容器；
7. id 不匹配：`activeJob` 属于旧任务而选择态指向新任务时按 `submitting` 处理，
   绝不展示旧任务数据（防串台）；
8. 取消/失败返回：UploadPage 取消上传、WritePage 失败后「返回修改参数」调用
   `registerJob(kind, null)`，三个相关状态同步清空；
9. `kindTab` 既有去向锁定：parse → upload；bid / supplement / rewrite 及其他非 parse
   字符串 → write（supplement/rewrite 的全局任务栏接入属 T-034，本任务只锁定映射
   函数现状）。

## 5. 测试策略（全部离线，禁止真实网络）

### 5.1 纯函数层（vitest，`tests/task_state.test.ts`）

- `derivePageJobState`：null 选择态 → form；选择态置位且无 activeJob → submitting；
  job_id 匹配 → job 并返回同一引用；activeJob 为旧 id → submitting（不返回旧 job）；
- `planJobJump`：parse 在 upload tab → 不切 tab；parse 在 write tab → 切到 upload；
  bid/supplement/rewrite 在 upload tab → 切到 write；已在 write tab → 不切；
- `kindTab`：parse/bid/supplement/rewrite 与未知字符串兜底去向（既有 4 例保留，
  补未知字符串兜底例）。

### 5.2 组件层（Testing Library，新增 `tests/app_active_job.test.tsx`）

- `vi.mock('../src/api')` 接管全部 API 函数，夹具返回 fake `JobView`，不发真实请求；
- `vi.useFakeTimers` + `advanceTimersByTime` 驱动 1.5s 轮询与 8s grace，禁止真实
  sleep；user-event 触发真实点击/文件选择事件；
- 覆盖场景：
  1. 解析任务运行中切 tab 再切回，任务进度视图仍在（旧实现会丢）；
  2. 跨 tab 点击「查看」跳到上传页并显示任务视图；
  3. 同 tab 点击「查看」调用任务卡片 `scrollIntoView`（`vi.spyOn` 断言）；
  4. 终态后推进 8s grace，页面回退表单态且无报错；
  5. 编写任务通过受控 props 在组件重挂后恢复任务视图（WritePage 受控关系）。
- 每个用例必须有断言；禁止 `// @ts-ignore` 掩盖类型错误。

### 5.3 手动验证（补充，组件测试无法覆盖真实布局/滚动）

启动前后端后人工执行一次解析与一次编写任务，分别验证切 tab 恢复、「查看」跳转与
同 tab 反馈、grace 清理回退；步骤与结果记入 `工作日志.md`，未实际执行不得标注
「已验证」。

## 6. 验收标准

1. 任务进行中切 tab 再切回（含「查看」入口）任务进度视图自动恢复，不依赖页面本地
   state；
2. 同 tab 点击「查看」有滚动/高亮反馈；
3. `kindTab` 对 parse/bid/supplement/rewrite 的去向有测试锁定；
4. 选择/恢复/回退逻辑抽纯函数并补 vitest；tab 恢复与「查看」反馈有组件测试；
   `npm test` 用例数在 T-035 的 70 例基线上只增不减，`npm run build`
   （`tsc --noEmit && vite build`）通过；
5. 本规格写明 App 单一数据源与页面受控派生关系；
6. grace 清理后页面安全回退表单态，无白屏/报错。

## 7. 明确范围外（越界即返工）

- 终态结果持久留存、「再写一份/再上传」复位、HistoryPage 自动刷新（T-030）；
- 单槽改多任务集合、排队语义、supplement/rewrite 接入全局任务栏（T-034）；
- 后端进度回调补点（T-032）、`jobEquals` 判等修复（T-033）；
- ResultView 的 supplement/rewrite 私有轮询改造；
- `src/` 之外的后端任何改动；不新增依赖、不引入 pnpm/yarn 锁文件。
