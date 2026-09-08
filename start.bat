@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

set GATEWAY_PORT=8766
set WEB_PORT=8010
set GATEWAY_TIMEOUT=30
set WEB_TIMEOUT=45
set GATEWAY_HEALTH_URL=http://127.0.0.1:%GATEWAY_PORT%/admin/health
set WEB_HEALTH_URL=http://127.0.0.1:%WEB_PORT%/api/sessions

echo ============================================
echo   TushareAgent 一键启动
echo ============================================
echo.

REM ---------- 0. 前置工具检查 ----------
where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv，请先安装：https://github.com/astral-sh/uv
    pause
    exit /b 1
)
where curl >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 curl，本脚本依赖 curl 做健康检查（Win10 1803+/Win11 自带）
    pause
    exit /b 1
)

REM ---------- 1. 检测环境，没有就 uv sync ----------
if not exist ".venv\Scripts\python.exe" (
    echo [环境] 未检测到 .venv，正在执行 uv sync（首次可能较慢）...
    uv sync
    if errorlevel 1 (
        echo [错误] uv sync 失败，请检查网络/依赖后重试
        pause
        exit /b 1
    )
    echo [环境] uv sync 完成
) else (
    echo [环境] 已检测到 .venv，跳过 uv sync
)

if not exist ".env" (
    echo [错误] 缺少根目录 .env，请先复制 .env.example 为 .env 并填好 DS_API_KEY 等配置
    pause
    exit /b 1
)
if not exist "mcp_gateway\.env" (
    echo [错误] 缺少 mcp_gateway\.env，请先复制 mcp_gateway\.env.example 为 mcp_gateway\.env
    echo         并配置 TUSHARE_MCP_URL（网关是唯一持有上游数据源凭证的进程）
    pause
    exit /b 1
)

REM 前端产物缺失时尝试自动构建（非必须，缺失只影响页面，不影响 API）
if not exist "scripts\web\dist\index.html" (
    where npm >nul 2>nul
    if not errorlevel 1 (
        echo [环境] 未检测到前端产物 scripts\web\dist，正在构建...
        pushd scripts\web
        call npm ci
        if not errorlevel 1 call npm run build
        popd
    ) else (
        echo [提示] 未检测到前端产物且未安装 npm，Web 将只提供 API、无页面
    )
)

REM ---------- 2. 启动 MCP 网关（先探活，避免重复启动） ----------
echo.
echo [网关] 检查 %GATEWAY_HEALTH_URL% ...
call :check_http %GATEWAY_HEALTH_URL% GW_CODE
if "!GW_CODE!"=="200" (
    echo [网关] 已在运行，跳过启动
) else (
    echo [网关] 启动 mcp_gateway（端口 %GATEWAY_PORT%）...
    start "TushareAgent-Gateway" cmd /k "uv run uvicorn mcp_gateway.app:app --host 127.0.0.1 --port %GATEWAY_PORT%"

    set GATEWAY_OK=0
    for /l %%i in (1,1,%GATEWAY_TIMEOUT%) do (
        call :check_http %GATEWAY_HEALTH_URL% GW_CODE
        if "!GW_CODE!"=="200" (
            set GATEWAY_OK=1
            goto :gateway_ready
        )
        timeout /t 1 /nobreak >nul
    )
    :gateway_ready
    if "!GATEWAY_OK!"=="0" (
        echo [错误] 网关启动超时（%GATEWAY_TIMEOUT%s），健康检查未通过
        echo [回退] 关闭网关窗口...
        taskkill /FI "WINDOWTITLE eq TushareAgent-Gateway" /T /F >nul 2>nul
        echo [回退] 已清理，请查看网关窗口日志排查（常见原因：mcp_gateway\.env 里 TUSHARE_MCP_URL 未配置或端口被占用）
        pause
        exit /b 1
    )
)
echo [网关] 就绪：http://127.0.0.1:%GATEWAY_PORT%/mcp

REM ---------- 3. 启动 Web 主程序（先探活，避免重复启动） ----------
echo.
echo [Web] 检查 %WEB_HEALTH_URL% ...
call :check_http %WEB_HEALTH_URL% WEB_CODE
if "!WEB_CODE!"=="200" (
    echo [Web] 已在运行，跳过启动
) else (
    echo [Web] 启动 demomcp（端口 %WEB_PORT%）...
    start "TushareAgent-Web" cmd /k "uv run uvicorn demomcp.entry.web:app --port %WEB_PORT%"

    set WEB_OK=0
    for /l %%i in (1,1,%WEB_TIMEOUT%) do (
        call :check_http %WEB_HEALTH_URL% WEB_CODE
        if "!WEB_CODE!"=="200" (
            set WEB_OK=1
            goto :web_ready
        )
        timeout /t 1 /nobreak >nul
    )
    :web_ready
    if "!WEB_OK!"=="0" (
        echo [错误] Web 启动超时（%WEB_TIMEOUT%s），健康检查未通过
        echo [回退] 关闭 Web 窗口...
        taskkill /FI "WINDOWTITLE eq TushareAgent-Web" /T /F >nul 2>nul
        echo [回退] 本次由本脚本拉起的网关一并关闭，恢复到启动前状态...
        taskkill /FI "WINDOWTITLE eq TushareAgent-Gateway" /T /F >nul 2>nul
        echo [回退] 已清理，请查看 Web 窗口日志排查（常见原因：.env 里 DS_API_KEY 未配置或端口被占用）
        pause
        exit /b 1
    )
)

REM ---------- 4. 启动成功 ----------
echo.
echo ============================================
echo   启动成功！
echo   前端页面: http://127.0.0.1:%WEB_PORT%
echo   网关地址: http://127.0.0.1:%GATEWAY_PORT%/mcp（/admin/sources 管理数据源）
echo ============================================
start http://127.0.0.1:%WEB_PORT%
pause
exit /b 0

REM ---------- 子程序：HTTP 状态码探测（不抛错，探测失败记 000） ----------
:check_http
set "_URL=%~1"
set "_OUTVAR=%~2"
curl -s -o nul -m 2 -w "%%{http_code}" "%_URL%" > "%TEMP%\tushareagent_http_code.txt" 2>nul
set /p _CODE=<"%TEMP%\tushareagent_http_code.txt"
if not defined _CODE set "_CODE=000"
set "%_OUTVAR%=%_CODE%"
exit /b 0
