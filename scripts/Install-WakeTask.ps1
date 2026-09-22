param([switch]$NoElevation)
$ErrorActionPreference = 'Stop'

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
if (-not $NoElevation -and -not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath))
    $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $elevated.ExitCode
}

$taskName = 'BACoffee-Cafe'
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$settingsPath = Join-Path $appDir 'data\settings.json'
$statusPath = Join-Path $appDir 'data\schedule_status.json'
$culture = [Globalization.CultureInfo]::InvariantCulture
$dateStyles = [Globalization.DateTimeStyles]::AssumeLocal

function Write-ScheduleStatus([hashtable]$status) {
    $status | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}

function Convert-ToLocalDateTime([object]$value) {
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
        return $null
    }
    $parsed = [datetime]::MinValue
    $ok = [datetime]::TryParse([string]$value, $culture, $dateStyles, [ref]$parsed)
    if ($ok) { return $parsed }
    return $null
}

function Get-ScheduleWindow([datetime]$value) {
    if ($value.Hour -lt 4) {
        $start = $value.Date.AddDays(-1).AddHours(16)
        $end = $value.Date.AddHours(4)
        $kind = 'night'
    } elseif ($value.Hour -lt 16) {
        $start = $value.Date.AddHours(4)
        $end = $value.Date.AddHours(16)
        $kind = 'day'
    } else {
        $start = $value.Date.AddHours(16)
        $end = $value.Date.AddDays(1).AddHours(4)
        $kind = 'night'
    }
    return [pscustomobject]@{ Start = $start; End = $end; Kind = $kind }
}

function Get-TimeValue([string]$timeText) {
    return [datetime]::ParseExact($timeText, 'HH:mm', $culture).TimeOfDay
}

function Get-NextOccurrence([datetime]$now, [string]$timeText) {
    $candidate = $now.Date.Add((Get-TimeValue $timeText))
    while ($candidate -le $now) { $candidate = $candidate.AddDays(1) }
    return $candidate
}

function Get-NextAffectedOccurrence($period, [datetime]$now, [string]$timeText) {
    $time = Get-TimeValue $timeText
    $hour = [int]$time.TotalHours
    if ($hour -lt 4) {
        $candidate = $period.End.Date.AddDays(1).Add($time)
    } else {
        $candidate = $period.Start.Date.AddDays(1).Add($time)
    }
    while ($candidate -le $now) { $candidate = $candidate.AddDays(1) }
    return $candidate
}

function Test-TimeInKind([string]$timeText, [string]$kind) {
    $hour = [int]$timeText.Substring(0, 2)
    if ($kind -eq 'day') { return $hour -ge 4 -and $hour -lt 16 }
    return $hour -ge 16 -or $hour -lt 4
}

function New-DailyPlan([string]$timeText, [datetime]$firstAt) {
    $trigger = New-ScheduledTaskTrigger -Daily -At $firstAt
    try { $trigger.StartBoundary = $firstAt.ToString('s', $culture) } catch { }
    return [pscustomobject]@{
        Trigger = $trigger
        Record = [pscustomobject]@{
            trigger_type = 'Daily'
            start_boundary = $firstAt.ToString('yyyy-MM-ddTHH:mm:ss', $culture)
            time = $timeText
            temporary = $false
        }
    }
}

function New-OncePlan([datetime]$runAt) {
    $trigger = New-ScheduledTaskTrigger -Once -At $runAt
    return [pscustomobject]@{
        Trigger = $trigger
        Record = [pscustomobject]@{
            trigger_type = 'Once'
            start_boundary = $runAt.ToString('yyyy-MM-ddTHH:mm:ss', $culture)
            time = $runAt.ToString('HH:mm', $culture)
            temporary = $true
        }
    }
}

if (-not (Test-Path -LiteralPath $settingsPath)) { throw 'Start BACoffee and save settings first.' }
$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$python = [string]$settings.baas_python
$script = Join-Path $appDir 'BACoffee.py'
$action = New-ScheduledTaskAction -Execute $python -Argument ('-X utf8 "{0}" --worker --scheduled' -f $script) -WorkingDirectory $appDir

$now = Get-Date
$period = Get-ScheduleWindow $now
$override = $settings.schedule_override
$overrideValid = $false
$overrideRuns = @()
$overrideUpdatedAt = ''
if ($null -ne $override) {
    $overrideStart = Convert-ToLocalDateTime $override.period_start
    $overrideEnd = Convert-ToLocalDateTime $override.period_end
    $overrideFirst = Convert-ToLocalDateTime $override.first_run
    $overrideUpdatedAt = [string]$override.updated_at
    if ($null -ne $overrideStart -and $null -ne $overrideEnd -and $null -ne $overrideFirst -and
        $overrideStart -eq $period.Start -and $overrideEnd -eq $period.End -and
        $overrideFirst -ge $period.Start -and $overrideFirst -lt $period.End) {
        foreach ($rawRun in @($override.run_times)) {
            $run = Convert-ToLocalDateTime $rawRun
            if ($null -ne $run -and $run -gt $now -and $run -lt $period.End) {
                $overrideRuns += $run
            }
        }
        $overrideValid = $overrideRuns.Count -gt 0
    }
}

$defaultTimes = @('01:30', '04:00', '07:10', '10:20', '13:30', '16:00', '19:10', '22:20')
$triggers = @()
$planned = @()

# 04:00 and 16:00 are always retained as daily refresh boundaries.
foreach ($boundaryTime in @('04:00', '16:00')) {
    $daily = New-DailyPlan $boundaryTime (Get-NextOccurrence $now $boundaryTime)
    $triggers += $daily.Trigger
    $planned += $daily.Record
}

foreach ($timeText in $defaultTimes) {
    if ($timeText -eq '04:00' -or $timeText -eq '16:00') { continue }
    if ($overrideValid -and (Test-TimeInKind $timeText $period.Kind)) {
        # Do not let the affected period's old daily trigger run today. Its
        # daily recurrence starts at the next same-period occurrence.
        $firstAt = Get-NextAffectedOccurrence $period $now $timeText
    } else {
        $firstAt = Get-NextOccurrence $now $timeText
    }
    $daily = New-DailyPlan $timeText $firstAt
    $triggers += $daily.Trigger
    $planned += $daily.Record
}

if ($overrideValid) {
    foreach ($run in $overrideRuns) {
        $once = New-OncePlan $run
        $triggers += $once.Trigger
        $planned += $once.Record
    }
}

$taskSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    $principal = New-ScheduledTaskPrincipal -UserId $currentIdentity.Name -LogonType Interactive -RunLevel Highest
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $currentIdentity.Name -LogonType Interactive -RunLevel Limited
}

$oldXml = $null
$hadExistingTask = $false
try {
    $oldXml = Export-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-String
    $hadExistingTask = $true
} catch { }

try {
    $task = New-ScheduledTask -Action $action -Trigger $triggers -Settings $taskSettings -Principal $principal `
        -Description 'Wake the PC and run the BACoffee cafe task.'
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    $registeredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $wakeToRun = [bool]$registeredTask.Settings.WakeToRun
    $actualStarts = @($registeredTask.Triggers | ForEach-Object {
        $triggerStart = Convert-ToLocalDateTime $_.StartBoundary
        if ($null -ne $triggerStart) { $triggerStart.ToString('yyyy-MM-ddTHH:mm:ss', $culture) }
    })
    $expectedStarts = @($planned | ForEach-Object { $_.start_boundary })
    $missing = @($expectedStarts | Where-Object { $actualStarts -notcontains $_ })
    if (-not $wakeToRun) { throw 'WakeToRun was not retained.' }
    if ($actualStarts.Count -ne $expectedStarts.Count -or $missing.Count -gt 0) {
        throw ('Trigger verification failed; expected={0}; actual={1}' -f $expectedStarts.Count, $actualStarts.Count)
    }

    $legacyTaskName = 'BACof' + 'fe-Cafe'
    if (Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
    }
    if ($isAdmin) {
        powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 | Out-Null
        powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 | Out-Null
        powercfg /SETACTIVE SCHEME_CURRENT | Out-Null
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    $nextRun = ''
    if ($info.NextRunTime -and $info.NextRunTime -ne [datetime]::MinValue) {
        $nextRun = $info.NextRunTime.ToString('o')
    }
    $sleepCapabilities = (& powercfg /a 2>&1 | Out-String).Trim()
    $wakeTimers = (& powercfg /waketimers 2>&1 | Out-String).Trim()
    Write-ScheduleStatus @{
        success = $true
        installed = $true
        installed_at = (Get-Date).ToString('o')
        next_run_time = $nextRun
        task_name = $taskName
        wake_to_run = $wakeToRun
        windows_task_state = [string]$registeredTask.State
        sleep_capabilities = $sleepCapabilities
        wake_timers = $wakeTimers
        elevated = $isAdmin
        logon_type = 'Interactive'
        override_active = $overrideValid
        override_status = if ($overrideValid) { 'active' } elseif ($null -ne $override) { 'invalid_or_expired' } else { 'default' }
        override_updated_at = $overrideUpdatedAt
        trigger_count = $actualStarts.Count
        planned_triggers = @($planned)
        verified_trigger_starts = @($actualStarts)
    }
    Write-Host ('BACoffee wake task installed; triggers={0}.' -f $actualStarts.Count)
    exit 0
} catch {
    $failure = $_.Exception.Message
    $rollbackSucceeded = $false
    $rollbackError = ''
    try {
        if ($hadExistingTask) {
            Register-ScheduledTask -TaskName $taskName -Xml $oldXml -Force | Out-Null
        } elseif (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        $rollbackSucceeded = $true
    } catch {
        $rollbackError = $_.Exception.Message
    }
    Write-ScheduleStatus @{
        success = $false
        installed = $hadExistingTask
        task_name = $taskName
        wake_to_run = $false
        override_active = $false
        override_updated_at = $overrideUpdatedAt
        trigger_count = 0
        error = $failure
        rollback_succeeded = $rollbackSucceeded
        rollback_error = $rollbackError
    }
    Write-Error $failure
    exit 1
}
