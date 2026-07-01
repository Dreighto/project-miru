# heartbeat_miru_flask_task.ps1
# Wakes the Miru Flask server (port 18765) every couple of minutes so the
# first request after an idle period does not pay a cold-start penalty.
# Hits /api/dev-status which is cheap; the response is discarded.
#
# Runs in a hidden window via run_heartbeat_miru_flask.vbs -- never shows
# a console popup. Logs only on failure.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$LogDir  = "D:\dev\miru\logs"
$LogPath = Join-Path $LogDir "heartbeat_miru_flask.log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:18765/api/dev-status" `
                              -UseBasicParsing `
                              -TimeoutSec 8 `
                              -ErrorAction Stop
} catch {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    "$stamp WARN heartbeat failed: $($_.Exception.Message)" | Out-File -FilePath $LogPath -Append -Encoding utf8
}
