param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'tradedesk-crypto-pre-takeoff-watch'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$collector = Join-Path $repoRoot 'scripts\pre_takeoff_watch.py'
$arguments = "`"$collector`" collect --market crypto"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual-environment Python is missing: $python"
}
if (-not (Test-Path -LiteralPath $collector)) {
    throw "Pre-takeoff collector is missing: $collector"
}

Write-Host "${taskName}: every 15 minutes; all active CoinDCX INR pairs; research only"
Write-Host "Action: $python $arguments"
if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to register or replace the task.'
    return
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument $arguments `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description 'Forward-only completed-M15 crypto pre-takeoff research watch; never places orders.' `
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

Write-Host "Registered and verified $taskName."
