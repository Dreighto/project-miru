$ErrorActionPreference = 'Stop'
$envPath = 'D:\dev\tcg-watcher-worktree\.env'
$line = Get-Content -LiteralPath $envPath |
    Where-Object { $_ -match '^\s*PERPLEXITY_API_KEY\s*=' } |
    Select-Object -First 1
if (-not $line) {
    Write-Error "PERPLEXITY_API_KEY missing in $envPath"
    exit 1
}
$value = ($line -split '=', 2)[1].Trim()
if ([string]::IsNullOrWhiteSpace($value)) {
    Write-Error "PERPLEXITY_API_KEY empty in $envPath"
    exit 1
}
$env:PERPLEXITY_API_KEY = $value
& npx.cmd --yes perplexity-mcp
