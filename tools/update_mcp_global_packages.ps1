# update_mcp_global_packages.ps1
# Updates all globally installed npm MCP server packages.
#
# WHY a maintenance script instead of npx @latest:
#   npx @latest triggers a network version check + potential download on every
#   Claude Code session start. That spawns cmd.exe processes without
#   CREATE_NO_WINDOW, causing visible console flashes. The fix is to
#   pre-install packages globally and reference them by direct node path in
#   .mcp.json. This script is the update mechanism -- run it periodically
#   (monthly or when an MCP server has a breaking update).
#
# Packages updated here must match the paths in .mcp.json. Package paths are
#   C:\Users\<user>\AppData\Roaming\npm\node_modules\<package>\<main>
# Path structure is stable across minor/patch updates; .mcp.json only needs
# updating if a package reorganizes its dist layout (rare; check release notes).
#
# ASCII-only per the PowerShell 5.1 / cp1252 rule.
# Usage (no elevation required):
#   powershell -ExecutionPolicy Bypass -File tools\update_mcp_global_packages.ps1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$packages = @(
    '@perplexity-ai/mcp-server',
    '@modelcontextprotocol/server-sequential-thinking',
    '@playwright/mcp',
    '@cyanheads/git-mcp-server',
    '@mokei/mcp-sqlite',
    '@notionhq/notion-mcp-server',
    '@21st-dev/magic',
    '@a.ardeshir/youtube-mcp',
    'shadcn'
)

Write-Host "[update-mcp-packages] Updating $($packages.Count) globally installed MCP packages..."

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
    Write-Error "npm not found on PATH. Install Node.js (WinGet: OpenJS.NodeJS.22) and retry."
    exit 1
}

foreach ($pkg in $packages) {
    Write-Host "[update-mcp-packages] npm install -g $pkg"
    & $npm.Source install -g $pkg
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[update-mcp-packages] WARN: npm install -g $pkg exited $LASTEXITCODE -- continuing"
    }
}

Write-Host "[update-mcp-packages] Done. Verify paths in .mcp.json still point to the correct entry files."
Write-Host "[update-mcp-packages] Global node_modules: $(& $npm.Source root -g 2>$null)"
