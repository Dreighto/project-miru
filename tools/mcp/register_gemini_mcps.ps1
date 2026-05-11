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
# Default scope is `project` (writes to <repo-root>/.gemini/settings.json).
# The script auto-locates the repo root from $PSScriptRoot (tools/mcp/ is
# two levels below the repo root) and cd's there before any `gemini mcp
# add -s project` call. Invoking from outside the repo root no longer
# mis-registers into the wrong .gemini/settings.json.
#
# ┌─────────────────────────────────────────────────────────────────────┐
# │ SECURITY — PLAINTEXT SECRETS WARNING (CR R4 finding on PR #190)     │
# │                                                                     │
# │ `gemini mcp add ... -Env @{ KEY=$value }` persists $value LITERALLY │
# │ into the target settings.json. The GitHub token used here ends up   │
# │ as plaintext in either:                                             │
# │   - project scope: <repo>/.gemini/settings.json  (DEFAULT)          │
# │   - user scope:    ~/.gemini/settings.json       (-UserScope)       │
# │                                                                     │
# │ DO NOT commit the post-registration .gemini/settings.json. The      │
# │ tracked version in this repo contains placeholders like             │
# │ "${env:GITHUB_TOKEN}" only — but gemini will rewrite it with the    │
# │ resolved literal value after this script runs.                      │
# │                                                                     │
# │ User scope (-UserScope) keeps the token OUT of the repo working     │
# │ tree (lower commit risk) but makes it readable to gemini from any   │
# │ project on this machine — choose based on your threat model.        │
# │                                                                     │
# │ Tracked as architectural cleanup: see LOS-15-adjacent ticket for    │
# │ "untrack .gemini/settings.json + add template + pre-commit secret   │
# │ scan" follow-up.                                                    │
# └─────────────────────────────────────────────────────────────────────┘

[CmdletBinding()]
param(
    # When set, registers with `-s user` instead of `-s project`. Tokens
    # end up in ~/.gemini/settings.json (outside the repo working tree)
    # rather than .gemini/settings.json (inside it). Still plaintext —
    # but no risk of `git add .gemini/` slipping a secret in.
    [switch]$UserScope
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command gemini -ErrorAction SilentlyContinue)) {
    Write-Error "gemini CLI not found on PATH. Install via: npm install -g @google/gemini-cli"
    exit 1
}

# Resolve effective scope for downstream `gemini mcp add` calls.
$geminiScope = if ($UserScope) { 'user' } else { 'project' }
if ($UserScope) {
    Write-Host ""
    Write-Host "Scope: USER (writes to ~/.gemini/settings.json, outside the repo)" -ForegroundColor Yellow
    Write-Host "  - Token stays out of the repo working tree (no accidental git add)" -ForegroundColor Gray
    Write-Host "  - But: token is readable by gemini from ANY project on this machine" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "Scope: PROJECT (writes to <repo>/.gemini/settings.json)" -ForegroundColor Yellow
    Write-Host "  - DO NOT commit the post-registration .gemini/settings.json — it contains literal token values" -ForegroundColor Gray
    Write-Host "  - Pass -UserScope to write to ~/.gemini/settings.json instead (outside the repo)" -ForegroundColor Gray
}

# CR R3 MAJOR finding on PR #190: `gemini mcp add -s project` writes to
# CWD/.gemini/settings.json. Invoked from anywhere other than the repo
# root, it lands in a stray .gemini/settings.json in the wrong directory.
# Lock CWD to the repo root for the duration of the script — derived
# from $PSScriptRoot since the script lives at <repo>/tools/mcp/.
# (Still needed under -UserScope so that any relative paths inside the
# registered MCP commands resolve consistently.)
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git') -ErrorAction SilentlyContinue)) {
    Write-Error "Resolved repo root '$repoRoot' has no .git — script location moved? Refusing to register from an unverifiable root."
    exit 1
}
$originalLocation = Get-Location
Set-Location -LiteralPath $repoRoot
Write-Host "Locked CWD to repo root: $repoRoot" -ForegroundColor Gray
try {

# Track partial failures so we can exit non-zero at the end. CR R1 finding
# on PR #190: previously the script logged FAIL but still exited 0, which
# hid partial registration failures in automation.
$script:FailedCount = 0

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
    # gemini's own flags. Scope is whatever the top-level $geminiScope
    # resolved to ('project' by default, 'user' under -UserScope).
    $cliArgs = @('mcp', 'add', $Name, $Command, '-s', $geminiScope)
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
        $script:FailedCount++
    } else {
        Write-Host "    OK" -ForegroundColor Green
    }
}

# CR R1 finding on PR #190: PoshMCP config path was hardcoded as
# D:\dev\miru\... — broke on any clone or machine outside that exact path.
# Derive from $PSScriptRoot so any clone location works.
$PoshMcpConfigPath = Join-Path $PSScriptRoot 'posh-mcp-config.json'
if (-not (Test-Path $PoshMcpConfigPath)) {
    Write-Error "PoshMCP config not found at $PoshMcpConfigPath. Expected next to this script."
    exit 1
}

# CR R1 finding on PR #190: GITHUB_TOKEN was passed as the literal string
# '${env:GITHUB_TOKEN}' inside single quotes, which PowerShell does NOT
# expand. That meant shadcn-svelte saw the literal placeholder instead of
# the real token value. Expand it explicitly here so the hashtable carries
# the actual token (or empty if unset, which downgrades shadcn to
# rate-limited mode rather than breaking it).
$GithubToken = if ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { '' }
if (-not $GithubToken) {
    Write-Host "Warning: GITHUB_TOKEN env var not set. shadcn-svelte will be rate-limited (60 req/hr)." -ForegroundColor Yellow
    Write-Host "  Set it via: [System.Environment]::SetEnvironmentVariable('GITHUB_TOKEN', (gh auth token), 'User')" -ForegroundColor Gray
    Write-Host "  Then re-run this script. (We do NOT prompt interactively — see header comment for why.)" -ForegroundColor Gray
} else {
    # CR R4 finding on PR #190: surface the plaintext-write at the point
    # of action, not just in the header. The operator should see this
    # the moment a real token is about to land in a settings.json.
    Write-Host ""
    Write-Host ("Note: GITHUB_TOKEN will be written PLAINTEXT into the {0}-scope .gemini/settings.json." -f $geminiScope) -ForegroundColor Yellow
    Write-Host "      See the security warning in this script's header. -UserScope flips to user scope (outside repo)." -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== Registering MCP servers with Gemini CLI ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "--- Infrastructure (CC + Gemini parity) ---"
Add-McpServer -Name 'docker'          -Command 'uvx'     -ServerArgs @('mcp-server-docker')
Add-McpServer -Name 'scheduled-tasks' -Command 'pwsh'    -ServerArgs @(
    '-NoProfile', '-NoLogo', '-NonInteractive', '-WindowStyle', 'Hidden',
    '-Command', ("Import-Module PoshMCP; Start-PoshMcp -ConfigPath '{0}'" -f $PoshMcpConfigPath)
)

Write-Host ""
Write-Host "--- Frontend (Gemini only — SvelteKit lane) ---"
Add-McpServer -Name 'svelte'        -Command 'npx.cmd' -ServerArgs @('-y', '@sveltejs/mcp')
Add-McpServer -Name 'shadcn-svelte' -Command 'npx.cmd' `
    -ServerArgs @('-y', '@jpisnice/shadcn-ui-mcp-server', '--framework', 'svelte') `
    -Env @{ 'GITHUB_PERSONAL_ACCESS_TOKEN' = $GithubToken }
Add-McpServer -Name 'lucide-icons'  -Command 'npx.cmd' -ServerArgs @('-y', 'lucide-icons-mcp')
Add-McpServer -Name 'a11y-scanner'  -Command 'npx.cmd' -ServerArgs @('-y', 'mcp-accessibility-scanner')
Add-McpServer -Name 'vitest'        -Command 'npx.cmd' -ServerArgs @('-y', '@djankies/vitest-mcp')

Write-Host ""
if ($script:FailedCount -gt 0) {
    Write-Host ("=== Completed with {0} failure(s) ===" -f $script:FailedCount) -ForegroundColor Red
    Write-Host "Inspect output above for FAIL lines. Common causes:" -ForegroundColor Yellow
    Write-Host "  - gemini CLI not on PATH"
    Write-Host "  - upstream npm package temporarily unavailable (npx -y fetch failed)"
    Write-Host "  - PoshMCP requires PowerShell 7 (pwsh) on PATH"
    exit 1
}
Write-Host "=== Done — all registrations succeeded ===" -ForegroundColor Cyan
# CR R2 (MINOR line 121): relaunch instructions previously hardcoded
# 'cd D:\dev\miru && gemini' which is workstation-specific. $repoRoot
# was already computed above so the instructions match the operator's
# actual clone location.
Write-Host "Next:"
Write-Host "  1. Close any running Gemini CLI session"
Write-Host ("  2. Relaunch from this repo: cd '{0}'; gemini" -f $repoRoot)
Write-Host "  3. In the Gemini prompt, type: /mcp"
Write-Host "  4. All 7 new entries (docker, scheduled-tasks, svelte, shadcn-svelte,"
Write-Host "     lucide-icons, a11y-scanner, vitest) should show as Ready."
Write-Host ""
Write-Host "shadcn-svelte specifically needs GITHUB_TOKEN env var set to avoid"
Write-Host "rate limits. To set it from gh CLI auth:"
Write-Host "  [System.Environment]::SetEnvironmentVariable('GITHUB_TOKEN', (gh auth token), 'User')"
} finally {
    # CR R3 MAJOR (PR #190): always restore the operator's original CWD
    # so the script doesn't leave them stranded at $repoRoot when they
    # invoked it from elsewhere. Runs on success, error, and ctrl-C.
    Set-Location -LiteralPath $originalLocation
}
