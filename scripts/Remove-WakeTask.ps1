param(
    [switch]$NoElevation,
    [string]$ResultPath = ''
)
$ErrorActionPreference = 'Stop'

function Write-RemovalResult([string]$Status, [string]$Message) {
    if ([string]::IsNullOrWhiteSpace($ResultPath)) { return }
    @{
        status = $Status
        message = $Message
    } | ConvertTo-Json | Set-Content -LiteralPath $ResultPath -Encoding UTF8
}

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
if (-not $NoElevation -and -not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    try {
        $arguments = @(
            '-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath),
            '-NoElevation', '-ResultPath', ('"{0}"' -f $ResultPath)
        )
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        if ($null -eq $elevated.ExitCode) { exit 1 }
        exit ([int]$elevated.ExitCode)
    }
    catch {
        Write-RemovalResult 'error' $_.Exception.Message
        exit 1
    }
}

try {
    $taskNames = @('BACoffee-Cafe', ('BACof' + 'fe-Cafe'))
    foreach ($taskName in $taskNames) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    $remaining = @($taskNames | Where-Object {
        Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    })
    if ($remaining.Count -gt 0) {
        throw ('Scheduled task still exists: ' + ($remaining -join ', '))
    }
    $appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $statusPath = Join-Path $appDir 'data\schedule_status.json'
    @{
        installed = $false
        removed_at = (Get-Date).ToString('o')
        task_name = 'BACoffee-Cafe'
    } | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
    Write-RemovalResult 'success' 'The BACoffee wake schedule has been removed.'
    Write-Host 'BACoffee wake schedule removed.'
    exit 0
}
catch {
    Write-RemovalResult 'error' $_.Exception.Message
    exit 1
}
