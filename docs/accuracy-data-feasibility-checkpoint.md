# Accuracy program — Checkpoint 1 feasibility verdict

Completed: 30 September 2026

Branch: `codex/accuracy-data-feasibility`

Status: **stopped: mandatory point-in-time data are not yet feasible locally**

## Result

Checkpoint 1's manifest, source inventory, zero-request acquisition ledger, causal
validators and fail-closed tests are implemented. The audit deliberately stopped
before signal construction because the local evidence does not satisfy the frozen
data gate.

The existing Cohort A contains 50 exact NSE identifiers across 120 consumed sessions.
Stock M1 coverage is 5,999/6,000 symbol-sessions; the known `NSE_3063` gap on 30 April
2026 remains excluded. Broad-index context is 359/360 code-sessions. These data are
useful development inputs, but historical point-in-time sector/index membership is
not verified.

Cohort B has no frozen 200-stock population. Historical announcement dissemination
timestamps, official corporate-action publication provenance, contract-note golden
evidence, and historical spread/depth or a validated proxy are also absent. Therefore
the complete coordinate table cannot be created without inventing symbols or
membership, D2 cannot be locked, no signal family is authorized, and Checkpoint 2 is
blocked.

## Layer verdict

| Layer | Verdict | Main gap |
|---|---|---|
| Stock M1/M5/M15/D1 | Partial | Cohort A only; M5/M15 not frozen; one M1 gap |
| Market/sector context | Partial | Three broad indices; point-in-time sector series absent |
| Symbol master | Unavailable | Historical listing, sector and membership intervals absent |
| Cross-section | Unavailable | Causal sector/liquidity peer population cannot be frozen |
| Announcements | Unavailable | No versioned, complete historical local corpus |
| Corporate actions | Partial | Heuristic only; publication/adjustment provenance absent |
| Execution | Unavailable | Modeled costs only; no real intraday golden note or spread history |
| Provenance | Partial | Existing artifacts are hashed; missing layers have no versions |

## Official-source feasibility

The exchange announcement page exposes received and dissemination timestamps, but
historical bulk completeness and permitted automated collection still require
verification. NSE describes historical index constituent information as a licensed
data product. Historical order/trade data are offered as a paid subscription, and
official charge schedules still need reconciliation to the user's broker contract
notes.

- [NSE corporate announcements](https://www.nseindia.com/companies-listing/corporate-filings-announcements)
- [NSE index data subscription](https://www.nseindia.com/static/nse-indices/index-data-subscription)
- [NSE historical order/trade data](https://www.nseindia.com/static/market-data/eod-historical-data-subscription)
- [NSE statutory levies](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies)

No source request was made in this checkpoint. The request budget remains zero until
access, licensing, date bounds and storage rules are approved and frozen.

## Integrity controls

The implementation rejects outcome-like columns recursively, duplicate coordinates,
coverage without a source/version/availability timestamp, unavailable rows without an
exclusion reason, candidate-inclusive peer aggregates, and peer observations from
after decision time. Fifteen focused tests pass; the combined Checkpoint 0–1 focused
regression suite passes all 38 tests.

No new outcome was opened, no model was trained, and no candidate was nominated. The
canonical baseline remains **149/693 = 21.50%**, with no improvement claimed.

## Required next action

Checkpoint 1 must be rerun after obtaining:

1. historical point-in-time listing, sector and index-membership intervals;
2. a reproducible 200-stock Cohort B plus its price coverage;
3. a versioned exchange-announcement corpus with dissemination timestamps;
4. official corporate-action/adjustment provenance; and
5. a real intraday contract-note golden source plus spread/depth history or a
   preregistered conservative proxy.

Signal construction remains prohibited until at least one family passes the complete
data-feasibility gate.
