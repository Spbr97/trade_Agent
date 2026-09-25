# AEM market/sector context readiness checkpoint

Date: 25 September 2026

Frozen dataset: `ec539cf66bea4c18ba994506f85a52d6`

Status: **not ready; no baseline improvement established**

## Outcome

This checkpoint adds an isolated, resumable collector for causal one-minute market
and financial-sector context. It deliberately does not change the AEM baseline,
production calls, management logic, risk rules or order paths.

The frozen plan covers 120 evaluation sessions and these index series:

- NIFTY 50: `NSE_40000001`
- BANK NIFTY: `NSE_40000003`
- Nifty Financial: `NSE_40000100`

That is 360 required index-sessions and 135,000 expected regular-session M1 bars.
Current coverage is **0/360 sessions and 0/135,000 bars**.

## Safety result

Offline preparation completed in run `b837ccb6301042e09dd0e31123ca6ab7`.
A bounded collection run `7e30983067ab426db6ecb88dc1305a54` was attempted at
10:17 IST. The safety preflight returned `protected_weekday_market_window`, so it
stopped before constructing a broker client or making a remote request.

Requests made in the run: **0**. Requests recorded across context runs: **0**.
No live process was stopped and no safety check was bypassed.

## Accuracy interpretation

This is data-foundation work, not evidence that prediction quality improved. The
canonical broader AEM baseline remains **149/693 strict wins (21.50%)** with
**−0.27471R per resolved fill**. The most recent bounded selector remains rejected.

The context data can only be credited after all of the following:

1. collect the frozen index history outside protected market hours;
2. verify timestamps, session completeness and causal availability;
3. resolve historical point-in-time sector membership or explicitly restrict the
   sector-context hypothesis so current membership cannot leak into history;
4. preregister a bounded trial set and evaluate it on later unconsumed or prospective
   evidence against the unchanged baseline, including accuracy, Wilson bound,
   availability and after-cost net R.

The structured evidence is in
[`docs/evidence/aem-market-sector-context-readiness.json`](evidence/aem-market-sector-context-readiness.json).
