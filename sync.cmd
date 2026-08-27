@echo off
REM sync.cmd —— 双击一键上传（等同不带参数运行 scripts\sync_deploy.ps1，即增量 copy）
REM 如需一次性引导，改用： sync.cmd -Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sync_deploy.ps1" %*
