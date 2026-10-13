param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$script = Join-Path $repoRoot 'scripts\potential_movers.py'
if (-not (Test-Path -LiteralPath $python)) { throw "Virtual-environment Python is missing: $python" }
if (-not (Test-Path -LiteralPath $script)) { throw "Missing: $script" }

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$jobs = @(
    @{ Name = 'tradedesk-potential-movers-nse'; Market = 'nse'
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '16:55') },
    @{ Name = 'tradedesk-potential-movers-bse'; Market = 'bse'
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '17:05') },
    @{ Name = 'tradedesk-potential-movers-crypto'; Market = 'crypto'
       Triggers = @((New-ScheduledTaskTrigger -Daily -At '05:45'), (New-ScheduledTaskTrigger -Daily -At '06:15')) }
)

foreach ($job in $jobs) {
    $arguments = "`"$script`" run --market $($job.Market)"
    Write-Host "$($job.Name): $python $arguments"
}
if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to register or replace the tasks.'
    return
}

foreach ($job in $jobs) {
    $arguments = "`"$script`" run --market $($job.Market)"
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $repoRoot
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $job.Name -Action $action -Trigger $job.Triggers `
        -Settings $settings `
        -Description 'Research-only potential-movers screen; never places orders.' -Force | Out-Null
    $task = Get-ScheduledTask -TaskName $job.Name -ErrorAction Stop
    if ([string]$task.State -eq 'Disabled') { throw "$($job.Name) was registered disabled" }
    if ($task.Actions[0].Arguments -ne $arguments) { throw "$($job.Name) action verification failed" }
    Write-Host "Registered and verified $($job.Name)."
}
