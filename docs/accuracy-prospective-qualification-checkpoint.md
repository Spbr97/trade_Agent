# NSE prospective qualification bundle

Implemented: 4 October 2026

Status: **implemented; collecting; no baseline change**

The frozen NSE trend-pullback challenger already has a strong one-time historical result:
186/223 strict wins (83.41%), a 77.97% Wilson lower bound, and +0.083R mean after-cost
expectancy. That remains historical evidence. It is not the live or canonical baseline.

This checkpoint adds the missing atomic review over the four fresh-evidence streams:

- M8: at least 100 resolved forward-only calls over 30 active sessions, at least 80%
  strict accuracy, at least 70% Wilson lower bound, at least 70% successful sessions,
  and positive after-cost expectancy;
- M9: source/model integrity, positive doubled-slippage expectancy, and session- and
  week-cluster lower bounds of at least 70%;
- M10: matched same-session random-stock selection control with at least +0.10R advantage
  and one-sided empirical p no greater than 0.05; and
- M11: same-stock random-future-session timing control with the same +0.10R and p-value
  gates.

The bundle additionally requires exact candidate fingerprints, compatible protocol
versions, M8/M9 summary identity, M10 outcome parity, M11 source integrity, and identical
resolved-call/session/model-result denominators across the final paired comparisons.
Missing components are `not_available`, immature components are `collecting`, mature
failed gates are `prospective_rejected`, and identity or parity failures are `degraded`.
None can be interpreted as a pass.

The strongest possible result is `human_review_authorized`. Even that result leaves
`baseline_improved` and `eligible_for_live` false. A separate explicitly approved review
would be required to replace the canonical AEM comparator or change any alert, sizing,
management, broker, or order behavior.

The initial run remains `collecting_insufficient_evidence`: M8, M10 and M11 have zero
fresh resolved calls and zero active sessions after activation on 3 October 2026. Since
4 October 2026 is a Sunday, there is no missing NSE trading session to backfill. The
canonical comparator therefore remains 149/693 = **21.50%**, at -0.27471R.

Run or inspect the bundle with:

```text
python scripts/accuracy_prospective_qualification.py run
python scripts/accuracy_prospective_qualification.py status
```

Implementation is isolated in
`tradedesk_lab/accuracy_prospective_qualification.py`; it does not modify the frozen
M8-M11 collectors or production/dashboard code.
