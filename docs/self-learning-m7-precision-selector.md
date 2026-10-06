# Self-learning Milestone 7 checkpoint — precision selector

Completed: 2026-10-06

The precision selector is a research-only second stage inside each sealed challenger
experiment. It cannot create a call or upgrade a rejected/shadow call. Its only authority
is to reject or rank calls that were already `qualified_call` records.

## Frozen gates

The selector fails closed when any mandatory input is unavailable. It checks:

- calibrated challenger probability;
- original rule score;
- relative-strength percentile;
- market-specific volatility range;
- positive turnover and estimated cost as a fraction of position value;
- positive remaining net reward at the first target; and
- recurrent setup/regime/sector/confidence failure patterns learned on development data.

The ranking score also uses the scanner's sealed trend, sector, pattern/volume, room to
resistance, net-reward and regime components when present. Those optional components can
improve ordering but cannot erase a rejection reason.

A historical failure pattern is blacklisted only after at least five development outcomes
with a failure rate of 60% or more. It is fitted before the locked tail is inspected.

## Preregistered operating points

Every challenger evaluates exactly six policies on its one-use chronological locked tail:

- top 1, top 3 and top 5 surviving calls per session; and
- calibrated-confidence floors of 60%, 70% and 80%.

For each policy the report includes strict accuracy, 95% Wilson lower bound, mean net R,
coverage of qualified calls, calls per session, no-call frequency and maximum losing
streak. Passing the numerical accuracy gate is only a report field; it never grants live
authority. `selected_live_policy` stays `null`, `active_model_changed` stays false and
`promotion_authorized` stays false.

## Remaining gaps

The sealed ledger does not yet contain an explicit sector-regime classifier or a
decision-time gap-risk feature. Those gates remain unchecked rather than being guessed.
There are also no mature sealed live rows yet, so this implementation has not raised any
real NSE, BSE or crypto baseline percentage. The dashboard therefore shows the selector as
not evaluated until a genuine market-specific challenger can run.
