Set-StrictMode -Version Latest

function Get-OpMiruPaths {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptDirectory
    )

    $repoRoot = Split-Path -Parent $ScriptDirectory
    $logDir = Join-Path $repoRoot "data\startup-logs"

    [pscustomobject]@{
        RepoRoot              = $repoRoot
        ScriptDirectory       = $ScriptDirectory
        LogDirectory          = $logDir
        DockerComposeFile     = Join-Path $repoRoot "docker-compose.yml"
        DockerConfigDirectory = Join-Path $repoRoot ".docker-config"
        MiruAiScript          = Join-Path $repoRoot "miru_ai\server.py"
        MiruAiHealthUrlLocal  = "http://127.0.0.1:18765/api/health"
        MiruAiRootUrlLocal    = "http://127.0.0.1:18765/"
        DashboardUrlLocal     = "http://127.0.0.1:8080/"
        MiruAiPort            = 18765
        DashboardPort         = 8080
    }
}

function Import-OpMiruDotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [switch]$OverrideExisting
    )

    $envPath = Join-Path $RepoRoot ".env"
    $loadedKeys = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path $envPath)) {
        return [pscustomobject]@{
            EnvPath     = $envPath
            Exists      = $false
            LoadedKeys  = @()
            Summary     = "Local .env file was not found."
        }
    }

    foreach ($rawLine in Get-Content -Path $envPath -ErrorAction Stop) {
        $line = [string]$rawLine
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $line = $line.Trim()
        if ($line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }

        $parts = $line -split "=", 2
        if ($parts.Count -lt 2) {
            continue
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        $currentValue = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($OverrideExisting -or [string]::IsNullOrWhiteSpace($currentValue)) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
            $loadedKeys.Add($name)
        }
    }

    return [pscustomobject]@{
        EnvPath    = $envPath
        Exists     = $true
        LoadedKeys = @($loadedKeys)
        Summary    = "Loaded local .env values into the current process."
    }
}

function Get-OpMiruPushoverStatus {
    $enabledText = [string]([Environment]::GetEnvironmentVariable("PUSHOVER_ENABLED", "Process"))
    $enabled = $enabledText -match '^(?i:true|1|yes|on)$'
    $missingKeys = New-Object System.Collections.Generic.List[string]
    foreach ($key in @("PUSHOVER_USER_KEY", "PUSHOVER_APP_TOKEN")) {
        $value = [string]([Environment]::GetEnvironmentVariable($key, "Process"))
        if ([string]::IsNullOrWhiteSpace($value)) {
            $missingKeys.Add($key)
        }
    }

    $configured = $enabled -and $missingKeys.Count -eq 0
    $summary = if (-not $enabled) {
        "Pushover notifications are disabled."
    }
    elseif ($configured) {
        "Pushover credentials are loaded and notifications are enabled."
    }
    else {
        "Pushover is enabled but missing required keys: $($missingKeys -join ', ')."
    }

    return [pscustomobject]@{
        Enabled             = $enabled
        Configured          = $configured
        MissingRequiredKeys = @($missingKeys)
        DefaultPriority     = [string]([Environment]::GetEnvironmentVariable("PUSHOVER_DEFAULT_PRIORITY", "Process"))
        Summary             = $summary
    }
}

function Get-OpMiruLanIpv4Address {
    $preferred = New-Object System.Collections.Generic.List[string]
    $fallback = New-Object System.Collections.Generic.List[string]

    foreach ($iface in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($iface.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) {
            continue
        }

        if ($iface.NetworkInterfaceType -in @(
                [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback,
                [System.Net.NetworkInformation.NetworkInterfaceType]::Tunnel
            )) {
            continue
        }

        foreach ($addressInfo in $iface.GetIPProperties().UnicastAddresses) {
            if ($addressInfo.Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
                continue
            }

            $ip = $addressInfo.Address.ToString()
            if ($ip.StartsWith("169.254.")) {
                continue
            }

            if (
                $ip.StartsWith("10.") -or
                $ip.StartsWith("192.168.") -or
                $ip -match "^172\.(1[6-9]|2\d|3[0-1])\."
            ) {
                $preferred.Add($ip)
            }
            else {
                $fallback.Add($ip)
            }
        }
    }

    if ($preferred.Count -gt 0) {
        return $preferred[0]
    }

    if ($fallback.Count -gt 0) {
        return $fallback[0]
    }

    return $null
}

function Test-OpMiruHttp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSeconds = 5,
        [string]$MustContain = ""
    )

    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Proxy = $null
        $req.AllowAutoRedirect = $true
        $req.Method = "GET"
        $req.Timeout = [Math]::Max(1000, $TimeoutSeconds * 1000)

        $resp = $req.GetResponse()
        try {
            $code = [int]$resp.StatusCode
            $stream = $resp.GetResponseStream()
            $content = ""
            $reader = $null
            try {
                $reader = New-Object System.IO.StreamReader($stream)
                $content = $reader.ReadToEnd()
            }
            finally {
                if ($null -ne $reader) {
                    $reader.Dispose()
                }
            }
            $contentMatches = [string]::IsNullOrWhiteSpace($MustContain) -or $content.Contains($MustContain)
            $ok = ($code -ge 200 -and $code -lt 400 -and $contentMatches)

            [pscustomobject]@{
                Ok            = $ok
                StatusCode    = $code
                ContentLength = $content.Length
                Content       = $content
                Error         = $null
                Url           = $Url
            }
        }
        finally {
            $resp.Close()
        }
    }
    catch {
        [pscustomobject]@{
            Ok            = $false
            StatusCode    = $null
            ContentLength = 0
            Content       = ""
            Error         = $_.Exception.Message
            Url           = $Url
        }
    }
}

function Wait-OpMiruHttp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSeconds = 120,
        [int]$RetryDelaySeconds = 3,
        [string]$MustContain = ""
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        # Per-attempt HTTP timeout must not use RetryDelaySeconds alone: sleep between retries
        # is unrelated to cold Flask import + first full GET / (worktree dashboard has been
        # observed at ~38s to listen and ~35s+ for the first HTML response; sub-60s caused false failures.)
        # JSON /api/* probes stay shorter so a dead stack does not burn 3 minutes per attempt.
        $isLikelyTinyResponse = $Url -match '/api/'
        $httpTimeout = if ($isLikelyTinyResponse) {
            [Math]::Min(90, [Math]::Max(20, $RetryDelaySeconds * 3))
        }
        else {
            [Math]::Min(300, [Math]::Max(180, $RetryDelaySeconds * 6))
        }
        $result = Test-OpMiruHttp -Url $Url -TimeoutSeconds $httpTimeout -MustContain $MustContain
        if ($result.Ok) {
            return $result
        }

        Start-Sleep -Seconds $RetryDelaySeconds
    } while ((Get-Date) -lt $deadline)

    return $result
}

function Get-OpMiruListeningEntry {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $entries = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($entries.Count -gt 0) {
        return [pscustomobject]@{
            LocalAddress = [string]$entries[0].LocalAddress
            Port         = $Port
            Pid          = [int]$entries[0].OwningProcess
        }
    }

    return $null
}

function Initialize-OpMiruDockerEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DockerConfigDirectory
    )

    New-Item -ItemType Directory -Force -Path $DockerConfigDirectory | Out-Null
    $env:DOCKER_CONFIG = $DockerConfigDirectory
    return $DockerConfigDirectory
}

function Invoke-OpMiruDockerCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$DockerConfigDirectory,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    Initialize-OpMiruDockerEnvironment -DockerConfigDirectory $DockerConfigDirectory | Out-Null

    $previousLocation = Get-Location
    $hadNativeErrorPreference = Test-Path variable:PSNativeCommandUseErrorActionPreference
    if ($hadNativeErrorPreference) {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
    }
    try {
        if ($hadNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        Set-Location $WorkingDirectory
        $rawOutput = @(& docker @Arguments 2>&1)
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        $output = @(
            $rawOutput | ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    $_.ToString()
                }
                else {
                    [string]$_
                }
            }
        )
    }
    catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }
    finally {
        if ($hadNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
        }
        Set-Location $previousLocation
    }

    [pscustomobject]@{
        Success  = ($exitCode -eq 0)
        ExitCode = $exitCode
        Output   = @($output)
        Command  = "docker $($Arguments -join ' ')"
    }
}

function ConvertFrom-OpMiruDockerJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Lines
    )

    $jsonText = (@($Lines) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        return @()
    }

    try {
        $parsed = $jsonText | ConvertFrom-Json -ErrorAction Stop
        if ($parsed -is [System.Array]) {
            return $parsed
        }
        return @($parsed)
    }
    catch {
        $records = [System.Collections.Generic.List[object]]::new()
        $nonEmptyLines = @($Lines | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        foreach ($line in $nonEmptyLines) {
            try {
                $records.Add(($line | ConvertFrom-Json -ErrorAction Stop))
            }
            catch {
                throw "Unable to parse Docker JSON output. $($_.Exception.Message)"
            }
        }
        return @($records)
    }
}

function Get-OpMiruDockerComposeServices {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$ComposeFile,
        [Parameter(Mandatory = $true)]
        [string]$DockerConfigDirectory,
        [string]$ProjectName = "op-miru-worktree"
    )

    $composePsResult = Invoke-OpMiruDockerCli `
        -Arguments @("compose", "-p", $ProjectName, "-f", $ComposeFile, "ps", "--all", "--format", "json") `
        -DockerConfigDirectory $DockerConfigDirectory `
        -WorkingDirectory $RepoRoot
    if (-not $composePsResult.Success) {
        throw "Unable to inspect docker services for project '$ProjectName'. $((@($composePsResult.Output) -join [Environment]::NewLine).Trim())"
    }

    $outputLines = @($composePsResult.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($outputLines.Count -eq 0) {
        return @()
    }
    return @(ConvertFrom-OpMiruDockerJson -Lines $outputLines)
}
