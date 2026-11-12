# 规格：库内历史高敏数据物理清理（T-041，方案 A：数据库层收敛）

## 1. 用户场景与业务目标

T-040 已将资料表单与 prompt 外发收敛为 7 字段白名单，但存量数据中仍保留 T-040
之前写入的高敏字段（similar_projects/personnel/financials/contact/notes）。只读盘点
（2026-11-12）确认三处数据库残留与一处接口暴露：

1. PostgreSQL `bidder_profile`：1 行（id=2）data 含 4 个移出键；
2. PostgreSQL `task_history`：6 个 bid 任务的 `options.profile` 内嵌完整 11 键资料
   （其中 1 行含 notes；5 succeeded、1 stale pending），手机号/邮箱具体值可检出；
3. `GET /api/tasks` 的 TaskHistoryItem.options 原样回传，任何持令牌调用方均可读到
   内嵌资料；前端 types 虽声明 options 但全代码零读取；
4. Chroma 向量库（354 块）与 task_history.summary 列经五针扫描零命中，无需处理。

用户已拍板方案 A：只做数据库层与接口层收敛，不改写历史标书产物文件（方案 B/C 范畴，
本规格不做）。业务目标：

1. 物理删除库内 T-040 移出字段，高敏针（手机号、邮箱等）在数据库 JSON 列中零残留；
2. `/api/tasks` 响应不再回显 `options.profile`，关闭接口暴露面；
3. 清洗可重复执行（幂等）、默认 dry-run、先备份后执行、具备回滚路径；
4. 不动表结构（无 Alembic）、不引入新依赖、不改前端；
5. 失败重试链路语义不破坏：retry 仍可读库内 options（profile 子键移除后按无资料
   重跑，生成占位符，与 T-040 后新建任务的无资料行为一致）。

## 2. 事实基线与设计决策

### 2.1 白名单单一事实源

7 字段白名单以 `src/bid_writer/schema.py:PROFILE_FIELD_LABELS` 的键为唯一事实源
（T-040 已收敛为 7 键），清洗模块不得另立常量副本，避免未来再次漂移。

### 2.2 task_history 的 profile 整体移除而非裁剪

决策：task_history.options 中的 `profile` 子键**整体删除**，不保留裁剪后的 7 键副本。

依据：

- options.profile 是 dispatch 落库的重试快照（api/dispatch.py 注释「失败重试时据此
  还原原参数」），不是业务主数据；低敏资料的权威来源是 bidder_profile 表；
- 整体移除使 task_history 中投标人资料零残留，暴露面最小化；
- 6 个受影响任务当前无 failed（retry 仅对 failed 开放），无即时行为变化；未来若重试
  旧任务，profile=None 走既有空资料路径（占位符），用户可在编写页重新从资料库选择；
- bidder_profile 行则保留 7 白名单键（该行是资料库的正常业务记录，不能整行删除）。

### 2.3 接口剥离位置

`repository._task_to_dict` 同时服务 `list_tasks`（接口）与 `get_task`（retry 读库
还原参数）。剥离逻辑只能放在 API 路由层，仓储层保持返回完整库内数据，否则 retry
会在接口函数之外被意外截断。

### 2.4 备份与回滚

执行前强制备份（脚本不自动备份，由操作者按本规格第 6 节手动完成并核验）：

- `pg_dump -Fc` 整库自定义格式备份 + 受影响行 JSON 导出，存放 gitignored 目录
  （tmp_output/t041-backup/），不入库；
- 备份后做一次恢复演练（恢复到临时 scratch 库，校验行数与样本键集合，再删除
  scratch 库），演练通过才允许 --apply；
- 回滚方式：从自定义格式备份整库恢复，或用 JSON 导出定向回写受影响行。

## 3. 输入输出与副作用

### 3.1 新增模块 `src/db/profile_sanitize.py`（纯函数 + 异步清洗）

纯函数（无 IO，单元测试直连）：

- `sanitize_profile_data(data: dict[str, Any]) -> dict[str, Any]`：仅保留白名单 7
  键中实际存在于入参的键，值原样保留（含 list/dict 结构），notes 与未知键丢弃；
  非 dict 入参返回空 dict；
- `strip_task_options(options: dict[str, Any]) -> dict[str, Any]`：返回浅拷贝并
  删除 `profile` 子键；无 profile 时返回内容相等的新 dict（不原地修改入参）；
- `sanitize_task_for_api(item: dict[str, Any]) -> dict[str, Any]`：对任务 dict 的
  options 应用上一条，其余字段原样保留；options 缺失/非 dict 时不抛异常。

异步函数（经会话工厂访问库）：

- `collect_cleanup_plan() -> CleanupPlan`：只读扫描，返回两类受影响行的 id 清单、
  各自清洗前后键集合预览与计数，不写库；
- `apply_cleanup() -> CleanupReport`：单事务内更新——bidder_profile 行 data 替换
  为 `sanitize_profile_data` 结果；task_history 行 options 替换为
  `strip_task_options` 结果；返回实际变更行数；已干净的行不产生 UPDATE（幂等）。

数据结构用 dataclass（CleanupPlan/CleanupReport），字段含计数与 id 列表，便于
脚本打印与测试断言。

### 3.2 接口变化

`GET /api/tasks`：每个 TaskHistoryItem 的 `options` 不再包含 `profile` 键；其余
options（export_word/max_revisions/max_sections/min_score/section_ids 等）原样
保留。HTTP 路径、方法、鉴权、响应 Schema 字段集合不变（options 仍是自由 dict），
属响应内容收敛而非 Schema 结构变更。

`GET /api/profiles` 不改：清洗后库内 data 本身只剩白名单键，接口自然收敛。

### 3.3 脚本 `scripts/sanitize_profile_data.py`

- 默认 `--dry-run`：调用 collect_cleanup_plan 并打印受影响行与预览，退出码 0，
  零写入；
- `--apply`：先打印同一计划，再执行 apply_cleanup，打印实际变更行数；
- `settings.database_url` 为空时打印明确错误并以退出码 2 结束（禁止静默成功）；
- 脚本只做编排，清洗逻辑全部在 src/db/profile_sanitize.py，可被 pytest 覆盖；
- 不新增依赖（argparse 为标准库）。

## 4. 正常 / 异常 / 边界路径

正常：

1. dry-run 输出 1 条 profile + 6 条 task 受影响（以执行时实扫为准），库内数据不变；
2. apply 后 SQL 复查 `similar_projects|personnel|financials|contact|notes` 在两表
   JSON 文本中零命中；再次 dry-run/apply 报告 0 变更（幂等）；
3. /tasks 响应 options 无 profile 键，非 profile 选项保留；retry 路径单测证明
   `get_task` 仍返回库内完整 options（清洗后即无 profile 的 options）。

异常与边界：

- database_url 为空：脚本退出码 2，提示配置；
- 清洗期间库不可用：会话异常向上抛由脚本捕获打印非零退出码，事务回滚不留半写；
- 入参 data/options 为 None、空 dict、含未知键、白名单键值为 list/dict：纯函数
  逐类有测试，不抛异常、不丢白名单值；
- task_history 中 options 无 profile 的行（parse/rewrite/多数 bid）：不产生 UPDATE；
- bidder_profile 某行已只有白名单键：不产生 UPDATE。

## 5. 权限与安全

- 不新增接口、不改鉴权；本任务是收窄暴露面；
- 备份文件含真实高敏数据，仅存 gitignored 本地目录，禁止入库、禁止截图外发；
- 台账与工作日志只记录 id、键集合与计数，不记录手机号等具体值；
- 清洗脚本不接受任意 SQL/表名参数，表与列在代码内固定，防止注入与误操作扩大化。

## 6. 备份与执行流程（操作者手册）

1. `pg_dump -Fc -h 127.0.0.1 -U postgres -d bidding -f tmp_output/t041-backup/bidding_<ts>.dump`；
2. 只读导出受影响行 JSON（bidder_profile id=2 全量、6 个 job_id 的 options）到同目录；
3. 恢复演练：建 scratch 库 → pg_restore 入 scratch → 核对行数/键集合 → 删除 scratch；
4. `uv run --frozen python scripts/sanitize_profile_data.py`（dry-run）核对计划；
5. 获用户当轮执行授权后 `--apply`，SQL 复查高敏针零命中、二次执行 0 变更；
6. 备份保留至用户确认验收后再处置（默认保留，不自动删除）。

## 7. 测试计划（严格 TDD，先红后绿）

### 7.1 新增 tests/test_profile_sanitize.py（SQLite 内存/文件库，离线）

1. 纯函数：白名单保留、移出键与 notes/未知键丢弃、值结构不变、非 dict/空输入、
   不原地修改入参；
2. API 剥离：含 profile 的 options 剥离后无 profile 且其他键保留；无 profile 不报错；
3. 异步清洗（复用 test_db.py 的 SQLite db 夹具模式）：seed 1 条 legacy profile +
   多条 task（含/不含 profile、parse 类无 profile），dry-run 不改库只出计划；
   apply 后断言两表内容、变更行数；第二次 apply 报告 0 变更（幂等）。

### 7.2 接口测试（tests/test_api.py 增补，local_backend 离线夹具）

monkeypatch routes.list_tasks 返回含 options.profile 的任务项，GET /api/tasks 响应
options 无 profile 键、其他选项保留；同时断言仓储 get_task 路径不经该剥离
（模块边界单测：sanitize 仅在路由层调用）。

### 7.3 全量门禁

- `uv run --frozen ruff check api src tests scripts eval` 全过；
- `uv run --frozen pytest -q` 全绿，用例只增不减；
- 两个 eval runner 不受影响仍全匹配（本任务不触 bid_writer 逻辑，实跑确认）；
- 无新依赖、uv.lock 不暂存；前端零改动（无需 npm，build 已在 T-040 主干验证）。

## 8. 验收标准

1. 规格经用户确认后再写实现（本规格）；
2. 纯函数、脚本、接口剥离均有离线测试，先红后绿；
3. 备份与恢复演练完成并有记录，--apply 获用户当轮单独授权；
4. apply 后数据库两表高敏针零命中、/tasks 无 profile 回显、二次执行 0 变更；
5. 全量门禁绿；无 Alembic、无新依赖、无前端改动、uv.lock 零提交；
6. 台账与工作日志记录清理前后键集合/行数对照与备份位置（不记具体高敏值）。

## 9. 不做项（边界）

- 不改写 upload/bids 下历史标书 md/docx（方案 B，需另立任务与产物备份方案）；
- 不清理 Chroma（已验证零命中）；
- 不处置 stale pending 任务 27e123ef 的状态本身（仅清洗其 options.profile）；
- 不做散文级高敏内容枚举（方案 C）；
- 不删除 bidder_profile 整行，仅裁剪 data 键；
- 不新增自动备份/定时清理机制。