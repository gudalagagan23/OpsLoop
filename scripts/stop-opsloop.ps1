# Stop OpsLoop: the watcher window, the worker, the tunnel and the Docker stack.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match "-File\s+.*start-opsloop\.ps1" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "opsloop\.worker" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
cmd /c "docker compose stop"
Write-Host "OpsLoop stopped. Start it again with Start OpsLoop." -ForegroundColor Cyan
