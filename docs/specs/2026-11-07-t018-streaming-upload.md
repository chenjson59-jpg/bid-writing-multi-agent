# 规格：上传接口流式落盘，避免整文件读入内存（T-018）

## 1. 用户场景与业务目标

`POST /api/parse` 接收中文招标文件 PDF，配置的上传上限为
`settings.api_max_upload_mb = 200`。当前 `create_parse`（`api/routes.py`）的处理顺序是：

1. 扩展名校验；
2. `content = await file.read()` —— 把整个上传文件物化成**一个 bytes 对象**；
3. 大小、空文件、PDF 文件头校验；
4. 对整包内容计算 `sha256` 取前 16 位作为 `doc_id`；
5. 写 `.incoming` 临时文件，再 `os.replace` 原子转正。

问题在于第 2 步：200MB 上限意味着单个请求的进程峰值内存可超过 200MB（multipart
解析的 SpooledTemporaryFile 与整包 bytes 同时存在），并发上传时存在 OOM 风险。

目标：改为**固定块大小流式读取**，边落盘、边增量哈希、边累计字节数，使接口内存
占用与文件大小解耦；对外 HTTP 契约、`doc_id` 语义、落盘文件名与原子替换行为保持
不变。本任务对齐 AGENTS.md 第 6 节「文件写入优先临时文件加原子替换」。

## 2. 输入、输出与副作用

- 输入不变：multipart 表单字段 `file`，以及 `parser` / `force` / `extract` /
  `ingest` 查询/表单参数。
- 输出不变：成功返回 `ParseAccepted`（202），字段与 `job_url` 规则不变；
  错误码语义与文案保持：400（扩展名/空文件/文件头）、413（超限）、422（缺文件）、
  503（队列不可用）、429（排队达上限）。
- 副作用：
  - 成功：`.incoming/{uuid}.pdf` 临时文件经 `os.replace` 原子转正为
    `FILES_DIR/{doc_id}_{safe_filename}`；
  - `doc_id = sha256(文件完整字节).hexdigest()[:16]`，增量哈希结果必须与整包哈希
    逐字节一致；
  - 失败：临时文件必须删除，`.incoming` 与 `FILES_DIR` 不得残留半成品；
  - 不提交任何任务。
- 无 HTTP Schema、数据库、向量库或产物结构变化，无新增配置项。

## 3. 设计约束（Starlette 1.9 事实）

本项目安装的 Starlette 1.9.0 中，`UploadFile` 已移除 `chunk()` 方法与异步迭代协议，
仅保留 `await read(size=-1)` / `write` / `seek` / `close`。因此流式方案为：

```text
循环 await file.read(1 MiB)，直到返回空 bytes
```

每次读取的块大小固定为 1 MiB，常驻内存只与块大小相关，与文件总大小无关。

## 4. 正常、异常与边界路径

### 4.1 正常路径

- 逐块写入临时文件，逐块 `hasher.update(chunk)` 累计 SHA-256、累计字节数；
- 单块小文件与多块大文件的 `doc_id` 都等于整包 SHA-256 前 16 位；
- 读取结束后 `os.replace` 原子转正，后续队列检查与 `dispatch.submit_parse` 不变。

### 4.2 异常路径与错误优先级

错误优先级与旧实现严格一致（旧顺序：大小 → 空文件 → 文件头）：

1. **超限（413）**：每读一块先累计字节数，`total > max_bytes` 立即抛 413 并停止
   读取，不再消费后续数据；既覆盖首块即超限（如上限被 monkeypatch 为 0），也覆盖
   读到第二块才超限的中途场景；
2. **文件头错误（400）**：首个非空块不以 `b"%PDF"` 开头即拒绝；大小校验先于文件头
   校验，保证「既超限又坏头」时仍返回 413，与旧行为一致；
3. **空文件（400）**：循环没有产生任何块，返回「文件为空」；
4. 任何校验失败或读取异常（含客户端断连）：关闭文件句柄并 `unlink` 临时文件后
   原样抛出异常。

### 4.3 边界与不调整项

- 队列可用性与排队深度检查目前发生在文件转正**之后**，本任务保持该顺序不调整，
  避免扩大变更范围；即 503/429 时文件已落盘，与现状一致；
- 扩展名校验仍发生在读流之前，不会为非 PDF 文件创建临时文件；
- 不构造真实 200MB 文件做测试：通过 monkeypatch 调小上限与 1 MiB 级载荷模拟
  多块与中途超限场景。

## 5. 兼容性与验收标准

- 既有上传用例（非 PDF 扩展名、空文件、坏文件头、超限、缺文件 422）零修改通过；
  `test_parse_submit_and_poll` 对 `doc_id == sha256(content)[:16]` 的断言保持成立，
  证明增量哈希与整包哈希同值；
- 新增测试覆盖：
  1. 超过 1 MiB 的多块文件：202、`doc_id` 一致、转正文件字节与上传内容一致、
     `.incoming` 无残留；
  2. 流到第二块才超限：413、无临时文件残留、无转正文件、未提交任务（metrics
     `jobs_total = 0`）；
  3. 坏文件头与空文件：400 且无临时文件残留；
  4. 回归守卫：禁止以 `read()` / `read(-1)` 整文件读取，只允许定长 `read(size)`；
- 全部测试离线执行（TestClient + monkeypatch + Fake），不依赖真实 LLM、网络与
  Chroma，不触碰本地 `chroma_db/`；
- `uv run ruff check api src tests scripts` 与 `uv run pytest -q` 全量通过。
