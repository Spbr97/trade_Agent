# Literature checks - 25 October 2026

Three published edges the project had not tested (per CLAUDE.md), each with its pass bar
written down before running. Module: `tradedesk_lab/literature_checks.py`
(`python -m tradedesk_lab.literature_checks`), tests: `tests_lab/test_literature_checks.py`.
Data: laptop snapshot to 16-17 Oct (daily NSE 2016-2026; NIFTY/NIFTYBEES 15-minute bars for
247 days). Research only; no production file changed.

| Check | Source | Pass bar (pre-registered) | Result | Verdict |
|---|---|---|---|---|
| A. Pre-results run-up: enter 10 sessions before last year's results date + 365d, exit the day before results | Frazzini & Lamont, NBER w13090; Kundu, Banerjee & McMillan 2021 (India) | net excess > 0 after 0.56%, day-clustered t > 3, both halves | +0.27% gross excess (~9 sessions), -0.21% net, t -1.7; halves -0.08%, -0.32% | FAIL (costs) |
| B1. Frog-in-the-pan momentum (lowest-ID third of top-30% 12-1 momentum) | Da, Gurun & Warachka, RFS 2014 | beats plain 12-1 top decile, paired t > 2.5, both halves | +0.08% per 20 sessions, t 0.4; halves +0.34%, -0.16% | FAIL |
| B2. Residual momentum (beta-only market model, vol-scaled) | Blitz, Huij & Martens 2011 | same | +0.13% per 20 sessions, t 0.7; halves +0.28%, +0.00% | FAIL |
| C. NIFTY intraday momentum: prev close -> 09:45 sets direction for NIFTYBEES 15:00 -> 15:30 | Gao, Han, Li & Zhou (JFE) | net > 0 after 0.08%, t > 2, both halves | -0.096% a day, t -8.2; correlation -0.085 | FAIL |

Notes
- A is the only one with a real gross effect: a pre-results premium of about +0.27% over
  nine sessions, close to the published ~60 bp a month. It does not pay for a Rs 10,000
  delivery round trip. A possible follow-up, not tested: enter before results and hold
  through into the existing earnings-drift exit only when the reaction qualifies, so one
  round trip covers both effects.
- An early scratch version of A measured the market using same-day turnover to define the
  liquid set (a look-ahead that adds that day's volume spikes to the benchmark) and showed
  +0.02% gross; the module uses prior-20-session turnover only.
- C has only one year of 15-minute index data; re-run when more history exists.
- Scalping was not tested: profits accrue to the fastest firms (CFTC co-authored E-mini
  study: aggressive HFT firms earned most, largely from small traders), which a 1-minute
  feed and ~0.16% intraday round trip cannot compete with.

## D. Buy before results, keep only if the reaction qualifies (2026-10-25)

`python -m tradedesk_lab.pre_results_hold` (research only). Same events, same 0.564% round
trip, returns net of the equal-weight liquid market. "Wait" is the live earnings-drift rule
(hold to the 20th close, no +10% target here); "pre-buy" buys ~9 sessions before the predicted
results day in every liquid stock, keeps qualifiers to the same exit and sells the rest at the
next open. Bar fixed beforehand: pre-buy must beat wait, clustered t > 2, both halves positive.

| | Rs 20 crore universe (9,993 events) | All liquid (17,076 events) |
| --- | --- | --- |
| Wait, per event | +0.02% | +0.02% |
| Pre-buy, per event | -0.64% (t -4.1) | -0.28% (t -1.8) |
| Pre-buy minus wait | -0.66% (t -4.5), both halves negative | -0.31% (t -2.0), both halves negative |
| Capital-days per event | 13.0 vs 2.2 | 12.4 vs 1.4 |

- Qualifying share is 10.9% (Rs 20 crore universe), 7.1% (all liquid).
- Pre-buy on non-qualifiers: -1.19% a trade (t -8.1; 15,861 events); 5th-percentile reaction -8.8%.
- On qualifiers pre-buy earns +10.8% more than wait, almost all of it the results jump itself,
  which cannot be known in advance. Run-up to results: +0.90% on qualifiers vs +0.25% on the rest.
- Wait here is +0.19% per qualifier, below the live rule's recorded +0.7%: this test needs a
  predicted results date, uses the 20th-close exit and different entry timing. The comparison
  is like for like; the level is not the live rule's.

Verdict: failed. The live rule stays; no pre-results buying.
