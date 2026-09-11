---
name: analyze
description: Explain why a stock is (or is not) on the tradedesk watchlist - run the setups, indicators and patterns on one scrip and read the result.
---

Use the `tradedesk` MCP tools (read-only). Given a symbol or scrip code (e.g. SBIN or NSE_3045):

1. Call `get_watchlist` and find the entry (rejected entries are listed too).
2. Call `analyze` with the scrip code (and the date, if the user names one).
3. Explain in plain language: trend (EMAs, ADX), relative strength percentile, ATR%, which
   pattern the code found (base / flag / pullback / squeeze) and its geometry, the regime,
   and the signal's trigger/stop/targets, score components and rejection reasons.
4. Never propose different levels or sizes - the rules set those. If the user wants a change,
   point at the config knob (config/setups.yaml, config/engine.yaml, config/risk.yaml).
