@echo off
rem Stop OpsLoop: watcher window, worker, tunnel and the Docker stack.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-opsloop.ps1"
pause
