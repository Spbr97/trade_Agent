param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pipelineScript = 'scripts/accuracy_collection_pipeline.py'
$primaryTasks = @(
    @{ Name = 'tradedesk-after-close'; Market = 'nse'; Limit = 'PT4H' },
    @{ Name = 'tradedesk-bse-after-close'; Market = 'bse'; Limit = 'PT4H' },
    @{ Name = 'tradedesk-crypto-tracker'; Market = 'crypto'; Limit = 'PT2H' }
)
$redundantTasks = @(
    'tradedesk-research-tracker',
    'tradedesk-bse-tracker',
    'tradedesk-bse-research-tracker'
)
$allNames = @($primaryTasks | ForEach-Object { $_.Name }) + $redundantTasks
$tasks = @{}

# Preflight every target before the first backup or mutation. Changing a running task can
# split one source session, and a late missing task can otherwise leave a partial migration.
foreach ($name in $allNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
    if ($Apply -and [string]$task.State -eq 'Running') {
        throw "Task $name is running; wait for it to finish before applying R2"
    }
    $tasks[$name] = $task
}
foreach ($registration in $primaryTasks) {
    $task = $tasks[$registration.Name]
    if (-not $task.Actions -or -not $task.Actions[0].Execute) {
        throw "Task $($registration.Name) has no executable action"
    }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path $repoRoot "data/scheduler-backups/accuracy-r2-$stamp"

function Backup-Task([string]$Name) {
    $xml = Export-ScheduledTask -TaskName $Name -ErrorAction Stop
    $xml | Set-Content -LiteralPath (Join-Path $backupRoot "$Name.xml") -Encoding UTF8
}

foreach ($registration in $primaryTasks) {
    $task = $tasks[$registration.Name]
    $execute = $task.Actions[0].Execute
    $arguments = "run --no-sync python $pipelineScript --market $($registration.Market)"
    Write-Host (
        "$($registration.Name): $execute $arguments " +
        "(execution limit $($registration.Limit))"
    )
}
foreach ($name in $redundantTasks) {
    Write-Host "${name}: disabled after its work moved into the ordered market pipeline"
}

if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to change Task Scheduler.'
    return
}

New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
foreach ($name in $allNames) {
    Backup-Task $name
}

foreach ($registration in $primaryTasks) {
    $task = $tasks[$registration.Name]
    $arguments = "run --no-sync python $pipelineScript --market $($registration.Market)"
    $action = New-ScheduledTaskAction `
        -Execute $task.Actions[0].Execute `
        -Argument $arguments `
        -WorkingDirectory $repoRoot
    $settings = $task.Settings
    $settings.ExecutionTimeLimit = $registration.Limit
    Set-ScheduledTask `
        -TaskName $registration.Name `
        -Action $action `
        -Settings $settings | Out-Null
    Enable-ScheduledTask -TaskName $registration.Name | Out-Null
}
foreach ($name in $redundantTasks) {
    Disable-ScheduledTask -TaskName $name | Out-Null
}

# Read back every authoritative setting. Cmdlet success alone is not verification that
# actions, timeouts, and disabled states match the registered migration.
foreach ($registration in $primaryTasks) {
    $task = Get-ScheduledTask -TaskName $registration.Name -ErrorAction Stop
    $expected = "run --no-sync python $pipelineScript --market $($registration.Market)"
    if ($task.Actions[0].Arguments -ne $expected) {
        throw "Task $($registration.Name) action verification failed"
    }
    if ($task.Settings.ExecutionTimeLimit -ne $registration.Limit) {
        throw "Task $($registration.Name) execution-limit verification failed"
    }
    if ([string]$task.State -eq 'Disabled') {
        throw "Task $($registration.Name) was not enabled"
    }
}
foreach ($name in $redundantTasks) {
    if ([string](Get-ScheduledTask -TaskName $name -ErrorAction Stop).State -ne 'Disabled') {
        throw "Task $name was not disabled"
    }
}

Write-Host "Applied and verified. Recoverable task XML backups: $backupRoot"
