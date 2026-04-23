#Requires -Version 5.1
<#
.SYNOPSIS
  Deploy an n8n workflow JSON template to the local n8n instance.

.DESCRIPTION
  Reads N8N_API_KEY from D:\dev\miru\.env, queries n8n's public API for the
  named credentials (notion-n8n, linear-n8n, pushover-n8n, github-n8n),
  substitutes {{NOTION_CRED_ID}} / {{LINEAR_CRED_ID}} / {{PUSHOVER_CRED_ID}} /
  {{GITHUB_CRED_ID}} placeholders in the workflow JSON, then POSTs to
  /api/v1/workflows (create) or PATCHes /api/v1/workflows/{id} if a workflow
  with the same name already exists (idempotent redeploy).

  Workflow is left INACTIVE. Operator activates via UI after reviewing.

  Never logs the API key. Never logs credential UUIDs. Prints only the new
  workflow ID and editor URL on success.

.PARAMETER WorkflowFile
  Filename (relative to docker/n8n/workflows/) of the workflow JSON template.

.EXAMPLE
  .\deploy-workflow.ps1 w1-planning-intake.json
  .\deploy-workflow.ps1 w1-error-handler.json
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$WorkflowFile
)

$ErrorActionPreference = 'Stop'

# --- Locate repo root + resolve paths -----------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$N8nDir     = Split-Path -Parent $ScriptDir
$RepoRoot   = Split-Path -Parent (Split-Path -Parent $N8nDir)
$EnvFile    = Join-Path $RepoRoot '.env'
$Workflows  = Join-Path $N8nDir 'workflows'
$TargetFile = Join-Path $Workflows $WorkflowFile

if (-not (Test-Path $TargetFile)) {
  Write-Error "Workflow file not found: $TargetFile"
  exit 1
}

# --- Read N8N_API_KEY from .env -----------------------------------------------
if (-not (Test-Path $EnvFile)) {
  Write-Error "Repo .env not found at $EnvFile"
  exit 1
}

$apiKey = $null
foreach ($line in Get-Content $EnvFile) {
  if ($line -match '^\s*N8N_API_KEY\s*=\s*(.+?)\s*$') {
    $apiKey = $matches[1].Trim('"').Trim("'")
    break
  }
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
  Write-Error "N8N_API_KEY not found in $EnvFile"
  exit 1
}

$BaseUrl = 'http://localhost:15678/api/v1'
$Headers = @{
  'X-N8N-API-KEY' = $apiKey
  'Accept'        = 'application/json'
}

# --- Fetch credentials (name -> id map) ---------------------------------------
# Note: n8n's /credentials endpoint returns the full list with names + IDs.
# We only use the IDs for placeholder substitution; they are never printed.
$credMap = @{}
try {
  $resp = Invoke-RestMethod -Method Get -Uri "$BaseUrl/credentials" -Headers $Headers
  $creds = if ($resp.data) { $resp.data } else { $resp }
  foreach ($c in $creds) {
    if ($c.name -and $c.id) { $credMap[$c.name] = $c.id }
  }
} catch {
  Write-Error "Failed to fetch credentials from n8n API: $($_.Exception.Message)"
  exit 1
}

$required = @('notion-n8n', 'linear-n8n', 'pushover-n8n')
foreach ($name in $required) {
  if (-not $credMap.ContainsKey($name)) {
    Write-Error "Required credential '$name' not found in n8n. Create it in the UI first."
    exit 1
  }
}

# --- Load workflow JSON + substitute placeholders -----------------------------
# Force UTF-8 read — PS5.1's Get-Content defaults to the system ANSI codepage,
# which mangles em-dashes and arrows in node names.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$raw = [System.IO.File]::ReadAllText($TargetFile, $utf8NoBom)

$substitutions = @{
  '{{NOTION_CRED_ID}}'   = $credMap['notion-n8n']
  '{{LINEAR_CRED_ID}}'   = $credMap['linear-n8n']
  '{{PUSHOVER_CRED_ID}}' = $credMap['pushover-n8n']
}
if ($credMap.ContainsKey('github-n8n')) {
  $substitutions['{{GITHUB_CRED_ID}}'] = $credMap['github-n8n']
}

foreach ($placeholder in $substitutions.Keys) {
  $raw = $raw.Replace($placeholder, $substitutions[$placeholder])
}

# Sanity: any unsubstituted placeholder would break the import.
if ($raw -match '\{\{[A-Z_]+_CRED_ID\}\}') {
  $stillThere = $matches[0]
  Write-Error "Unsubstituted placeholder remains in workflow JSON: $stillThere"
  exit 1
}

# Parse to validate JSON and extract workflow name.
try {
  $wfObj = $raw | ConvertFrom-Json
} catch {
  Write-Error "Workflow JSON is invalid after substitution: $($_.Exception.Message)"
  exit 1
}

$wfName = $wfObj.name
if ([string]::IsNullOrWhiteSpace($wfName)) {
  Write-Error "Workflow JSON is missing a top-level 'name' field."
  exit 1
}

# --- Find existing workflow by name (for idempotent redeploy) -----------------
$existingId = $null
try {
  $list = Invoke-RestMethod -Method Get -Uri "$BaseUrl/workflows" -Headers $Headers
  $items = if ($list.data) { $list.data } else { $list }
  foreach ($wf in $items) {
    if ($wf.name -eq $wfName) {
      $existingId = $wf.id
      break
    }
  }
} catch {
  Write-Error "Failed to list existing workflows: $($_.Exception.Message)"
  exit 1
}

# --- Build the request body ---------------------------------------------------
# n8n's POST /workflows rejects unknown top-level fields like `active` or `tags`.
# PATCH expects the same shape. Keep only name, nodes, connections, settings.
$payload = [ordered]@{
  name        = $wfObj.name
  nodes       = $wfObj.nodes
  connections = $wfObj.connections
  settings    = if ($wfObj.settings) { $wfObj.settings } else { @{ executionOrder = 'v1' } }
}
$bodyJson  = $payload | ConvertTo-Json -Depth 50 -Compress
# Encode body as UTF-8 bytes so PS5.1 does not transcode to ISO-8859-1 on the wire.
$bodyBytes = $utf8NoBom.GetBytes($bodyJson)

$createHeaders = $Headers.Clone()
$createHeaders['Content-Type'] = 'application/json; charset=utf-8'

# --- Create or update ---------------------------------------------------------
$resultId = $null
$action = $null
try {
  if ($existingId) {
    $action = 'updated'
    $resp = Invoke-RestMethod -Method Put -Uri "$BaseUrl/workflows/$existingId" `
      -Headers $createHeaders -Body $bodyBytes
    $resultId = if ($resp.id) { $resp.id } else { $existingId }
  } else {
    $action = 'created'
    $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/workflows" `
      -Headers $createHeaders -Body $bodyBytes
    $resultId = $resp.id
  }
} catch {
  $errBody = ''
  try {
    if ($_.Exception.Response) {
      $stream = $_.Exception.Response.GetResponseStream()
      $reader = New-Object System.IO.StreamReader($stream)
      $errBody = $reader.ReadToEnd()
    }
  } catch {}
  Write-Error "n8n API error ($action): $($_.Exception.Message)`n$errBody"
  exit 1
}

if (-not $resultId) {
  Write-Error "n8n API returned no workflow ID."
  exit 1
}

$editorUrl = "http://localhost:15678/workflow/$resultId"
Write-Host ""
Write-Host "Workflow $action (inactive):" -ForegroundColor Green
Write-Host "  Name: $wfName"
Write-Host "  ID:   $resultId"
Write-Host "  URL:  $editorUrl"
Write-Host ""
Write-Host "Next: open the URL, review the imported workflow, click Activate." -ForegroundColor Yellow
