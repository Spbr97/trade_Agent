"""Strategy Validation Harness (M14-M18 lab): a deterministic test framework for measuring
whether a mechanical rule set has positive expectancy after costs - not a trading bot. It
never places an order and never writes to a production path; see the harness's own plan
(`tradedesk_lab/harness/rules/`) for the Context this was built for.

Wired to primitives this project already has tested rather than re-implementing them:
`tradedesk.backtest.null_baseline` (the random-entry benchmark), `tradedesk.backtest.reports`
(metrics, drawdown, streaks), `tradedesk.markets.costs` (the cost model), and
`tradedesk_lab.validation` (purge/walk-forward/CPCV/PBO/DSR). See `gauntlet.py::run_gauntlet`
for the orchestration and `spec.py::KillCriteria` for the pass/fail gate.
"""
