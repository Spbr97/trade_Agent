param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'tradedesk-probation-desk-bse'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$script = Join-Path $repoRoot 'scripts\probation_desk.py'
$arguments = "`"$script`" run --market bse"
if (-not (Test-Path -LiteralPath $python)) { throw "Virtual-environment Python is missing: $python" }
if (-not (Test-Path -LiteralPath $script)) { throw "Missing: $script" }

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
# After the 16:05 BSE tracker and the self-review runs, so it sees the day's graded signals.
$triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '17:15')
Write-Host "${taskName}: weekdays 17:15 IST - $python $arguments"
Write-Host 'Capped probation calls for approved detectors; the human places every order.'
if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to register or replace the task.'
    return
}

$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $repoRoot
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings `
    -Description 'Probation desk: capped calls for gauntlet-passed detectors; never places orders.' `
    -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ([string]$task.State -eq 'Disabled') { throw "$taskName was registered disabled" }
if ($task.Actions[0].Arguments -ne $arguments) { throw "$taskName action verification failed" }
Write-Host "Registered and verified $taskName."
