# Frozen 50-stock AEM baseline checkpoint

Date: 24 September 2026

Branch: `codex/nse-anticipatory-validation`

Dataset: `ec539cf66bea4c18ba994506f85a52d6`

Universe plan: `1145cab2055949899159d8589d4f04f4`

## Outcome

The first broader replay of the unchanged AEM v1 contract **fails** the accuracy and
after-cost economics objectives. It is a historical development diagnostic and is
not eligible for live calls.

| Measure | Result |
|---|---:|
| Frozen stocks | 50 |
| Evaluation sessions | 120 |
| Candidate events | 1,397 |
| Trade decisions | 815 |
| Resolved fills | 693 |
| Strict wins | 149 |
| Strict success rate | 21.50% |
| 95% Wilson interval | 18.60%–24.71% |
| Mean net R per resolved fill | −0.27471R |
| Unfilled decisions | 122 |
| Unresolved decisions | 0 |
| Active sessions | 118/120 |
| Active sessions reaching at least 70% | 3.39% |
| Active sessions reaching at least 80% | 1.69% |

These figures do not support a 70–80% accuracy claim. The protocol remains fail
closed: no live eligibility assessment is made until matched-random, stress,
portfolio, out-of-sample and prospective evidence exists.

The earlier 37.74% result was a different, smaller population: ten stocks that
already had available M1 history. Only five overlap the frozen 50-stock cohort.
Those five reproduce exactly in both pipelines at **5/26 strict wins (19.23%)**,
including identical per-symbol fills and net R. The five old-only stocks contributed
**35/80 wins (43.75%)**, lifting the old aggregate; the broader cohort outside the
overlap produced **144/667 wins (21.59%)**. This supports population/availability
selection as the reason for the decline, not a reconstruction regression on the
observable overlap. A controlled experiment is still required before attributing
causality beyond that decomposition.

## Data foundation

- Collected 2,624,979 of 2,625,000 planned regular-session one-minute bars.
- Verified 6,999 of 7,000 stock-sessions as complete across 272 recorded requests.
- The sole gap is `NSE_3063` (VEDL), 30 April 2026, missing 09:15–09:35. A repeat
  request returned the same provider history. The 354 observed bars stay in the
  stage, and event reconstruction rejects that session as incomplete.
- The joined source fingerprint is
  `b07440d8a07686fe3b7d0f0071119c81ce5f04255a9428dc61470cfda85f83cf`.
- The dataset fingerprint is
  `7f30a4fa585e2f3763b2d4c3b356b6061134ef1861b4ac7da0942e774ae7742e`.

## Frozen accuracy protocol

Protocol `aem-accuracy-v1` is fingerprinted as
`8d377ca8733e159f9559ac361ca0ee0a3917f6b0f3f1bb55cf4a3c7aad3651bd`.
It registers top-one, top-two and top-three policies, at most three selected calls
per session, explicit zero-call availability, 70% and 80% session thresholds, an
80% pooled target, a 70% Wilson lower-bound target at the first prospective review,
and positive economics under registered execution-cost stress cases.

## Dashboard and safety

The existing research dashboard now has a read-only **Accuracy roadmap** view. It
loads compact collection, baseline and protocol data from the frozen manifest. It
does not change production scanning, ranking, risk, management, alerts or execution.
Its canonical scorecard is also the framework artifact: every later experiment must
report accuracy, uncertainty, session consistency, availability and net-R deltas
against this baseline before it can be described as an improvement.

Focused staged-data, protocol, dataset and dashboard verification passed 18 tests.
The full runnable repository suite passed **903 tests**, with two skips and one
explicitly deselected scikit-learn tuning test. Five additional SciPy-dependent
modules and that tuning test remain blocked by Windows Application Control; no
security policy or production dependency was changed.

## Next milestone

Run matched-random comparisons, registered cost/fill stresses and simultaneous
portfolio replay on this exact dataset. Use the failure breakdown to preregister a
bounded set of information improvements; do not tune against an unrecorded target.
