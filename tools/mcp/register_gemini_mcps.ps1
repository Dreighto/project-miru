# register_gemini_mcps.ps1 — register MCP servers in Gemini CLI via its
# official `gemini mcp add` command.
#
# Why this exists: editing .gemini/settings.json directly DOES NOT WORK.
# Gemini CLI strips unrecognized entries on next launch and rewrites the
# file to whatever it considers "trusted." The only reliable registration
# path is `gemini mcp add <name> <command> [args...]` — that bakes the
# entry into Gemini's trust model and persists across launches.
#
# This script is idempotent: re-running on already-registered MCPs is a
# no-op (gemini mcp add overwrites the existing entry with the same data).
#
# Run from anywhere:
#   pwsh -ExecutionPolicy Bypass -File tools/mcp/register_gemini_mcps.ps1
#
# After running:
#   - Relaunch Gemini CLI (close any open session, then `gemini` again)
#   - Type `/mcp` inside Gemini to verify all entries show as Ready
#
# Scope is `project` (writes to <cwd>/.gemini/settings.json). Run from
# D:\dev\miru to register against the Miru repo's config.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command gemini -ErrorAction SilentlyContinue)) {
    Write-Error "gemini CLI not found on PATH. Install via: npm install -g @google/gemini-cli"
    exit 1
}

function Add-McpServer {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$Command,
        [string[]]$ServerArgs = @(),
        [hashtable]$Env = @{}
    )

    Write-Host "  Registering: $Name" -ForegroundColor Yellow

    # Build the gemini mcp add invocation. The `--` separator forces all
    # subsequent tokens to be treated as args to <Command> rather than
    # gemini's own flags.
    $cliArgs = @('mcp', 'add', $Name, $Command, '-s', 'project')
    foreach ($k in $Env.Keys) {
        $cliArgs += @('-e', ('{0}={1}' -f $k, $Env[$k]))
    }
    if ($ServerArgs.Count -gt 0) {
        $cliArgs += '--'
        $cliArgs += $ServerArgs
    }

    & gemini @cliArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    FAIL: gemini mcp add returned $LASTEXITCODE" -ForegroundColor Red
    } else {
        Write-Host "    OK" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== Registering MCP servers with Gemini CLI ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "--- Infrastructure (CC + Gemini parity) ---"
Add-McpServer -Name 'docker'          -Command 'uvx'     -ServerArgs @('mcp-server-docker')
Add-McpServer -Name 'scheduled-tasks' -Command 'pwsh'    -ServerArgs @(
    '-NoProfile', '-NoLogo', '-NonInteractive', '-WindowStyle', 'Hidden',
    '-Command', "Import-Module PoshMCP; Start-PoshMcp -ConfigPath 'D:\dev\miru\tools\mcp\posh-mcp-config.json'"
)

Write-Host ""
Write-Host "--- Frontend (Gemini only — SvelteKit lane) ---"
Add-McpServer -Name 'svelte'        -Command 'npx.cmd' -ServerArgs @('-y', '@sveltejs/mcp')
Add-McpServer -Name 'shadcn-svelte' -Command 'npx.cmd' `
    -ServerArgs @('-y', '@jpisnice/shadcn-ui-mcp-server', '--framework', 'svelte') `
    -Env @{ 'GITHUB_PERSONAL_ACCESS_TOKEN' = '${env:GITHUB_TOKEN}' }
Add-McpServer -Name 'lucide-icons'  -Command 'npx.cmd' -ServerArgs @('-y', 'lucide-icons-mcp')
Add-McpServer -Name 'a11y-scanner'  -Command 'npx.cmd' -ServerArgs @('-y', 'mcp-accessibility-scanner')
Add-McpServer -Name 'vitest'        -Command 'npx.cmd' -ServerArgs @('-y', '@djankies/vitest-mcp')

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Next:"
Write-Host "  1. Close any running Gemini CLI session"
Write-Host "  2. Relaunch: cd D:\dev\miru && gemini"
Write-Host "  3. In the Gemini prompt, type: /mcp"
Write-Host "  4. All 7 new entries (docker, scheduled-tasks, svelte, shadcn-svelte,"
Write-Host "     lucide-icons, a11y-scanner, vitest) should show as Ready."
Write-Host ""
Write-Host "shadcn-svelte specifically needs GITHUB_TOKEN env var set to avoid"
Write-Host "rate limits. To set it from gh CLI auth:"
Write-Host "  [System.Environment]::SetEnvironmentVariable('GITHUB_TOKEN', (gh auth token), 'User')"
