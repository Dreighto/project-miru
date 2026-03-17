# NOTE (worktree authority): this is a Miru AI-only convenience launcher.
# Canonical full worktree runtime startup is:
#   windows/start_op_miru_worktree.ps1  (dashboard 18080 + Miru AI/Dev 18765)
# This script defaults to port 8765 and is non-canonical for worktree full-stack startup.

param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8765,
    [string]$LogPath = ""
)

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
. (Join-Path $ProjectRoot "windows\op_miru_common.ps1")

function Test-IsPrivateLanIp {
    param([string]$IpAddress)

    if (-not $IpAddress) {
        return $false
    }
    if ($IpAddress -like '10.*' -or $IpAddress -like '192.168.*') {
        return $true
    }
    if ($IpAddress -match '^172\.(1[6-9]|2[0-9]|3[0-1])\.') {
        return $true
    }
    return $false
}

function Test-IsVirtualAdapterName {
    param([string]$Name)

    if (-not $Name) {
        return $false
    }
    return $Name -match 'Hyper-V|vEthernet|WSL|Docker|VMware|VirtualBox|Loopback|Tailscale|ZeroTier|WireGuard|VPN|TAP|TUN|Tunnel|Virtual|Bluetooth'
}

function Test-IsTailscaleIp {
    param([string]$IpAddress)

    if (-not $IpAddress) {
        return $false
    }
    return $IpAddress -match '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.'
}

function Get-LanIp {
    try {
        $adapters = Get-NetAdapter -ErrorAction Stop | Where-Object { $_.Status -eq 'Up' }
        $adapters = $adapters | Where-Object {
            -not (Test-IsVirtualAdapterName $_.Name) -and
            -not (Test-IsVirtualAdapterName $_.InterfaceDescription)
        }
        $preferredAdapters = $adapters | Where-Object {
            $_.InterfaceDescription -match 'Wi-Fi|Wireless|Ethernet' -or
            $_.Name -match 'Wi-Fi|Wireless|Ethernet'
        }
        if (-not $preferredAdapters) {
            $preferredAdapters = $adapters
        }

        if ($preferredAdapters) {
            $adapterIndexes = $preferredAdapters | Select-Object -ExpandProperty ifIndex
            $candidates = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $adapterIndexes -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -notlike '127.*' -and
                    $_.IPAddress -notlike '169.254.*' -and
                    $_.PrefixOrigin -ne 'WellKnown'
                } |
                Select-Object -ExpandProperty IPAddress
            $preferred = $candidates | Where-Object { Test-IsPrivateLanIp $_ } | Select-Object -First 1
            if ($preferred) {
                return $preferred
            }
            $fallback = $candidates | Select-Object -First 1
            if ($fallback) {
                return $fallback
            }
        }
    } catch {
    }

    try {
        $hostname = [System.Net.Dns]::GetHostName()
        $candidates = [System.Net.Dns]::GetHostAddresses($hostname) |
            Where-Object {
                $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
                $_.IPAddressToString -notlike '127.*' -and
                $_.IPAddressToString -notlike '169.254.*'
            } |
            Select-Object -ExpandProperty IPAddressToString
        $preferred = $candidates | Where-Object { Test-IsPrivateLanIp $_ } | Select-Object -First 1
        if ($preferred) {
            return $preferred
        }
        $fallback = $candidates | Select-Object -First 1
        if ($fallback) {
            return $fallback
        }
    } catch {
    }

    return $null
}

function Get-TailscaleIp {
    try {
        $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                ($_.InterfaceAlias -match 'Tailscale' -or $_.InterfaceDescription -match 'Tailscale') -and
                (Test-IsTailscaleIp $_.IPAddress)
            } |
            Select-Object -ExpandProperty IPAddress
        $preferred = $candidates | Select-Object -First 1
        if ($preferred) {
            return $preferred
        }
    } catch {
    }
    return $null
}

function Write-LaunchLine {
    param(
        [string]$Message,
        [string]$Color = ""
    )

    if ($Color) {
        Write-Host $Message -ForegroundColor $Color
    } else {
        Write-Host $Message
    }
    if ($LogPath) {
        try {
            Add-Content -Path $LogPath -Value $Message
        } catch {
        }
    }
}

$lanIp = Get-LanIp
$tailscaleIp = Get-TailscaleIp
$localBase = "http://localhost:$Port"
$localMiruUrl = "$localBase/"
$localDevUrl = "$localBase/dev"

Write-LaunchLine "Miru Dev Launcher" "Cyan"
Write-LaunchLine "Project root: $ProjectRoot"
$envLoad = Import-OpMiruDotEnv -RepoRoot $ProjectRoot
$pushoverStatus = Get-OpMiruPushoverStatus
if ($envLoad.Exists) {
    Write-LaunchLine "Loaded local .env from $($envLoad.EnvPath)."
} else {
    Write-LaunchLine "Local .env not found at $($envLoad.EnvPath)." "Yellow"
}
if ($pushoverStatus.Configured) {
    Write-LaunchLine $pushoverStatus.Summary "Green"
} elseif ($pushoverStatus.Enabled) {
    Write-LaunchLine $pushoverStatus.Summary "Yellow"
} else {
    Write-LaunchLine $pushoverStatus.Summary "Yellow"
}
Write-LaunchLine "Miru AI URL: $localMiruUrl"
Write-LaunchLine "Dev Monitor URL: $localDevUrl"
if ($lanIp) {
    Write-LaunchLine "LAN Miru AI URL: http://$lanIp`:$Port/"
    Write-LaunchLine "LAN Dev Monitor URL: http://$lanIp`:$Port/dev"
} else {
    Write-LaunchLine "LAN URL: unavailable" "Yellow"
}
if ($tailscaleIp) {
    Write-LaunchLine "Tailscale Miru AI URL: http://$tailscaleIp`:$Port/"
    Write-LaunchLine "Tailscale Dev Monitor URL: http://$tailscaleIp`:$Port/dev"
}
Write-LaunchLine "Press CTRL+C to stop the server."
Write-LaunchLine ""

python tools\miru_ai_server.py --host $BindHost --port $Port
