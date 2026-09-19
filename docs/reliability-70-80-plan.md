# Plan: 70–80% successful calls with measurable call availability

Prepared 16 September 2026. Status: proposal, supported by a read-only code and artifact audit. No implementation, retraining, policy changes, or promotion is performed by this document.

## 1. Objective and what would count as success

The user's objective is useful calls with at least 70–80% success. Call volume is flexible: “Anything that works.” Keep the integrated dashboard. Develop subsequent implementation on a new branch; preserve existing management code and risk limits. Extend the existing lab through its `/lab` mount where possible.

Use 80% observed success as the engineering acceptance target, with uncertainty and call availability reported alongside it. Seventy percent is an evidence floor to investigate, not permission to lower the existing production gate, which already requires 80% win rate. An “80% predicted probability” is not proof of 80% observed success.

Success in every session is a stronger requirement than success over many calls. Even with independent calls that each succeed 80% of the time, winning all three calls in a three-call session occurs only 51.2% of the time. Three calls cannot produce a 70–80% session result: the possibilities are 0%, 33.3%, 66.7%, and 100%. This example illustrates sampling variability; real calls may also be correlated. No model or agent can honestly promise a minimum success rate in every future session.

We will measure the user's session objective directly: the fraction of sessions delivering calls, the fraction of mature session cohorts achieving at least 70% and 80%, and the worst periods. Failure to produce enough useful calls remains a failed product outcome, even if a tiny selected sample looks accurate.

The intended holding period remains unresolved. Maintain two explicit research tracks:

- **Same-session:** entry and exit within the exchange session, with a fixed last-entry time and exit deadline. This is the direct test if “each session” means results by that session's close.
- **Swing:** calls issued in a session, with results attributed back to that session once the declared holding window finishes. The current system allows ten subsequent sessions; those results must never be presented as same-day accuracy.

Choose the primary track before registering a confirmatory experiment. Results from the two tracks cannot be pooled to satisfy one target.

## 2. What the current evidence says

The frozen lab run is `a5f42cd2f0784b59b96683395cad801a`. It contains 29,964 historical candidates across 1,527 symbols. Its already-researched holdout contains 6,657 candidates, about 26.5% target hits, and mean simulated candidate net return of approximately −0.579R. Logistic, XGBoost, and CatBoost are already implemented. Their holdout AUCs are about 0.571, 0.546, and 0.565. All final filters selected zero calls; there is no champion.

These are legacy diagnostic numbers, not a clean acceptance baseline. The audit found economically invalid target geometry in the training/evaluation inputs:

- `data/reports/barrier_signals.csv:23311` records entry 112.66, stop 112.56, and T1 112.62. Reaching that target is not a profitable long trade.
- `tradedesk_lab/dataset.py` rejects entry at/below stop but does not reject target at/below entry. The shared barrier labeler also lacks that geometry check.
- A post-hoc diagnostic subset with logistic probability at least 0.90 contains 85 candidates and 83 target-hit labels, yet all 85 have negative simulated candidate net returns. This is an artifact diagnostic, not an investable strategy or portfolio result.

Further gaps affect the reliability claim:

| Current behavior | Required treatment in the new layer |
|---|---|
| Legacy tracker grades next-day bars without first establishing a trigger/fill | Require an executable entry before grading a trade |
| EOD learner targets positive gross return | Separate gross return, target success, and net success |
| EOD test net return averages all test rows | Measure the calls selected by the frozen policy |
| EOD review path supplies fixed 50% win rate and no random baseline | Supply measured evidence through a new reviewed adapter; retain the gate |
| Forward collector admits later watchlist dates without a scoring deadline | Scores must exist before the earliest eligible entry; missed deadlines are retrospective |
| Collector reads `latest.json` before checking cohort identity | Load the activation-pinned run so a new experiment cannot halt an older cohort |
| Forward report contains gross return only | Add modeled fills, costs, net returns, and session-level outcomes |
| Forward selection is always false in current code | Add an explicit, validated research selection policy before expecting selected calls |

The intraday store has two years of data for five liquid NSE names across 1/3/5/15/60-minute intervals, ending 11 September. It supports prototype work but requires freshness and wider coverage checks. Existing risk limits permit at most three new entries per day.

Previously tested daily breakout strategies, RSI2 variants, and VWAP Reclaim have not established the required edge. Support/Resistance Reversal and Breakout+Retest are documented as unattempted. Historical negative results guide research; they do not prove that all possible strategies or filters are incapable of working.

## 3. Phase A — make success auditable

**Deliverable:** a versioned call/outcome contract and a rebuilt diagnostic dataset.

Each call must preserve issue time, market, symbol, strategy/model version, source-data availability time, feature/config hashes, trigger, stop, target, entry deadline, exit deadline, and predicted probability. Outcomes are appended separately. Never alter the original prediction to match later evidence.

For the proposed strict primary metric, a success requires all of:

1. A valid, executable entry after the prediction was issued.
2. The predeclared target reached before the stop and deadline.
3. Positive modeled return after applicable fees and slippage.

For a long trade require `stop < actual entry < target`, sensible tick/stop geometry, and cost viability. Gap entries must be reevaluated at their actual fill. Positive timeout exits are separately reported as net-profitable exits; they do not become target successes. Untriggered candidates are neither wins nor losses, but remain visible in issuance, trigger, expiry, and availability counts. Filled failures, timeouts, and scratches remain in the primary denominator. Missing data remain unresolved and block a clean assessment; they cannot disappear from the ledger.

Audit the complete dataset, not only the discovered examples: invalid instruments, illiquidity, tiny risk distances, splits/bonuses, duplicate symbols across exchanges, finalized candles, actual trading calendars, and information availability at prediction time. For events, event date alone does not establish when that event became known. Rebuild the current feature contract from source; the legacy export and current feature set differ.

Simulate only price action after a fill. Use finer bars where needed to establish ordering; handle ambiguous bars conservatively and disclose how many there are. Validate entry/exit behavior against the existing execution rules, including time stops, rather than equating every barrier label to a realized trade.

Reuse the existing cost and risk engines, verifying that calculations reflect the chosen market, entry/exit times, and actual exit policy. For additional markets, distinguish permanent trading charges from tax withholding/cash-flow effects. Report both per-candidate outcomes and the executable portfolio under position/sector/heat limits.

Implement these checks in the lab first, preserving original artifacts with an audit status. Any eventual shared-code correction gets its own scoped review and regression tests.

## 4. Phase B — find entry opportunities that merit modeling

**Deliverable:** a comparison of accuracy, net returns, and call availability for a small, preregistered set of strategies.

Start with NSE because its execution infrastructure is the most developed. Keep BSE and crypto as separate experiments with separate evidence; a success on one market does not validate another. Avoid counting the same underlying stock on NSE and BSE as independent evidence.

For the same-session track, prototype the two unattempted candidates using existing intraday infrastructure:

- Support/resistance reversal: levels identified before the setup, rejection plus a later confirmation, and explicit trend/volatility conditions.
- Breakout followed by retest: breakout, a later retest that holds, then continuation. All three events must occur in order using completed bars.

These are hypotheses, not claims that either has an edge. Include existing setups and matched random-entry controls as baselines. Repeat previously failed ideas only with a materially new hypothesis or genuinely new data, recorded before testing.

Refresh and verify the five-name prototype dataset, then expand to a defined liquid universe with point-in-time membership, adequate history, and a known API/time budget. Wider symbol coverage and different market regimes are required before extrapolating a five-name result.

For swing research, first reevaluate the existing candidates after repairing labels and current features. Open a new hypothesis only if diagnostics support it. Keep targets and exits fixed within an experiment. Any proposed new target/stop policy is a separate strategy with its own validation; do not shrink targets, widen losses, extend deadlines, or increase capital risk merely to print a higher percentage.

## 5. Phase C — compare models and select calls

**Deliverable:** a reproducible model comparison at useful operating points.

| Component | Role and acceptance condition |
|---|---|
| Regularized logistic model | Transparent baseline; establishes whether the features contain usable information |
| XGBoost and CatBoost | Nonlinear challengers; keep only if they improve future selected-call performance |
| Regime/setup specialist | Tests whether a strategy works conditionally; use shared models where subgroup samples are too small |
| Second-stage call selector | Estimates whether a candidate will meet the exact success contract; trained on predictions made outside training folds |
| Time-based calibration | Checks whether predicted 0.80 calls actually succeed around 80% in later data |
| Ensemble | Retained only if it improves results; disagreement and correlated errors are measured |

Select by precision, net expectancy, and availability together. Evaluate the highest-ranked one, two, and three qualifying calls per session under existing limits. A rank alone does not qualify a call. Publish the entire accuracy-versus-coverage curve and the zero-call-session rate. A configuration selecting nothing fails the availability objective.

A fourth tree family or a temporal neural network is a bounded challenger, justified by better input information and adequate independent sessions. Neural networks, model agreement, or extra agents are not evidence of accuracy by themselves. Start with the models already installed; choose additional software/data spending only after identifying a specific missing capability.

Research agents can divide data-quality checks, hypothesis implementation, and independent validation. A separate reviewer reproduces the candidate's scorecard. Language-model commentary remains advisory; deterministic code establishes prices, triggers, size, and outcomes. Several agents agreeing on the same information is not independent market evidence.

## 6. Phase D — validate without recycling the answer

**Deliverable:** a locked selection policy and a full record of successful and failed experiments.

- Split chronologically by trading sessions. Purge overlapping outcome intervals, apply an appropriate embargo, and fit feature transformations, calibration, and selector thresholds within training/development data.
- Group related calls by date and underlying instrument. Report sensitivity across symbols, sectors, volatility, and trend/range regimes. Five hundred highly correlated calls do not supply five hundred independent observations.
- Use nested walk-forward evaluation. CPCV and overfitting diagnostics are supplementary stress checks, not replacements for future observations.
- Predeclare an initial budget of at most 24 pipeline specifications per track. Record every feature, strategy, model, threshold, and hyperparameter trial, including nested fits. Expand the budget only with a documented new hypothesis and a fresh evaluation protocol.
- Treat the previously inspected 2023–2026 windows as development/diagnostic history. Rebuilding labels does not make the old holdout unseen again.
- Compare against matched random timing under the same universe, market conditions, fill rules, and costs. Stress spreads, slippage, missed fills, and latency at preregistered levels, including a doubled-slippage scenario.
- Freeze one candidate policy before prospective evaluation. Save prediction-time timestamps and versions. Missed scoring deadlines are explicitly retrospective, even when features can be reconstructed from old bars.

The report must state target-hit rate, strict net-success rate, net-profitable exit rate, average win/loss, net expectancy, drawdown, concentration, call counts, zero-call sessions, and session success distribution. Show aggregate and rolling windows without deleting losing sessions or mixing model versions.

## 7. Phase E — earn the reliability claim

**Deliverable:** prospective paper calls visible in the existing lab, followed by a promotion report if the evidence qualifies.

The existing setup gate remains binding: 500 resolved trades, 100 out-of-sample trades, 80% measured win rate, positive net expectancy, and at least +0.10R versus matched random timing. Those are existing minima, not a guarantee. Five hundred total trades is not necessarily a requirement for five hundred new live trades; historical, OOS, and prospective counts must be shown separately.

Add a predeclared uncertainty requirement. An illustrative first research milestone is at least 100 resolved prospective selected calls, an observed strict success rate of at least 80%, and a lower confidence bound of at least 70%, across a minimum of 30 distinct sessions. This is a research review point, not automatic production permission.

For illustration, the lower end of a two-sided 95% Wilson interval is about 71.1% for 80 wins out of 100, and 76.3% for 400 out of 500. To support a claim closer to an 80% lower bound requires stronger observations: 420/500 gives about 80.5% under the independent-binomial calculation. Session clustering can widen uncertainty; use session/week block resampling as an additional requirement. These calculations do not promise future performance.

Evaluate at preregistered sample/session checkpoints. Repeatedly looking until a threshold passes creates another selection problem; repeated promotion tests need a sequential-testing or multiple-testing adjustment. The dashboard may update continuously without every refresh becoming a new statistical test.

The primary acceptance outcome is the intersection of credible accuracy, positive cost-stressed economics, and demonstrated availability. If no operating point meets all three, report that no qualifying solution has been found. A reliable abstention mechanism is useful infrastructure, but an always-empty model has not delivered the user's requested calls.

## 8. Phase F — keep learning with an auditable record

Keep the deployed evaluation model fixed for its cohort. Challengers may train on fully resolved past observations, but receive separate versions and new future predictions. Once observations are used to tune a challenger, they are development data for that challenger and cannot also certify it as unseen evidence. Previous model scores and outcomes remain immutable.

Add durable collection, a single-instance guard, restart recovery, freshness/late-scoring warnings, activation-pinned artifacts, and feature/config version checks. A stalled process must be visible. The integrated lab should distinguish historical diagnostics, prospective paper calls, and production calls, and replace stale base-file verification claims with a current, clearly dated integration audit.

Use predetermined drift checks on calibration, net expectancy, costs, and data quality. A challenger replaces a model only after its independent evaluation qualifies. The system does not lower an accuracy threshold to meet a daily quota.

## 9. Delivery sequence and timing

These are engineering effort estimates, conditional on data access and the size of the audit findings. They are not dates by which a profitable model will exist.

| Stage | Indicative effort | Reviewable output |
|---|---|---|
| A: contract, label/fill audit, prospective integrity | 2–4 working days | Clean baseline, exclusions ledger, consistent success definitions |
| B: two strategy prototypes and data verification | 3–7 working days, plus data acquisition | Strategy/control results with call counts |
| C–D: bounded model race and temporal validation | 3–5 working days after data is ready | Accuracy/availability curves and a freeze-or-reject decision |
| E: prospective observation | Determined by eligible calls and label maturity | Timestamped calls and real later outcomes |
| F: durable operation and model change controls | Built alongside A–D | Restart-tested collection and versioned scorecards |

At an actual average of 1–3 selected filled calls per trading session, 100 resolved calls require approximately 34–100 sessions, about 7–20 trading weeks, plus any swing outcome lag. Per-strategy evidence can take longer. The current qualified-call rate is zero, so no reliable calendar forecast exists yet. Initial outcomes can arrive much earlier; they are not proof of sustained 70–80% reliability. Earlier estimates of one to several weeks were appropriate only for an initial indication, not certification of this target.

The first implementation package should be Phase A and collection integrity, with failing-case regression tests for target below entry, missing fills, post-deadline scoring, pinned model versions, and after-cost outcomes. This creates the trustworthy measuring system needed to judge every later strategy and model.

## Sources and audit anchors

- Local evidence: `data/m14_m18/runs/a5f42cd2f0784b59b96683395cad801a/report.json`, adjacent `predictions.json`, `data/reports/barrier_signals.csv`, `config/setups.yaml`, `config/risk.yaml`.
- Implementation audit: `tradedesk_lab/dataset.py`, `tradedesk_lab/forward.py`, `src/tradedesk/prediction/labeling.py`, `src/tradedesk/signal_tracker.py`, `src/tradedesk/eod_learning.py`, `src/tradedesk/engine/scoring.py`.
- The accuracy/coverage tradeoff is formalized by [Deep Gamblers: Learning to Abstain](https://proceedings.neurips.cc/paper/2019/hash/0c4b1eeb45c90b52bfb9d07943d855ab-Abstract.html). Its general classification results do not establish a trading edge.
- Confidence-bound methodology: [NIST: confidence intervals for proportions](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
- Selection-bias controls: [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) and [The Deflated Sharpe Ratio](https://doi.org/10.2139/ssrn.2460551).
- Model-family choice should be tested empirically. [When Do Neural Nets Outperform Boosted Trees on Tabular Data?](https://proceedings.neurips.cc/paper_files/paper/2023/hash/f06d5ebd4ff40b40dd97e30cee632123-Abstract.html) finds no universal winner; its benchmarks are not proof of financial predictability.
