# install_dispatch_listener_startup_shortcut.ps1
# Idempotent installer: places a shell:startup .lnk shortcut that boots
# dispatch_listener (port 19100) into the operator's interactive session.
#
# WHY a shell:startup shortcut instead of the MiruDispatchListener scheduled task:
#   The scheduled task fires "At system startup" via S4U logon. At boot,
#   before the operator logs in, the spawned process inherits Windows Session 0
#   (the non-interactive service session). A non-elevated worker shell (Claude
#   Code) running in the operator's interactive session (Session 1+) cannot
#   kill a Session 0 process without SeDebugPrivilege -- which defeats the
#   restart mechanism. A shell:startup shortcut fires at LOGON, so the process
#   spawns in the operator's interactive session where non-elevated Stop-Process
#   works without UAC.
#
# Primary boot path (after this script runs + next reboot/logon):
#   shell:startup shortcut -> start_dispatch_listener.ps1 -> node index.js
#
# Fallback (still registered, but should not fire if shortcut is present):
#   MiruDispatchListener scheduled task (S4U / AtStartup -> Session 0)
#   The Session 0 self-check in start_dispatch_listener.ps1 causes that path
#   to exit 1 immediately, surfacing the regression.
#
# IDEMPOTENCY: if the shortcut already exists and points at the correct target,
# this script exits 0 without modifying anything. Safe to re-run after a
# repo move or OS reinstall.
#
# Usage (does NOT require elevation):
#   powershell -ExecutionPolicy Bypass -File windows\install_dispatch_listener_startup_shortcut.ps1
#
# Parameters (override in tests only -- do NOT pass in production):
#   -StartupFolder  <path>   Override the Startup folder path (default: shell:startup)
#   -WrapperScript  <path>   Override the wrapper script path (default: $PSScriptRoot\start_dispatch_listener.ps1)

[CmdletBinding()]
param(
    [string]$StartupFolder = "",
    [string]$WrapperScript = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$scriptDir  = $PSScriptRoot
$repoRoot   = Split-Path -Parent $scriptDir
$logDir     = Join-Path $repoRoot "logs"
$installLog = Join-Path $logDir "install_dispatch_listener_shortcut.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-InstallLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "[$(Get-Date -Format o)] install_dispatch_listener_startup_shortcut: $Message"
    Add-Content -Path $installLog -Value $line -Encoding UTF8
    Write-Host "[install-dispatch-listener-shortcut] $Message"
}

$exitCode = 1

try {
    Write-InstallLog "action=install_begin"

    # Use wscript.exe + VBS as the shortcut target.
    # wscript.exe is a GUI-subsystem binary (no console window of its own).
    # The VBS calls WshShell.Run "powershell.exe ...", 0, False which sets
    # SW_HIDE at process-creation time -- no visible window at any point.
    # This is strictly better than a .lnk pointing directly at powershell.exe
    # with WindowStyle=7 (minimized), which briefly shows a taskbar button.
    $resolvedVbs = Join-Path $scriptDir "tasks\run_dispatch_listener.vbs"
    if ($WrapperScript -ne "") {
        # Test override: still accept a PS1 path but wrap it via VBS logic
        $resolvedVbs = $WrapperScript
    }
    Write-InstallLog "vbs_wrapper=$resolvedVbs"

    if (-not (Test-Path $resolvedVbs)) {
        throw "VBS wrapper not found at '$resolvedVbs'. Ensure the repo is intact before running this installer."
    }

    # Resolve wscript.exe
    $resolvedWscript = "$env:SystemRoot\System32\wscript.exe"
    if (-not (Test-Path $resolvedWscript)) {
        throw "wscript.exe not found at $resolvedWscript"
    }
    $expectedArgs = "`"$resolvedVbs`""

    # Resolve the startup folder
    if ($StartupFolder -eq "") {
        # shell:startup resolves to:
        # %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
        $shell = New-Object -ComObject WScript.Shell
        $resolvedStartupFolder = $shell.SpecialFolders("Startup")
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
    } else {
        $resolvedStartupFolder = $StartupFolder
    }
    Write-InstallLog "startup_folder=$resolvedStartupFolder"

    $shortcutName = "MiruDispatchListener.lnk"
    $shortcutPath = Join-Path $resolvedStartupFolder $shortcutName

    # Idempotency check: if shortcut already exists and points at the correct
    # wscript.exe + VBS target, skip creation and exit 0.
    if (Test-Path $shortcutPath) {
        try {
            $checkShell  = New-Object -ComObject WScript.Shell
            $existingLnk = $checkShell.CreateShortcut($shortcutPath)
            $existingTarget = $existingLnk.TargetPath
            $existingArgs   = $existingLnk.Arguments
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($checkShell) | Out-Null

            if ($existingTarget -ieq $resolvedWscript -and $existingArgs -eq $expectedArgs) {
                Write-InstallLog "shortcut_exists=yes target_matches=yes -- skipping (idempotent)"
                $exitCode = 0
                return
            }
            Write-InstallLog "shortcut_exists=yes target_matches=no existing_target=$existingTarget existing_args=$existingArgs -- recreating"
        } catch {
            Write-InstallLog "shortcut_exists=yes read_failed=$($_.Exception.Message) -- recreating"
        }
    } else {
        Write-InstallLog "shortcut_exists=no -- creating"
    }

    # Create or recreate the shortcut
    New-Item -ItemType Directory -Force -Path $resolvedStartupFolder | Out-Null

    $wshShell  = New-Object -ComObject WScript.Shell
    $shortcut  = $wshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath       = $resolvedWscript
    $shortcut.Arguments        = $expectedArgs
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Description      = "Miru Dispatch Listener (port 19100) -- starts in operator interactive session, no visible window"
    $shortcut.WindowStyle      = 1  # irrelevant for wscript.exe (GUI subsystem, no console)
    $shortcut.Save()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wshShell) | Out-Null

    if (-not (Test-Path $shortcutPath)) {
        throw "Shortcut file was not created at '$shortcutPath' after Save()."
    }

    Write-InstallLog "shortcut_created=$shortcutPath"
    Write-InstallLog "target=$resolvedPowershell args='$expectedArgs'"
    Write-InstallLog "action=install_success"
    Write-InstallLog "NEXT_STEP: log off and back on (or reboot) so the shortcut fires in your interactive session"

    $exitCode = 0
} catch {
    Write-InstallLog "error=$($_.Exception.Message)"
    $exitCode = 1
} finally {
    $marker = if ($exitCode -eq 0) { "INSTALL_SUCCESS" } else { "INSTALL_FAILED" }
    Add-Content -Path $installLog -Value $marker -Encoding UTF8
    Write-Host "[install-dispatch-listener-shortcut] $marker"
}

exit $exitCode
