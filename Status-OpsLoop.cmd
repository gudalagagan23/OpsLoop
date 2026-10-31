@echo off
rem Show whether every part of OpsLoop is running.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\status-opsloop.ps1"
pause
