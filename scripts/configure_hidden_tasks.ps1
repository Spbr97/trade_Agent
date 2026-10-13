param(
    [switch]$Apply
)

# Moves the short-lived tradedesk-* scheduled tasks onto the hidden launcher
# (pythonw.exe scripts/run_hidden.py <log> <script|tradedesk> args) so they stop opening
# console windows. Output goes to data/logs/<log>.log. No admin needed.
# Deliberately NOT touched: tradedesk-dashboard and tradedesk-cloudflare-tunnel (long-running
# services), tradedesk-close-finality (stale, errored since 14 Sep).
# Re-running a configure_*.ps1 for one of these tasks puts the visible console action back.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $repoRoot 'scripts\run_hidden.py'
if (-not (Test-Path -LiteralPath $pythonw)) { throw "Missing: $pythonw" }

$tasks = [ordered]@{
    'tradedesk-after-close'              = 'after-close scripts/accuracy_collection_pipeline.py --market nse'
    'tradedesk-bse-after-close'          = 'bse-after-close scripts/accuracy_collection_pipeline.py --market bse'
    'tradedesk-bse-tracker'              = 'bse-tracker scripts/bse_signal_tracker.py'
    'tradedesk-bse-research-tracker'     = 'bse-research-tracker scripts/research_tracker.py run --market bse'
    'tradedesk-research-tracker'         = 'research-tracker scripts/research_tracker.py run'
    'tradedesk-crypto-research-tracker'  = 'crypto-research-tracker scripts/research_tracker.py run --market crypto'
    'tradedesk-crypto-eod-learning'      = 'crypto-eod-learning scripts/research_tracker.py eod-learn --market crypto'
    'tradedesk-nse-self-review'          = 'nse-self-review tradedesk review self-review-run --market nse'
    'tradedesk-bse-self-review'          = 'bse-self-review tradedesk review self-review-run --market bse'
    'tradedesk-crypto-self-review'       = 'crypto-self-review tradedesk review self-review-run --market crypto'
    'tradedesk-results-calendar'         = 'results-calendar tradedesk data refresh-results'
    'tradedesk-options-snapshot'         = 'options-snapshot scripts/options_snapshot.py'
    'tradedesk-live-session'             = 'live-session tradedesk live --until 15:35 --no-dashboard'
    'tradedesk-bse-live-session'         = 'bse-live-session tradedesk live --market bse --until 15:35 --no-dashboard'
    'tradedesk-equity-pre-takeoff-watch' = 'equity-pre-takeoff-watch scripts/pre_takeoff_watch.py collect --market equity'
    'tradedesk-potential-movers-nse'     = 'potential-movers-nse scripts/potential_movers.py run --market nse'
    'tradedesk-potential-movers-bse'     = 'potential-movers-bse scripts/potential_movers.py run --market bse'
    'tradedesk-potential-movers-crypto'  = 'potential-movers-crypto scripts/potential_movers.py run --market crypto'
    'tradedesk-probation-desk-bse'       = 'probation-desk-bse scripts/probation_desk.py run --market bse'
}

foreach ($name in $tasks.Keys) {
    $arguments = "`"$launcher`" $($tasks[$name])"
    Write-Host "${name}: $arguments"
    if (-not $Apply) { continue }
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $repoRoot
    Set-ScheduledTask -TaskName $name -Action $action | Out-Null
    $check = (Get-ScheduledTask -TaskName $name).Actions[0]
    if ($check.Arguments -ne $arguments -or $check.Execute -ne $pythonw) { throw "$name verification failed" }
}
if ($Apply) { Write-Host 'Applied and verified.' } else { Write-Host 'Preview only. Re-run with -Apply.' }
