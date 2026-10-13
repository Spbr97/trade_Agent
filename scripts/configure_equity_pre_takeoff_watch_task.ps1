param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'tradedesk-equity-pre-takeoff-watch'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$collector = Join-Path $repoRoot 'scripts\pre_takeoff_watch.py'
$arguments = "`"$collector`" collect --market equity"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual-environment Python is missing: $python"
}
if (-not (Test-Path -LiteralPath $collector)) {
    throw "Pre-takeoff collector is missing: $collector"
}

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$firstRun = [datetime]::Today.AddHours(9).AddMinutes(31)
$lastRun = [datetime]::Today.AddHours(15).AddMinutes(31)
$triggers = @()
for ($runAt = $firstRun; $runAt -le $lastRun; $runAt = $runAt.AddMinutes(15)) {
    $triggers += New-ScheduledTaskTrigger `
        -Weekly `
        -WeeksInterval 1 `
        -DaysOfWeek $weekdays `
        -At $runAt
}

Write-Host "${taskName}: weekdays 09:31-15:31 IST every 15 minutes"
Write-Host 'Scope: full daily-liquid NSE then BSE universe; research only'
Write-Host "Action: $python $arguments"
if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to register or replace the task.'
    return
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument $arguments `
    -WorkingDirectory $repoRoot
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 14) `
    -MultipleInstances IgnoreNew `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Description 'Forward-only completed-M15 NSE/BSE pre-takeoff research watch; never places orders.' `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ([string]$task.State -eq 'Disabled') {
    throw "$taskName was registered disabled"
}
if ($task.Actions[0].Execute -ne $python -or $task.Actions[0].Arguments -ne $arguments) {
    throw "$taskName action verification failed"
}
if ($task.Settings.MultipleInstances -ne 'IgnoreNew') {
    throw "$taskName overlap protection verification failed"
}
if ($task.Triggers.Count -ne $triggers.Count) {
    throw "$taskName trigger verification failed"
}

Write-Host "Registered and verified $taskName with $($triggers.Count) weekday triggers."
