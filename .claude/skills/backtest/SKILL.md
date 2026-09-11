---
name: backtest
description: Run and interpret a tradedesk backtest for a setup over stored history (expectancy R, profit factor, drawdown, gap damage).
---

Use the `tradedesk` MCP tool `backtest(setup, start, end)`. Setups: base_breakout,
trend_pullback, nr7_breakout.

Report expectancy in R, win rate, profit factor, max drawdown, gap losses/damage and exit
reasons. Always repeat the survivorship caveat the tool returns. For a walk-forward view run
two backtests split at a date and compare; parameters are only ever tuned on the earlier
period. Never present a backtest as a forecast.
