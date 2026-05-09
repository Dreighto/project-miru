# Launcher for the miru_memory MCP server (mcp-server-sqlite via uvx).
#
# Hardens cold-boot startup against the two failure modes seen in practice:
#   1. Bare `uvx` in .mcp.json fails on Windows when PATH propagation lags
#      after a system restart. Resolving uvx.exe via Get-Command at launch
#      time avoids that race.
#   2. A dirty shutdown can leave orphan -wal / -shm sidecar files next to
#      the SQLite DB. mcp-server-sqlite can hang or refuse to open the DB
#      when those exist. We checkpoint and remove them before launching.
#
# ASCII-only per the PowerShell 5.1 / cp1252 rule. No em dashes, no smart
# quotes. Do not add Unicode here even in comments.

$ErrorActionPreference = 'Stop'

$dbPath = 'D:\dev\miru\data\miru_memory.db'
$walPath = $dbPath + '-wal'
$shmPath = $dbPath + '-shm'

# Step 1: clean up any orphan WAL/SHM left by a dirty shutdown.
# A successful checkpoint folds the WAL back into the main DB. If sqlite3
# is not on PATH, fall through to a direct delete; on cold boot no other
# process holds the DB so this is safe.
if ((Test-Path -LiteralPath $walPath) -or (Test-Path -LiteralPath $shmPath)) {
    $sqlite = Get-Command sqlite3.exe -ErrorAction SilentlyContinue
    if ($sqlite) {
        try {
            & $sqlite.Source $dbPath 'PRAGMA wal_checkpoint(TRUNCATE);' | Out-Null
        } catch {
            # Checkpoint failed; we will fall through to direct removal.
        }
    }
    if (Test-Path -LiteralPath $walPath) { Remove-Item -LiteralPath $walPath -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $shmPath) { Remove-Item -LiteralPath $shmPath -Force -ErrorAction SilentlyContinue }
}

# Step 2: resolve uvx.exe explicitly. Get-Command throws if not found,
# which surfaces a clean error to the MCP client instead of a silent
# spawn failure.
$uvx = Get-Command uvx.exe -ErrorAction Stop

# Step 3: launch the SQLite MCP server. Stdio is inherited so the parent
# (Claude Code) handles the MCP protocol over this subprocess pipe.
& $uvx.Source mcp-server-sqlite --db-path $dbPath
exit $LASTEXITCODE
