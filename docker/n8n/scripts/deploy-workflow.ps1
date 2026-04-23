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

# --- Helpers ------------------------------------------------------------------

function Assert-ConnectionsIntegrity {
  # Pre-flight check #1: every key in `connections` and every edge target node
  # must exist in `nodes`. n8n keys connections by node display name; a rename
  # of a node without a matching rewrite of every reference orphans the node
  # from the pipeline silently (trigger fires, zero items flow). Fail fast.
  param(
    [Parameter(Mandatory)] $Workflow
  )
  if (-not $Workflow.nodes -or -not $Workflow.nodes.Count) {
    Write-Error "Workflow has no 'nodes' array to validate against."
    exit 1
  }
  $nodeNames = @{}
  foreach ($n in $Workflow.nodes) {
    if ($n.name) { $nodeNames[$n.name] = $true }
  }
  $conns = $Workflow.connections
  if (-not $conns) { return }
  $actualList = ($nodeNames.Keys | Sort-Object) -join "', '"
  foreach ($sourceProp in $conns.PSObject.Properties) {
    $sourceName = $sourceProp.Name
    if (-not $nodeNames.ContainsKey($sourceName)) {
      Write-Error ("Connections key '{0}' is not a node in this workflow.`nPresent node names: '{1}'." -f $sourceName, $actualList)
      exit 1
    }
    $outputs = $sourceProp.Value
    if ($null -eq $outputs -or -not $outputs.PSObject.Properties) {
      Write-Error "Unknown connections shape: connections['$sourceName'] is not an object. Stop and escalate."
      exit 1
    }
    foreach ($typeProp in $outputs.PSObject.Properties) {
      $outputType = $typeProp.Name
      $arr = $typeProp.Value
      if ($null -eq $arr) { continue }
      if ($arr -isnot [System.Collections.IEnumerable] -or $arr -is [string]) {
        Write-Error "Unknown connections shape: connections['$sourceName']['$outputType'] is not an array. Stop and escalate."
        exit 1
      }
      $outputIdx = 0
      foreach ($edgeList in $arr) {
        if ($null -eq $edgeList) { $outputIdx++; continue }
        if ($edgeList -isnot [System.Collections.IEnumerable] -or $edgeList -is [string]) {
          Write-Error "Unknown connections shape: connections['$sourceName']['$outputType'][$outputIdx] is not an array. Stop and escalate."
          exit 1
        }
        $edgeIdx = 0
        foreach ($edge in $edgeList) {
          if ($null -eq $edge -or -not $edge.PSObject.Properties['node']) {
            Write-Error "Unknown connections shape: edge at connections['$sourceName']['$outputType'][$outputIdx][$edgeIdx] is missing a 'node' property. Stop and escalate."
            exit 1
          }
          $target = $edge.node
          if (-not $nodeNames.ContainsKey($target)) {
            Write-Error ("Connection edge connections['{0}']['{1}'][{2}][{3}] points to node '{4}' which does not exist.`nPresent node names: '{5}'." -f $sourceName, $outputType, $outputIdx, $edgeIdx, $target, $actualList)
            exit 1
          }
          $edgeIdx++
        }
        $outputIdx++
      }
    }
  }
}

function Assert-CredentialReferences {
  # Pre-flight check #2: every credential ID referenced by a node must exist
  # in the n8n vault. When a credential is rotated (delete + recreate), its
  # UUID changes and the previous workflow references break silently. Fail
  # fast with the offending node + UUID so the operator can diagnose.
  param(
    [Parameter(Mandatory)] $Workflow,
    [Parameter(Mandatory)] [hashtable] $ValidIds
  )
  foreach ($node in $Workflow.nodes) {
    if (-not $node.credentials) { continue }
    if (-not $node.credentials.PSObject.Properties) { continue }
    foreach ($credProp in $node.credentials.PSObject.Properties) {
      $credType = $credProp.Name
      $credRef  = $credProp.Value
      if ($null -eq $credRef -or [string]::IsNullOrWhiteSpace($credRef.id)) { continue }
      if (-not $ValidIds.ContainsKey($credRef.id)) {
        Write-Error ("Node '{0}' references credential ID '{1}' (type '{2}') which is not present in the n8n credential vault.`nThis typically means the credential was rotated (delete + recreate in the UI) after this workflow was last deployed. Re-run the deploy so the placeholder resubstitutes, or verify the credential under Settings -> Credentials in the n8n UI." -f $node.name, $credRef.id, $credType)
        exit 1
      }
    }
  }
}

function Merge-WorkflowSettings {
  # PUT /workflows/:id replaces settings wholesale. Fetch-and-merge preserves
  # operator-configured keys (e.g., errorWorkflow, the Error Workflow wire-up
  # set in the UI and not shipped in the workflow JSON). Incoming values from
  # the JSON still win on conflict.
  #
  # GET returns more keys than PUT accepts — keys like binaryMode, timeSavedMode,
  # and availableInMCP are server-managed and cause 400 Bad Request if echoed
  # back on PUT. Filter existing settings through the known-writable allowlist
  # (confirmed against the live n8n public API, 2026-04-23); incoming JSON keys
  # pass through as-is so the script does not silently swallow operator-authored
  # keys it does not recognize.
  param(
    $Existing,
    $Incoming
  )
  $writableKeys = @(
    'executionOrder', 'errorWorkflow', 'callerPolicy', 'timezone',
    'executionTimeout', 'saveExecutionProgress', 'saveManualExecutions',
    'saveDataErrorExecution', 'saveDataSuccessExecution'
  )
  $merged = [ordered]@{}
  if ($Existing -and $Existing.PSObject.Properties) {
    foreach ($p in $Existing.PSObject.Properties) {
      if ($writableKeys -contains $p.Name) { $merged[$p.Name] = $p.Value }
    }
  }
  if ($Incoming) {
    if ($Incoming -is [hashtable] -or $Incoming -is [System.Collections.Specialized.OrderedDictionary]) {
      foreach ($k in $Incoming.Keys) { $merged[$k] = $Incoming[$k] }
    } elseif ($Incoming.PSObject.Properties) {
      foreach ($p in $Incoming.PSObject.Properties) { $merged[$p.Name] = $p.Value }
    }
  }
  if (-not $merged.Contains('executionOrder')) { $merged['executionOrder'] = 'v1' }
  return $merged
}

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

# --- Pre-flight validation ----------------------------------------------------
# Two blocker checks before any POST/PUT. Both surface the W1-era silent
# breakage classes: node-rename-without-connection-rewrite (W1 Lesson #1) and
# credential-rotation UUID drift.
Assert-ConnectionsIntegrity -Workflow $wfObj

$validCredIds = @{}
foreach ($c in $creds) {
  if ($c.id) { $validCredIds[$c.id] = $true }
}
Assert-CredentialReferences -Workflow $wfObj -ValidIds $validCredIds

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

# --- Fetch-then-merge settings on update --------------------------------------
# PUT /workflows/:id replaces the settings block wholesale. Without a merge,
# operator-configured keys like errorWorkflow (the Error Workflow wire-up,
# set manually in the UI per scripts/README.md) are silently dropped on every
# redeploy. Also carry forward the current active state so the success line
# reports the truth instead of hardcoded "(inactive)".
$existingSettings = $null
$activeState      = $false
if ($existingId) {
  try {
    $getResp = Invoke-RestMethod -Method Get -Uri "$BaseUrl/workflows/$existingId" -Headers $Headers
    $existingWf = if ($getResp.data) { $getResp.data } else { $getResp }
    $existingSettings = $existingWf.settings
    if ($null -ne $existingWf.active) { $activeState = [bool]$existingWf.active }
  } catch {
    Write-Error "Failed to fetch existing workflow $existingId for settings merge: $($_.Exception.Message)"
    exit 1
  }
}
$mergedSettings = Merge-WorkflowSettings -Existing $existingSettings -Incoming $wfObj.settings

# --- Build the request body ---------------------------------------------------
# n8n's POST /workflows rejects unknown top-level fields like `active` or `tags`.
# PATCH expects the same shape. Keep only name, nodes, connections, settings.
$payload = [ordered]@{
  name        = $wfObj.name
  nodes       = $wfObj.nodes
  connections = $wfObj.connections
  settings    = $mergedSettings
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

$editorUrl  = "http://localhost:15678/workflow/$resultId"
$stateLabel = if ($activeState) { 'active' } else { 'inactive' }
Write-Host ""
Write-Host "Workflow $action ($stateLabel):" -ForegroundColor Green
Write-Host "  Name: $wfName"
Write-Host "  ID:   $resultId"
Write-Host "  URL:  $editorUrl"
Write-Host ""
if ($activeState) {
  Write-Host "Active state preserved. Review changes in the UI if needed." -ForegroundColor Yellow
} else {
  Write-Host "Next: open the URL, review the imported workflow, click Activate." -ForegroundColor Yellow
}
