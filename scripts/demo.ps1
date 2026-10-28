# 一键启动演示环境：Redis（可选）→ Celery worker（可选）→ FastAPI → 前端 dev server → 自检。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 [-Backend celery|local] [-NoFrontend]
[CmdletBinding()]
param(
    [ValidateSet('celery', 'local')]
    [string]$Backend = 'celery',
    [int]$ApiPort = 8001,
    [int]$FrontPort = 5173,
    [int]$RedisPort = 6379,
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "== 招标文件多智能体系统：演示启动（backend=$Backend）==" -ForegroundColor Cyan

function Test-Port {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $Port)
        $client.Close()
        return $true
    }
    catch {
        return $false
    }
}

function Start-LoggedWindow {
    param([string]$Title, [string]$Command)
    $inner = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$root'; $Command"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $inner | Out-Null
}

# ---- 0. 前置条件 ----
if (-not (Test-Path '.env')) {
    Write-Host '缺少 .env：先执行 copy .env.example .env 并填入 API Key' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '缺少虚拟环境：先执行 uv sync' -ForegroundColor Red
    exit 1
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '缺少 uv：https://docs.astral.sh/uv/ 安装后重试' -ForegroundColor Red
    exit 1
}

# ---- 1. Redis ----
if ($Backend -eq 'celery') {
    if (Test-Port $RedisPort) {
        Write-Host "[1/4] Redis 已在 127.0.0.1:$RedisPort 运行" -ForegroundColor Green
    }
    else {
        $redisExe = (Get-Command redis-server -ErrorAction SilentlyContinue)
        if ($null -ne $redisExe) {
            Start-Process -FilePath $redisExe.Source -ArgumentList "--port $RedisPort" -WindowStyle Minimized | Out-Null
            $ok = $false
            for ($i = 0; $i -lt 10; $i++) {
                Start-Sleep -Seconds 1
                if (Test-Port $RedisPort) { $ok = $true; break }
            }
            if ($ok) {
                Write-Host "[1/4] 已启动 redis-server（127.0.0.1:$RedisPort）" -ForegroundColor Green
            }
            else {
                Write-Host 'Redis 启动失败：手动执行 redis-server，或改用 scripts\demo.ps1 -Backend local' -ForegroundColor Red
                exit 1
            }
        }
        else {
            Write-Host '未找到 redis-server：装一个 Redis（或 Memurai），或改用 -Backend local 演示' -ForegroundColor Red
            exit 1
        }
    }
}
else {
    Write-Host '[1/4] local 后端：跳过 Redis 与 worker' -ForegroundColor Yellow
}

# ---- 2. Worker ----
if ($Backend -eq 'celery') {
    Start-LoggedWindow 'bidding-worker' 'uv run celery -A src.celery_app worker --loglevel=info --pool=solo'
    Write-Host '[2/4] worker 已在新窗口启动（首次启动需等待加载 LangGraph 依赖）' -ForegroundColor Green
}

# ---- 3. API ----
if (Test-Port $ApiPort) {
    Write-Host "[3/4] 端口 $ApiPort 已被占用，跳过启动（若非本项目请先关闭占用进程）" -ForegroundColor Yellow
}
else {
    if ($Backend -eq 'local') {
        Start-LoggedWindow 'bidding-api' "`$env:TASK_BACKEND='local'; uv run uvicorn api.main:app --host 127.0.0.1 --port $ApiPort"
    }
    else {
        Start-LoggedWindow 'bidding-api' "uv run uvicorn api.main:app --host 127.0.0.1 --port $ApiPort"
    }

    $healthy = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2 | Out-Null
            $healthy = $true
            break
        }
        catch {
            continue
        }
    }
    if ($healthy) {
        Write-Host "[3/4] API 已就绪：http://127.0.0.1:$ApiPort" -ForegroundColor Green
    }
    else {
        Write-Host '[3/4] API 在 120 秒内未就绪，查看 bidding-api 窗口的报错' -ForegroundColor Red
    }
}

# ---- 4. 前端 ----
if ($NoFrontend) {
    Write-Host '[4/4] 跳过前端（-NoFrontend）' -ForegroundColor Yellow
}
elseif (-not (Test-Path 'frontend\node_modules')) {
    Write-Host '[4/4] 前端依赖未安装：先执行 cd frontend; npm install，然后重跑本脚本' -ForegroundColor Yellow
}
else {
    $devRunner = "npm run dev -- --port $FrontPort"
    Start-LoggedWindow 'bidding-frontend' "Set-Location '$root\frontend'; $devRunner"
    Write-Host "[4/4] 前端已启动：http://127.0.0.1:$FrontPort" -ForegroundColor Green
}

Write-Host ''
& "$PSScriptRoot\check.ps1" -ApiPort $ApiPort
