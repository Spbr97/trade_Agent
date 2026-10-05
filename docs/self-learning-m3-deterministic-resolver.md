# Self-learning Milestone 3 — deterministic outcome resolver

Completed: 2026-10-06

## Outcome

The tracker can now resolve sealed `quick-profit-v1` and `swing-v1` predictions using the
rules frozen in Milestone 1. Legacy rows retain the historical resolver and remain
labelled legacy. Nothing is retroactively regraded.

Versioned rows use independent IDs (`signal::contract-version`), so identical frozen
entries may be measured under quick-profit and swing contracts without pooling or
overwriting one another. The ordinary market trackers continue to log legacy calls until
the preregistered exit experiment activates the versioned cohorts.

## Deterministic rules

- Only sessions after the arming date can trigger entry.
- Entry remains valid for the contract's three-session window.
- Gap entries fill at the open; intraday touches fill at the trigger.
- Opens beyond the frozen ATR chase limit become `chased`, not fills.
- A close below the stop before entry becomes `invalidated`.
- No entry by expiry becomes `never_triggered`.
- Stop is checked before target on every bar, including same-bar ambiguity.
- Gaps below stop exit at the open; ordinary stop and target touches exit at their levels.
- Quick-profit uses 0.75R and a three-session maximum hold.
- Swing uses 2.0R and a ten-session maximum hold.
- A full holding window without a barrier exits at the final close as `timeout`.
- An incomplete future window remains pending.
- Missing observed OHLC data, invalid geometry, unordered/duplicate candles, unknown
  contracts, or unsealed versioned rows fail closed as unavailable/invalid.

## Recorded outcome audit

Each versioned terminal result records:

- entry and exit session and actual slippage-adjusted prices;
- first price event and the `stop_before_target` resolution rule;
- gross R, execution R, net R after the frozen fee/tax schedule, and total charges;
- reporting-only crypto after-tax R under the existing VDA tax contract;
- holding time, time to entry, and time to resolution; and
- maximum favourable and adverse excursion.

NSE/BSE after-tax R remains `null`. The official Section 111A headline rate does not by
itself define the user's complete tax treatment, surcharge, cess, offsets, or investor vs
trader classification, so the resolver does not invent a personal tax result.

A target counts as a strict success only when the after-cost net R is positive. Timeouts,
stops and gap stops remain valid failures. Invalid, unavailable, never-triggered and
pending rows receive no performance label and cannot enter performance learning.

## Scheduling and visibility

The existing market-specific collection pipelines already call the shared resolver:

- crypto through its 30-minute tracker schedule;
- NSE through its after-close accuracy collection; and
- BSE through its after-close accuracy collection.

Repeated runs provide finality when entry or holding windows mature. Dashboard call and
Learning tables now expose gross R, net R, first event, MFE and MAE. The summary API keeps
legacy, quick-profit and swing counts/performance separate.

## Verification gate

Tests cover deterministic replay, target, stop-first same-bar behavior, gap/chase,
pre-entry invalidation, entry expiry, timeout finality, insufficient future history,
missing observed candles, quick/swing separation, after-cost results, crypto after-tax R,
tracker routing, immutable-ledger compatibility, and invalid-call reporting.

The next milestone is failure attribution. It must explain mature failures without
rewriting the sealed prediction or claiming diagnostic associations are proven causes.
