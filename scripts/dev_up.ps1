# 本地开发启动器：把 MCP 网关与 Web 各自拉起成**一个独立进程**（不是父子关系）。
#
#   .\scripts\dev_up.ps1              # 起网关（带自动重启）+ 起 Web
#   .\scripts\dev_up.ps1 -GatewayOnly # 只起网关
#   .\scripts\dev_up.ps1 -WebOnly     # 只起 Web（网关另开/不开都行，Web 会退避重连）
#
# 为什么分两个进程：解耦。网关持有全部上游 MCP 源（Tushare/万得…），Web 只是它的 MCP 客户端——
# 关掉任一个另一个照常活着。杀掉网关窗口 3 秒后会自己起回来（对应生产的 restart: unless-stopped）。

param(
    [switch]$GatewayOnly,
    [switch]$WebOnly,
    [int]$GatewayPort = 8766,
    [int]$WebPort = 8010
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

function Start-Gateway {
    $envFile = Join-Path $repo 'mcp_gateway\.env'
    if (-not (Test-Path $envFile)) {
        Write-Host "缺少 mcp_gateway\.env —— 网关的配置是独立的一份，请先建好：" -ForegroundColor Yellow
        Write-Host "  1) copy mcp_gateway\.env.example mcp_gateway\.env"
        Write-Host "  2) 把项目根 .env 里这两行搬进去（网关是唯一持有上游源凭证的进程）："
        Write-Host "       TUSHARE_MCP_URL=...   （带 ?token=）" -ForegroundColor Gray
        Write-Host "       WIND_API_KEY=...      （不用万得可留空并设 WIND_ENABLED=false）" -ForegroundColor Gray
        throw "mcp_gateway\.env 不存在，网关未启动"
    }
    # 外层 while 循环 = 本地版「重启机制」：进程退出（崩了/被 kill）3s 后自动重起
    $inner = "while (`$true) { uv run uvicorn mcp_gateway.app:app --host 127.0.0.1 --port $GatewayPort; " +
             "Write-Host '[gateway] 进程退出，3s 后重启…' -ForegroundColor Yellow; Start-Sleep -Seconds 3 }"
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoExit', '-Command', "Set-Location '$repo'; $inner" `
        -WorkingDirectory $repo | Out-Null
    Write-Host "[gateway] 已在新窗口启动（http://127.0.0.1:$GatewayPort/mcp，管理 API /admin/sources），带自动重启" -ForegroundColor Green
}

function Start-Web {
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoExit', '-Command', "Set-Location '$repo'; uv run uvicorn demomcp.entry.web:app --port $WebPort" `
        -WorkingDirectory $repo | Out-Null
    Write-Host "[web] 已在新窗口启动（http://127.0.0.1:$WebPort）" -ForegroundColor Green
}

if ($WebOnly) {
    Start-Web
} elseif ($GatewayOnly) {
    Start-Gateway
} else {
    Start-Gateway
    Start-Web
    Write-Host ''
    Write-Host '两个独立进程已起来；停掉其中一个不会影响另一个。' -ForegroundColor Cyan
    Write-Host '「设置」页可以看到 Tushare / 万得的按源开关（网关没起来时会显示不可达诊断）。' -ForegroundColor Cyan
}
