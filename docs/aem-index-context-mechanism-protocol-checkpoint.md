# AEM index-context mechanism protocol checkpoint

Frozen: 30 September 2026

This checkpoint freezes the real-outcome test before opening the AEM outcome columns.
It does not report an accuracy result and does not change the 21.50% canonical
baseline.

## Frozen test

The test evaluates four ordered, threshold-free alignment rules over completed 3- or
5-minute returns from NIFTY 50, BANK NIFTY, and Nifty Financial. The unchanged
population identity is 815 trade attempts, 693 resolved fills, 149 strict wins, and
-0.274707954R mean net R over the same 120 sessions.

Each rule is compared with its inverse, a within-session next-event circular placebo,
and 256 within-session matched-selection shuffles using seed `20260930`. Results must
improve strict accuracy and mean net R in each of three consecutive 40-session folds,
clear both shuffle 95th percentiles with empirical p-values no greater than 0.05,
produce positive absolute mean net R, and meet the frozen fill and availability floors.
One failed gate is a failure.

The exact protocol fingerprint is
`f8543543eb46c06710389882a5d791cb0886dbd0e30e31978f124034e3c6852d`.
The local no-outcome freeze run is `f595a49f2e1a4592b403f3e0a6027e86`.

## Verification

- Seven synthetic mechanism tests pass, including a deliberately strong mechanism,
  outcome leakage rejection, exact mask behavior, and empty-selection failure.
- The 167-test context/history regression suite passes.
- Ruff passes for the implementation, CLI, and mechanism tests.
- The frozen feature artifact remains outcome-free and retains integrity run
  `b66ac4b126c54374a9fd8cb2ff846cfb`.

## Decision

After this checkpoint is committed, the real mechanism test may run exactly once from
the committed protocol. A pass permits only the bounded selector diagnostic. A failure
stops this context version. Neither result changes production, dashboard, management,
risk, alerts, broker behavior, or the canonical baseline.

Machine-readable evidence is in
`docs/evidence/aem-context-mechanism-protocol.json`.
