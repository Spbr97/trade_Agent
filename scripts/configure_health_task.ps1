param(
    [switch]$Apply
)

# Hourly health check (approved 2026-10-14): reads every tradedesk-* task result and the
# after-close pipeline status files, writes data/reports/health.json for the dashboard
# banner, and messages Telegram about new failures once Telegram is configured.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $repoRoot 'scripts\run_hidden.py'
foreach ($path in @($pythonw, $launcher, (Join-Path $repoRoot 'scripts\task_health.py'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing: $path" }
}
$name = 'tradedesk-health-check'
$arguments = "`"$launcher`" health-check scripts/task_health.py check"
Write-Host "${name}: $pythonw $arguments"
if (-not $Apply) { Write-Host 'Preview only. Re-run with -Apply.'; return }

$trigger = New-ScheduledTaskTrigger -Daily -At '00:05'
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At '00:05' `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $repoRoot
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Hourly tradedesk health check; read-only, never trades.' -Force | Out-Null
$task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
if ($task.Actions[0].Arguments -ne $arguments) { throw "$name action verification failed" }
Write-Host "Registered and verified $name."
