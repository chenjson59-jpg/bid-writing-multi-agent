# 演示环境自检：接口 / 队列 / worker / 文档库 / 资料库逐项检查，异常时打印修复命令。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\check.ps1 [-ApiPort 8001] [-ApiToken xxx]
[CmdletBinding()]
param(
    [int]$ApiPort = 8001,
    [string]$ApiToken = $env:API_TOKEN
)

$ErrorActionPreference = 'Stop'
$base = "http://127.0.0.1:$ApiPort"
$headers = @{}
if ($ApiToken) { $headers['X-API-Token'] = $ApiToken }
$failed = 0

function Write-Result {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    if ($Ok) {
        Write-Host ("[ OK ] {0,-8} {1}" -f $Name, $Detail) -ForegroundColor Green
    }
    else {
        Write-Host ("[FAIL] {0,-8} {1}" -f $Name, $Detail) -ForegroundColor Red
        $script:failed++
    }
}

Write-Host "== 演示环境自检 $base ==" -ForegroundColor Cyan

# 1. 接口存活
$health = $null
try {
    $health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 5
    Write-Result '接口' $true ("v" + $health.version + " / task_backend=" + $health.task_backend)
}
catch {
    Write-Result '接口' $false ("/api/health 不可达 → 启动 API：uv run uvicorn api.main:app --host 127.0.0.1 --port $ApiPort")
}

if ($null -ne $health) {
    # 2. 队列（Redis）
    if ($health.task_backend -eq 'local') {
        Write-Result '队列' $true 'local 后端：进程内线程池，无需 Redis'
    }
    elseif ($health.broker_ok) {
        Write-Result '队列' $true ("Redis 可连通，当前排队 " + $health.queue_depth + " 个任务")
    }
    else {
        Write-Result '队列' $false 'Redis 不可连通 → redis-server 启动，或改用 scripts\demo.ps1 -Backend local'
    }

    # 3. worker
    if ($health.task_backend -eq 'local') {
        Write-Result 'Worker' $true 'local 后端：无需独立 worker'
    }
    elseif ($health.worker_ok) {
        Write-Result 'Worker' $true 'Celery worker 在线（已加载 bidding.parse_document / bidding.write_bid）'
    }
    else {
        Write-Result 'Worker' $false 'worker 未启动 → uv run celery -A src.celery_app worker --loglevel=info --pool=solo'
    }
}

# 4. 文档库
try {
    $docs = @(Invoke-RestMethod -Uri "$base/api/documents" -Headers $headers -TimeoutSec 10)
    $ready = @($docs | Where-Object { $_.has_extraction }).Count
    Write-Result '文档库' $true ("共 " + $docs.Count + " 份，其中已抽取 " + $ready + " 份")
    if ($docs.Count -eq 0) {
        Write-Host '       提示：文档库为空，先在「上传解析」页上传招标 PDF 并勾选结构化抽取' -ForegroundColor Yellow
    }
    elseif ($ready -eq 0) {
        Write-Host '       提示：没有已完成抽取的文档，编写任务会被 404 拒绝' -ForegroundColor Yellow
    }
}
catch {
    Write-Result '文档库' $false ("/api/documents 失败：" + $_.Exception.Message)
}

# 5. 资料库（数据库可选，不可用不影响主链路）
try {
    $profiles = @(Invoke-RestMethod -Uri "$base/api/profiles" -Headers $headers -TimeoutSec 10)
    Write-Result '资料库' $true ("共 " + $profiles.Count + " 份可复用资料")
    if ($profiles.Count -eq 0) {
        Write-Host '       提示：资料库为空（DATABASE_URL 留空则降级），演示前建议先录入一份' -ForegroundColor Yellow
    }
}
catch {
    Write-Result '资料库' $false ("/api/profiles 失败：" + $_.Exception.Message)
}

Write-Host ''
if ($failed -gt 0) {
    Write-Host "自检未通过：$failed 项异常，按提示修复后重跑 scripts\check.ps1" -ForegroundColor Red
    exit 1
}
Write-Host ("自检通过，可以演示：http://127.0.0.1:" + $ApiPort) -ForegroundColor Green
