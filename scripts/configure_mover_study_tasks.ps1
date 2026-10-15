param(
    [switch]$Apply
)

# Registers the learn-from-movers loop (approved 2026-10-14). All research/watch-only; nothing
# here alerts, sizes or places an order. Runs on the hidden launcher, logs to data/logs/.
#   tradedesk-filings-watch        every 15 min 07:30-21:30, every day: NSE exchange filings
#   tradedesk-mover-study-nightly  weekdays 19:30: observe NSE/BSE movers, register forward
#                                  picks before the next open, score matured picks
#   tradedesk-mover-study-crypto   daily 06:30 (after the 05:30 IST daily close): observe crypto
#   tradedesk-mover-study-search   Saturdays 10:00: weekly condition search on NSE/BSE
#   tradedesk-intraday-study-nightly  weekdays 20:15 (first run 15 Oct 2026): top up 15-minute
#                                  history, score live intraday picks, log movers' first hour
#   tradedesk-intraday-study-search   Saturdays 12:00: weekly first-hour condition search (NSE)
#   tradedesk-intraday-study-crypto-nightly  daily 06:45: the same for crypto (UTC-day sessions)
#   tradedesk-intraday-study-crypto-search   Saturdays 14:00: weekly crypto search (long only)
#   tradedesk-news-trial-fetch     hourly 07:20-22:20: enabled news sources only (none by default)
#   tradedesk-news-trial-score     weekdays 20:45: score each enabled source against the movers
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $repoRoot 'scripts\run_hidden.py'
foreach ($path in @($pythonw, $launcher, (Join-Path $repoRoot 'scripts\filings_watch.py'),
                    (Join-Path $repoRoot 'scripts\mover_study.py'),
                    (Join-Path $repoRoot 'scripts\intraday_mover_study.py'),
                    (Join-Path $repoRoot 'scripts\news_trial.py'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing: $path" }
}

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$filingsTrigger = New-ScheduledTaskTrigger -Daily -At '07:30'
$filingsTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At '07:30' `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 14)).Repetition

$newsTrigger = New-ScheduledTaskTrigger -Daily -At '07:20'
$newsTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At '07:20' `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 15)).Repetition

$jobs = @(
    @{ Name = 'tradedesk-filings-watch'; Minutes = 10; Triggers = @($filingsTrigger)
       Args = 'filings-watch scripts/filings_watch.py fetch --days 2' },
    @{ Name = 'tradedesk-mover-study-nightly'; Minutes = 90
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '19:30')
       Args = 'mover-study-nightly scripts/mover_study.py nightly --market nse --market bse' },
    @{ Name = 'tradedesk-mover-study-crypto'; Minutes = 45
       Triggers = @(New-ScheduledTaskTrigger -Daily -At '06:30')
       Args = 'mover-study-crypto scripts/mover_study.py nightly --market crypto' },
    @{ Name = 'tradedesk-mover-study-search'; Minutes = 180
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At '10:00')
       Args = 'mover-study-search scripts/mover_study.py search --market nse --market bse' },
    @{ Name = 'tradedesk-intraday-study-nightly'; Minutes = 90
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays `
                        -At ([datetime]'2026-10-15T20:15:00'))
       Args = 'intraday-study-nightly scripts/intraday_mover_study.py nightly --market nse' },
    @{ Name = 'tradedesk-intraday-study-search'; Minutes = 180
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At '12:00')
       Args = 'intraday-study-search scripts/intraday_mover_study.py search --market nse' },
    @{ Name = 'tradedesk-intraday-study-crypto-nightly'; Minutes = 60
       Triggers = @(New-ScheduledTaskTrigger -Daily -At '06:45')
       Args = 'intraday-study-crypto-nightly scripts/intraday_mover_study.py nightly --market crypto' },
    @{ Name = 'tradedesk-intraday-study-crypto-search'; Minutes = 180
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At '14:00')
       Args = 'intraday-study-crypto-search scripts/intraday_mover_study.py search --market crypto' },
    @{ Name = 'tradedesk-intraday-study-bse-nightly'; Minutes = 90
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '20:45')
       Args = 'intraday-study-bse-nightly scripts/intraday_mover_study.py nightly --market bse' },
    @{ Name = 'tradedesk-intraday-study-bse-search'; Minutes = 180
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At '13:00')
       Args = 'intraday-study-bse-search scripts/intraday_mover_study.py search --market bse' },
    @{ Name = 'tradedesk-news-trial-fetch'; Minutes = 10; Triggers = @($newsTrigger)
       Args = 'news-trial scripts/news_trial.py fetch' },
    @{ Name = 'tradedesk-news-trial-score'; Minutes = 15
       Triggers = @(New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '20:45')
       Args = 'news-trial scripts/news_trial.py score' }
)

foreach ($job in $jobs) { Write-Host "$($job.Name): $pythonw `"$launcher`" $($job.Args)" }
if (-not $Apply) {
    Write-Host 'Preview only. Re-run with -Apply to register or replace the tasks.'
    return
}

foreach ($job in $jobs) {
    $arguments = "`"$launcher`" $($job.Args)"
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $repoRoot
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $job.Minutes) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $job.Name -Action $action -Trigger $job.Triggers `
        -Settings $settings `
        -Description 'Research/watch-only mover study and filings watch; never places orders.' `
        -Force | Out-Null
    $task = Get-ScheduledTask -TaskName $job.Name -ErrorAction Stop
    if ([string]$task.State -eq 'Disabled') { throw "$($job.Name) was registered disabled" }
    if ($task.Actions[0].Arguments -ne $arguments) { throw "$($job.Name) action verification failed" }
    Write-Host "Registered and verified $($job.Name)."
}
