# Show whether every part of OpsLoop is running, and the current public address.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Line([string]$name, [bool]$ok, [string]$detail = "") {
    $mark = if ($ok) { "OK  " } else { "DOWN" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host ("  {0}  {1,-22} {2}" -f $mark, $name, $detail) -ForegroundColor $color
}

function Get-Ok([string]$url) {
    try { return (Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 8).StatusCode -eq 200 } catch { return $false }
}

Write-Host ""
Write-Host "  OpsLoop status" -ForegroundColor Cyan
cmd /c "docker info >nul 2>&1"
Line "Docker" ($LASTEXITCODE -eq 0)
$running = @(cmd /c "docker compose ps --status running --format {{.Service}} 2>nul")
$need = "postgres", "taskflow-api", "traffic-gen", "prometheus", "opsloop-engine", "opsloop-heartbeat", "opsloop-api"
$missing = @($need | Where-Object { $running -notcontains $_ })
Line "Services (7)" ($missing.Count -eq 0) $(if ($missing.Count) { "not running: " + ($missing -join ", ") } else { "all running" })
Line "Backend API" (Get-Ok "http://localhost:8000/health") "http://localhost:8000/docs"
$worker = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "opsloop\.worker" })
Line "Worker (AI agents)" ($worker.Count -gt 0) "log: logs\worker.log"
$me = $PID
$starter = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match "-File\s+.*start-opsloop\.ps1" })
Line "Start OpsLoop window" ($starter.Count -gt 0) "keeps everything running while open"
$url = $null
$log = Join-Path $Root "logs\tunnel.log"
if (Test-Path $log) {
    $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
    if ($m) { $url = $m.Matches[0].Value }
}
Line "Public link" ($url -and (Get-Ok "$url/health")) $(if ($url) { $url } else { "not started" })
Write-Host ""
Write-Host "  Dashboard: https://ops-loop.vercel.app" -ForegroundColor Cyan
Write-Host ""
