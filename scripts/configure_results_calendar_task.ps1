param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'tradedesk-results-calendar'
$uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
$arguments = 'run --no-sync tradedesk data refresh-results'
if (-not (Test-Path -LiteralPath $uv)) { throw "uv is missing: $uv" }

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$triggers = @(
    (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '07:30'),
    (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '16:50')
)
Write-Host "${taskName}: weekdays 07:30 and 16:50 IST - $uv $arguments"
Write-Host 'Fetches NSE public board-meeting dates (results calendar) into the NSE store.'
if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to register or replace the task.'
    return
}

$action = New-ScheduledTaskAction -Execute $uv -Argument $arguments -WorkingDirectory $repoRoot
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings `
    -Description 'Refresh the results calendar from NSE; never places orders.' -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ([string]$task.State -eq 'Disabled') { throw "$taskName was registered disabled" }
if ($task.Actions[0].Arguments -ne $arguments) { throw "$taskName action verification failed" }
Write-Host "Registered and verified $taskName."
