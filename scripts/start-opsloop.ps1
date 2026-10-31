# Start OpsLoop and keep it running.
#
#   1. Starts Docker Desktop if needed, then the stack (docker compose).
#   2. Starts the worker (investigations, fixes, validation, releases).
#   3. Opens a Cloudflare quick tunnel to the API (port 8000 only; the fault-control port
#      stays private) and publishes its address to the `backend-url` branch, which the Vercel
#      dashboard reads on load. No redeploy is needed when the address changes.
#   4. Keeps watching: restarts the stack, worker or tunnel if one stops, and keeps the laptop
#      awake while this window is open. Close the window (or Ctrl+C) to stop watching; the
#      services keep running until you run `docker compose down`.
#
# Run it with Start-OpsLoop.cmd (double-click), or from VS Code (runs on folder open).

param([switch]$Once)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$TunnelLog = Join-Path $Logs "tunnel.log"
$WorkerLog = Join-Path $Logs "worker.log"
$Dashboard = "https://ops-loop.vercel.app"
$env:PYTHONUNBUFFERED = "1"
$Host.UI.RawUI.WindowTitle = "OpsLoop - running"

function Say([string]$msg, [string]$color = "Gray") {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor $color
}

function Test-Url([string]$url, [int]$timeout = 8) {
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec $timeout).StatusCode -eq 200
    } catch {
        return $false
    }
}

function Find-Cloudflared {
    foreach ($p in @("C:\Program Files (x86)\cloudflared\cloudflared.exe", "C:\Program Files\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { return $p }
    }
    $c = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

# Keep the machine awake while this script runs (released automatically when it exits).
Add-Type -Namespace OpsLoop -Name Power -MemberDefinition @"
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
"@
function Stay-Awake { [void][OpsLoop.Power]::SetThreadExecutionState([uint32]2147483649) }  # CONTINUOUS | SYSTEM_REQUIRED

# ---- Docker and the stack -----------------------------------------------------------------

function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

function Start-DockerDesktop {
    if (Test-Docker) { return $true }
    $exe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) { Say "Docker Desktop is not installed." "Red"; return $false }
    Say "Starting Docker Desktop..." "Yellow"
    Start-Process $exe | Out-Null
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Docker) { Say "Docker is running." "Green"; return $true }
    }
    Say "Docker did not start within 3 minutes. Open Docker Desktop and run this again." "Red"
    return $false
}

function Start-Stack {
    cmd /c "docker image inspect opsloop-python:dev >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Say "Building images (first run only, takes a while)..." "Yellow"
        cmd /c "docker compose build"
        cmd /c "docker build -f docker/sandbox.Dockerfile -t opsloop-sandbox:dev ."
    }
    Say "Starting the stack (database, TaskFlow, traffic, Prometheus, watchers, API)..."
    cmd /c "docker compose up -d --no-build postgres migrate taskflow-api traffic-gen prometheus opsloop-engine opsloop-heartbeat opsloop-api >nul 2>&1"
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Url "http://localhost:8000/health" 4) { Say "API is healthy on http://localhost:8000" "Green"; return $true }
        Start-Sleep -Seconds 2
    }
    Say "The API did not become healthy. Check: docker compose logs opsloop-api" "Red"
    return $false
}

# ---- worker -------------------------------------------------------------------------------

function Get-Worker {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "opsloop\.worker" }
}

function Start-Worker {
    if (Get-Worker) { return }
    if (-not (Test-Path $Python)) { Say "No virtualenv at .venv. Run: python -m venv .venv; .venv\Scripts\pip install -e .[dev]" "Red"; return }
    Say "Starting the worker (log: logs\worker.log)..."
    $cmd = '""' + $Python + '" -m opsloop.worker >> "' + $WorkerLog + '" 2>&1"'
    Start-Process -FilePath "cmd.exe" -ArgumentList "/s", "/c", $cmd -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
}

# ---- tunnel -------------------------------------------------------------------------------

$script:TunnelUrl = $null
$script:TunnelProc = $null

function Stop-Tunnels {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "localhost:8000" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Read-Shared([string]$path) {
    try {
        $fs = [System.IO.File]::Open($path, "Open", "Read", "ReadWrite")
        $sr = New-Object System.IO.StreamReader($fs)
        $text = $sr.ReadToEnd()
        $sr.Close()
        return $text
    } catch {
        return ""
    }
}

function Start-Tunnel {
    $cf = Find-Cloudflared
    if (-not $cf) { Say "cloudflared is not installed: winget install Cloudflare.cloudflared" "Red"; return $false }
    Stop-Tunnels
    Remove-Item $TunnelLog -ErrorAction SilentlyContinue
    Say "Opening a public tunnel to the API..."
    $script:TunnelProc = Start-Process -FilePath $cf -PassThru -WindowStyle Hidden -RedirectStandardError $TunnelLog `
        -ArgumentList "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://localhost:8000"
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        $m = [regex]::Match((Read-Shared $TunnelLog), "https://[a-z0-9-]+\.trycloudflare\.com")
        if ($m.Success) { $url = $m.Value }
    }
    if (-not $url) { Say "The tunnel did not report an address (see logs\tunnel.log)." "Red"; return $false }
    for ($i = 0; $i -lt 45; $i++) {
        if (Test-Url "$url/health" 6) { break }
        Start-Sleep -Seconds 2
    }
    $script:TunnelUrl = $url
    Say "Tunnel is up: $url" "Green"
    Publish-Url $url
    return $true
}

function Publish-Url([string]$url) {
    # One-file commit on its own branch, force-pushed, without touching the working tree.
    $json = '{"url":"' + $url + '","updated_at":"' + (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") + '"}'
    $tmp = Join-Path $Logs "backend.json"
    [System.IO.File]::WriteAllText($tmp, $json)
    $blob = (git hash-object -w $tmp | Out-String).Trim()
    # Feed mktree from a file: a PowerShell pipe would prepend a byte-order mark.
    $treeSpec = Join-Path $Logs "backend.tree"
    [System.IO.File]::WriteAllText($treeSpec, "100644 blob $blob`tbackend.json`n")
    $tree = (cmd /c "git mktree < `"$treeSpec`"" | Out-String).Trim()
    $commit = (git commit-tree $tree -m "Backend address for the dashboard" | Out-String).Trim()
    cmd /c "git push -q -f origin $($commit):refs/heads/backend-url >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Say "Published the address; $Dashboard will find it on its next load." "Green"
    } else {
        Say "Could not publish the address (git push failed). Paste it into the dashboard: Sign in > OpsLoop API address." "Yellow"
    }
}

# ---- main ---------------------------------------------------------------------------------

Write-Host ""
Write-Host "  OpsLoop  -  autonomous investigation and fix proposal, human-gated release (simulated)" -ForegroundColor Cyan
Write-Host ""
Stay-Awake
if (-not (Start-DockerDesktop)) { exit 1 }
if (-not (Start-Stack)) { exit 1 }
Start-Worker
if (-not (Start-Tunnel)) { Say "Continuing without a tunnel; the local dashboard still works." "Yellow" }
Write-Host ""
Say "Dashboard: $Dashboard" "Cyan"
Say "API (local): http://localhost:8000   Logs: $Logs" "Cyan"
if ($Once) { exit 0 }
Say "Watching. Leave this window open to keep everything running." "Cyan"

$tunnelFailures = 0
while ($true) {
    Start-Sleep -Seconds 20
    Stay-Awake
    if (-not (Test-Docker)) { Say "Docker stopped; restarting it." "Yellow"; [void](Start-DockerDesktop) }
    if (-not (Test-Url "http://localhost:8000/health" 6)) { Say "API not answering; restarting the stack." "Yellow"; [void](Start-Stack) }
    if (-not (Get-Worker)) { Say "Worker stopped; restarting it." "Yellow"; Start-Worker }
    if ($script:TunnelUrl -and (Test-Url "$($script:TunnelUrl)/health" 10)) {
        $tunnelFailures = 0
    } else {
        $tunnelFailures++
        if ($tunnelFailures -ge 3) {
            Say "Tunnel unreachable; opening a new one." "Yellow"
            $tunnelFailures = 0
            [void](Start-Tunnel)
        }
    }
}
