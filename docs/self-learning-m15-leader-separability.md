# Milestone 15 checkpoint — leader separability

Run date: 2026-10-09

Status: complete and rejected for all three markets. The experiment found historical
future-high ranking information, but it did not produce an accurate or profitable
executable call policy. No active model, live call, management rule or reported baseline
was changed.

Protocol:
[frozen M15 leader-separability protocol](self-learning-m15-leader-separability-protocol.md)

## Locked result

Each market used its own latest 180 mature sessions with a 102-session training block,
three-session purge, 36-session validation block, second three-session purge and one
36-session locked test. Markets were never pooled. Validation selected the bounded random
forest for all three markets before the locked block was scored.

| Evidence | NSE | BSE | Crypto |
|---|---:|---:|---:|
| Causal dataset rows | 196,095 | 189,496 | 1,976 |
| Validation top-1 leader precision | 19.44% | 22.22% | 38.89% |
| Locked top-1 leader precision | 41.67% (15/36) | 52.78% (19/36) | 30.56% (11/36) |
| Locked top-3 leader precision | 37.96% (41/108) | 37.04% (40/108) | 25.00% (27/108) |
| Same-session leader prevalence | 5.06% | 5.05% | 7.28% |
| Top-1 lift over prevalence | 8.24x | 10.44x | 4.20x |
| Momentum top-1 precision | 44.44% | 41.67% | 11.11% |
| Random opportunity tail | 0.0005 | 0.0005 | 0.0005 |
| Executable top-1 strict accuracy | 33.33% (12/36) | 33.33% (12/36) | 41.67% (15/36) |
| Executable top-1 mean net R | -0.580R | -0.610R | -0.164R |
| Matched-random mean net R | -0.397R | -0.399R | -0.218R |
| Net-R advantage over random | -0.183R | -0.212R | +0.054R |
| Executable net-R random tail | 0.9140 | 0.9425 | 0.2909 |
| Frozen gates passed | 6/12 | 7/12 | 8/12 |
| Decision | rejected | rejected | rejected |

The result artifacts are content-addressed and registered under these experiment IDs:

- NSE: `8ef8ca64a7230adf1d6bd3521ab7fce379a085896dcd1a71af2779ff8fbe10a2`
- BSE: `1e4132a4613b6670a35dd1ea60d3ce8a5c26449409c2d3b343e8a05fa49beb04`
- Crypto: `ae2243fce1da553fb4a6e00fcc34ee16a0e9c08e11d4240ebea0bc2fe2c3bbea`

## Gate diagnosis

All markets produced exactly 36 primary selections, beat random leader prevalence by at
least 3x, cleared the frozen top-one and top-three opportunity thresholds, and had an
opportunity random tail below 0.05.

The gates that prevent any promotion are more important:

- No market reached 50% executable strict accuracy.
- No market produced positive mean net R.
- No market beat matched-random execution by the required +0.10R.
- No market had a significant executable net-R random tail.
- NSE did not beat simple momentum top-one by ten percentage points.
- NSE and BSE had validation-to-test top-one gaps above twenty percentage points.

Path outcomes expose the mismatch. NSE recorded 23 stops, 12 targets including one gap
target, and one timeout. BSE recorded 23 stops, 12 targets and one timeout. Crypto recorded
eight stops, 15 targets and 13 timeouts. A label based on the maximum future high can be
rankable even when the entry-to-exit path stops first, fails to reach the executable target,
or loses after costs.

## What this changes

The useful finding is narrow: the M14 causal features contain information about later
high-price opportunities. That is not evidence that the current entry/stop/target contract
is tradeable, and it is not a baseline-accuracy improvement. The M15 bundles remain
research artifacts and have no runtime authority.

The next accuracy checkpoint must change the learning target rather than tune these models
against an exposed test:

1. Build a market-specific, causal dataset whose labels are the frozen executable path
   outcomes and net R after costs—not future maximum-high membership.
2. Separate opportunity discovery from tradeability: stage one may rank potential moves;
   stage two must estimate target-before-stop probability and expected net R.
3. Learn a no-call threshold on development data so accuracy is optimized subject to a
   disclosed minimum call count; never force one call per session.
4. Treat all M15 locked sessions as consumed diagnostic evidence. They may inform the new
   hypothesis but cannot qualify it.
5. Freeze the new contract before evaluation, use purged chronological development checks,
   then require a fresh prospective market-specific cohort for any improvement claim.

Until that checkpoint passes, the honest baseline and live agent behaviour remain exactly
as they were before M15.
