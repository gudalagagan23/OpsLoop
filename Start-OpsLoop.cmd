@echo off
rem Double-click to start OpsLoop (Docker stack, worker, public tunnel) and keep it running.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-opsloop.ps1" %*
pause
