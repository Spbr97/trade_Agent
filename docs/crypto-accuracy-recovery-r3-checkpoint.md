# Crypto accuracy recovery R3 checkpoint

## Status

R3 is an integrated, research-only accuracy experiment. It evaluates smaller targets and shorter holds without changing the crypto baseline, live-call authority, or existing C1/C2 evidence.

The verified run `20261005T103445-508246e5-e437741a` evaluated 64 trials: four setup families multiplied by 16 frozen exit geometries, using 522 tracker signals (461 backfill and 61 explicitly marked live). No setup produced a candidate that cleared the accuracy, uncertainty, sample-size, fold-stability, and net/after-tax expectancy gates. This is a valid rejection, not an unavailable result and not a pass.

The strongest diagnostic by development and walk-forward ranking was `nr7_breakout` with a 0.75R target and seven-session maximum hold:

- development: 82/337 wins, 24.33% accuracy, 20.06% Wilson lower bound, -1.030 mean net R, and -0.779 mean after-tax R;
- walk-forward: 43/206 wins, 20.87% accuracy, 15.88% Wilson lower bound, -1.113 mean net R, -0.842 mean after-tax R, and zero positive folds;
- separate live retrospective cohort: 4/47 wins, 8.51% accuracy, 3.36% Wilson lower bound, -1.647 mean net R, and -1.019 mean after-tax R.

The live cohort did not select or rank this diagnostic; it was evaluated only after the backfill selection. Every accuracy and expectancy gate remained false except the development, walk-forward, and live sample-size gates.

## What the result means

Smaller and quicker targets alone did not repair the crypto baseline. Some small targets can increase the raw win count while still losing after stops, fees, slippage, and tax. Optimizing the displayed percentage without positive expectancy would make the agent less reliable, so those candidates are rejected.

The R3 dashboard panel is diagnostic only. It reports the registered trial count, setup and geometry under inspection, walk-forward metrics, and the separate live-cohort metrics. It always reports baseline improvement and live eligibility as false.

## Next accuracy experiment

Move from exit-only search to a precision-first meta-selector:

1. Build causal features available at arming time only.
2. Train and calibrate within purged chronological folds, separately by setup.
3. Permit abstention so the selector can reject weak calls.
4. Require minimum call volume, Wilson support, positive net R, positive after-tax R, and stability across folds and regimes.
5. Preserve a completely untouched prospective cohort before any baseline claim.
6. Do not train on C1 outcomes until those labels are mature.

R3 remains useful as the frozen exit layer for that later selector; its failed candidates must not be promoted or silently retuned.
