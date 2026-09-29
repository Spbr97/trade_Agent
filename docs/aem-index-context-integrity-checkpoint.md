# AEM index-context causal join integrity checkpoint

Date: 30 September 2026

Branch: `codex/aem-context-m2-integrity`

Status: **integrity passed for price context; VWAP context rejected as insufficient;
no model run and no baseline improvement claimed**

## Real result

Run `b66ac4b126c54374a9fd8cb2ff846cfb` joined the three frozen index series to every
decision in the canonical AEM v1 dataset:

| Population | Count |
|---|---:|
| Frozen decisions | 1,397 |
| TRADE decisions | 815 |
| NO_TRADE decisions | 582 |
| Causally joined | 1,397 |
| Excluded | 0 |
| Outcome columns in feature artifact | 0 |

The join uses only a bar whose open plus one minute is no later than the decision's
`available_at`. Therefore a 09:20 decision can consume bars only through the 09:19
open. Tests prove that changing the 09:20 or later bar cannot change that snapshot.

The known Nifty Financial source gap on 7 July 2026 from 15:17-15:29 affects zero
events because the frozen AEM decision window ends at 11:00. The gap remains recorded
and unfilled.

## Feature availability

| Causal feature family | Decisions available |
|---|---:|
| All-index 1-minute returns | 1,397 |
| All-index 3-minute returns | 1,397 |
| All-index 5-minute returns | 1,397 |
| All-index 15-minute returns | 755 |
| Complete all-index VWAP | 16 |

Fifteen-minute returns correctly fail closed for the 642 decisions before 09:30,
where a full 15 completed bars do not exist. They are not backfilled with a shorter
window.

Index volume is not reliable enough for the registered VWAP family. NIFTY 50 had
complete prefix volume for 919 decisions, BANK NIFTY for 908, and Nifty Financial for
only 16. Because all three are required for the uniform context hypothesis, only 16
decisions have complete all-index VWAP. VWAP is therefore removed from the next
mechanism test rather than calculated from partial volume.

## Leakage and integrity controls

The generated feature artifact contains event identity, decision metadata, causal
context features, availability flags, and explicit exclusions. It deliberately omits
labels, strict success, trade status, target hits, entry/exit fields, P&L, gross R,
and net R. Current stock membership in an index or sector is also absent.

The implementation fails closed on:

- naive or second-level decision timestamps;
- decisions outside the frozen session calendar or 09:20-11:00 window;
- duplicated event identifiers;
- a missing completed context minute;
- invalid, duplicated, non-minute or noncausal candle coordinates; and
- invalid OHLCV geometry.

Fifty-eight focused collection, preflight, and integrity tests pass, and Ruff is
clean for the changed Python files.

## Accuracy interpretation and decision

This checkpoint proves only that price context can be supplied causally to the frozen
population. It does not show that the context predicts success. The canonical
baseline remains **149/693 strict wins (21.50%)** and **-0.27471R per resolved fill**.

The price-only 1/3/5-minute context is authorized for the next preregistered
mechanism/placebo diagnostic on consumed development data. Fifteen-minute features may
be tested only on their explicit available subset. VWAP is not authorized. No
selector, production, management, risk, dashboard, alert, broker, or order behavior
changes.

Machine-readable evidence is in
[`docs/evidence/aem-context-integrity.json`](evidence/aem-context-integrity.json).
