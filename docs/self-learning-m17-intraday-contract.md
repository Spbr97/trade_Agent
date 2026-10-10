# Milestone 17 checkpoint — intraday execution-contract feasibility

Completed: 2026-10-10 10:45 IST

Status: implemented and evaluated. NSE/BSE were unavailable at the preregistered path
coverage gate; crypto reached a terminal `development_rejected` result. No prospective
observer, active-model change, live authority or baseline-improvement claim was created.

## What M17 tested

M17 ranked the complete causal liquid universe before inspecting intraday availability,
sealed the primary, momentum and matched-random candidates, and then acquired only those
M1/M5/M15 paths. Six fixed quick-profit contracts combined three entry rules with 0.50R
and 0.75R targets. Invalid paths never entered a performance denominator.

The base protocol and two pre-outcome amendments are immutable:

- [Frozen base protocol](self-learning-m17-intraday-contract-protocol.md)
- [Amendment 1 — exact smaller random sets](self-learning-m17-intraday-contract-amendment-1.md)
- [Amendment 2 — crypto M1-authoritative interval contract](self-learning-m17-intraday-contract-amendment-2.md)

## Acquisition and readiness

| Market | Sealed candidate paths | Acquisition | Validation primary paths | Diagnostic primary paths | Outcome race |
| --- | ---: | --- | ---: | ---: | --- |
| NSE | 1,704 | 120/216 request groups complete; obsolete/unavailable codes retained as partial | 33/36 | 30/36 | Not opened |
| BSE | 1,706 | 216/216 complete | 24/36 | 25/36 | Not opened |
| Crypto | 872 | 216/216 complete; zero errors | 36/36 | 36/36 | Opened once |

Eight of nine invalid NSE primary paths and all 23 invalid BSE primary paths contained
zero M1 bars for the complete sealed session. The remaining NSE path lacked 13 closing
minutes. They were not substituted, synthesized or counted as losses.

CoinDCX observed M1 and M15 histories were not lossless resolutions of one stream: across
872 sealed paths only 245 had exact M15 closes, 57 exact volumes and 21 satisfied all
audited invariants. Before any outcome replay, amendment 2 made exact observed M1 the
crypto execution source, derived M5/M15 from it and retained observed M15 disagreement in
each path artifact. NSE/BSE rules were not changed.

## Crypto validation result

All figures are after configured slippage, fees, GST and sell-side TDS.

| Frozen contract | Fills | Strict wins | Accuracy | Wilson 95% lower | Mean net R | No-call rate | Eligible |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Prior-close pullback, 0.50R | 3 | 2 | 66.67% | 20.77% | +0.125R | 91.67% | No |
| Prior-close pullback, 0.75R | 3 | 1 | 33.33% | 6.15% | −0.319R | 91.67% | No |
| Opening-range breakout, 0.50R | 33 | 10 | 30.30% | 17.38% | −0.369R | 8.33% | No |
| Opening-range breakout, 0.75R | 33 | 9 | 27.27% | 15.07% | −0.350R | 8.33% | No |
| Next open, 0.50R | 36 | 12 | 33.33% | 20.21% | −0.340R | 0.00% | No |
| Next open, 0.75R | 36 | 11 | 30.56% | 18.00% | −0.270R | 0.00% | No |

The 66.67% headline is not an acceptable accuracy result: it is two wins from only three
fills and abstains on 33 of 36 sessions. It fails the frozen 24-fill, 24-session and
one-third maximum no-call requirements. Every contract with usable volume lost money
after costs. Therefore no validation contract was selected and the diagnostic block was
not opened.

## Decision

- `baseline_accuracy_improved = false`
- `active_model_changed = false`
- `eligible_for_live = false`
- prospective registration: none
- live authority: `NONE — RESEARCH ONLY`

M17 supplies useful negative evidence: smaller targets and intraday ordering alone do not
repair the selector. The next experiment should change causal selection and abstention,
not reopen these six exits or report the sparse pullback percentage as a success.

## Next accuracy checkpoint

M18 should be a selection-first intraday experiment with a newly frozen protocol. It must
use separate chronological development data, train only on causal pre-entry features and
after-cost M1 outcomes, preserve market separation, compare against momentum and matched
random controls, and retain minimum call/session requirements. M17's validation evidence
is consumed development evidence and cannot become M18's locked proof.
