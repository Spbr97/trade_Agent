# Accuracy Milestone 7 protocol: setup-specific chronological stability

Status: preregistered before execution

Date: 2026-10-03

Protocol: `accuracy-setup-stability-v1`

## Frozen hypotheses

Primary, with precedence:

- Setup: `trend_pullback`.
- Entry: next exchange-session open after arming.
- Stop: 1.0 arming ATR.
- Target: 0.5R.
- Maximum hold: three sessions.

Secondary, evaluated only as a separately reported fallback:

- Setup: `base_breakout`.
- Entry: next exchange-session open after arming.
- Stop: 1.25 arming ATR.
- Target: 0.5R.
- Maximum hold: three sessions.

Both were chosen from already-inspected Milestone-6 development diagnostics. They
are hypotheses, not fresh evidence.

## Stage A: mechanism stability

Use only the first 80% development partition retained by Milestone 6. The final
4,848 rows remain externally locked. Divide the global development calendar into
four expanding-window test blocks after a train-only first block, with a ten-session
embargo before every test block.

A setup may proceed to the selector only with:

- at least 500 resolved development calls;
- at least 75% aggregate accuracy and 70% Wilson lower bound;
- non-negative aggregate after-cost expectancy;
- at least 70% accuracy in every chronological test fold; and
- non-negative expectancy in at least three of four folds.

This is a mechanism-stability gate, not the promotion gate.

## Stage B: one frozen selector

For a stable setup, fit one standardized L2 logistic model (`C=0.1`) using the 33
existing causal arming features. Produce predictions only from the four embargoed
walk-forward test blocks. Evaluate fixed thresholds 0.50 through 0.90 in 0.05 steps
and top 1/2/3 calls per active setup session.

The selector must still pass the unchanged accuracy policy: at least 80% observed
success, 70% Wilson lower bound, 70% successful-session rate, adequate sample and
session coverage, and non-negative expectancy. Primary has frozen nomination
precedence; secondary cannot displace a qualifying primary.

## Locked-tail rule

Only a fully qualified selector may open the final 4,848-row tail. Train once on all
development rows for that setup and evaluate only its frozen threshold/top-k on the
tail. A locked pass remains shadow research and does not change live configuration or
the canonical baseline automatically.
