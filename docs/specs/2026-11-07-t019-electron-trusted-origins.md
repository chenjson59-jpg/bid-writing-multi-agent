# 规格：Electron HTTP 下载增加可信后端地址白名单（T-019）

## 1. 用户场景与业务目标

Electron 主进程 `desktop:open-path` IPC 对渲染进程传来的任意 `http(s)://` URL 都执行
`net.fetch` 下载并用系统默认程序打开，没有来源约束。一旦渲染进程被 XSS 或依赖投毒，
恶意脚本可借桌面端发起任意地址请求（可触达内网地址的 SSRF）并自动打开下载内容。
本地文件分支已有 `upload/` 白名单（`resolveAllowed`），HTTP 分支缺同等约束。

目标：主进程对下载 URL 做**精确 origin 匹配**的可信后端白名单校验，非法地址直接拒绝，
不发起任何网络请求；保持 `contextIsolation=true`、`nodeIntegration=false` 不变。

## 2. 输入、输出与副作用

### 2.1 新增纯逻辑模块（无 Electron 依赖，可单测）

`frontend/electron/trust.cjs`（CommonJS，与主进程模块制式一致）：

- `DEFAULT_TRUSTED_ORIGINS`：默认白名单，包含本机后端两个回环 origin
  `http://localhost:8001`、`http://127.0.0.1:8001`（对齐后端默认端口）；
- `parseTrustedOrigins(raw)`：解析逗号分隔的环境变量值，逐项 trim，经 URL 解析只保留
  http/https origin，非法项跳过，去重；非字符串/空值返回 `[]`；
- `resolveTrustedOrigins(envValue)`：默认白名单合并环境变量白名单并去重；
- `isTrustedUrl(target, allowedOrigins)`：非字符串、URL 解析失败、非 http/https 协议、
  或 origin 不在白名单集合内一律返回 `false`；严格按标准化后的 `URL.origin` 精确匹配，
  不做前缀/后缀字符串包含匹配。

### 2.2 主进程接入（`main.cjs`）

- 启动时取环境变量 `ELECTRON_TRUSTED_ORIGINS`（逗号分隔），与默认回环白名单合并；
- `desktop:open-path` 的 HTTP 分支在 `net.fetch` 前先过 `isTrustedUrl`，不通过直接
  返回 `{ ok: false, error: '下载地址不在可信后端范围内' }`，不下载、不打开；
- 本地路径分支、`contextIsolation`、`nodeIntegration`、preload 桥均不变。

### 2.3 部署侧联动

- 实际部署的桌面端构建时 `VITE_API_BASE` 指向远程后端，主进程读不到构建期变量，必须
  在启动环境设置与之一致的 `ELECTRON_TRUSTED_ORIGINS` 同源 origin；仓库内不写死
  任何地址（延续 T-003 决策），由本地 gitignored 启动脚本注入；
- 不新增 HTTP API、不改变渲染进程代码与 IPC 签名。

## 3. 正常、异常与边界路径

- 本地回环（默认白名单）：`http://localhost:8001/api/bids/...` 放行；
- 任意外部 origin（含内网其他主机、公网恶意站点）：拒绝；
- 协议降级/伪装：`file://`、`javascript:`、`data:` 等非 http(s) 拒绝；
- userinfo 伪装：`http://localhost:8001@evil.com/x` 的 origin 是 `http://evil.com`，
  精确匹配后拒绝；
- 默认端口归一化：`http://host:80` 与 `http://host` 的 origin 一致（`new URL` 标准化）；
- 环境变量含空白、重复、非法条目：trim、去重、跳过非法项，不影响其余条目生效；
- 目标为相对路径（开发态相对 `/api/...`）：不是合法绝对 URL，走不到下载逻辑（既有
  isHttpUrl 已拦截），isTrustedUrl 同样返回 false；
- 白名单为空（理论上不会，因为有默认值）：全部 HTTP URL 拒绝。

## 4. 兼容性与验收标准

- 新增 `frontend/tests/electron_trust.test.ts`（vitest，node 环境，无 Electron 依赖）：
  覆盖默认回环放行、环境变量扩展、外部/内网地址拒绝、非 http 协议拒绝、userinfo 伪装、
  默认端口归一化、环境变量容错、非字符串输入、主进程配置守卫；
- 主进程守卫测试：读取 `main.cjs` 源码断言仍含 `contextIsolation: true` 与
  `nodeIntegration: false`，且下载前调用可信校验（源码级回归锁定）；
- `npm test` 全过、`npm run build`（含 `tsc --noEmit`）通过（测试文件不在 tsc include
  内，不引入类型声明文件）；
- 文档同步：`docs/api-contract.md` §11 增加 HTTP 可信 origin 白名单小节与环境变量；
  `README.md` 桌面端小节补充说明，并修正既有笔误（代码读 `UPLOAD_ROOT`，文档误写
  `BIDDING_UPLOAD_ROOT`）；
- 本地 gitignored 启动脚本补设该环境变量的示例值，保证重装后桌面端打开 Word 不断流；
- 后端无改动，`uv run pytest -q` 与 Ruff 仅做回归确认，不需要新增后端用例。
