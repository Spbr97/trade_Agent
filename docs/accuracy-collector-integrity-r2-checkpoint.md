# Accuracy collector integrity R2 — ordered, fail-closed evidence collection

Implemented: 5 October 2026

Status: **engineering and scheduler migration verified; fresh evidence still collecting;
no baseline change**

## Scope and decision

R2 changes evidence collection, not the prediction hypothesis. It does not change a signal,
feature, model, threshold, setup, quick-profit geometry, alert, risk rule, management path,
broker action or order path. NSE, BSE and crypto remain three independent evidence streams.

The audit found that adding more accuracy experiments would be premature because unattended
collection could silently miss dependencies:

- equity catch-up jobs started together and collided on INDstocks token generation; the
  observed broker response was “Please wait a minute before requesting another token”;
- the BSE tracker was scheduled before its required BSE load and scan, and the derived B1
  hook could fail to import after the base tracker had already succeeded;
- a missing entire NSE watchlist session was invisible to M9;
- a later BSE catch-up row could carry an old `armed_on` and be mistaken for prospective;
- crypto C2 terminal reuse trusted mutable state; and
- caught derived-refresh errors could still leave Task Scheduler with result 0.

R2 fixes those evidence-integrity failures before any new model or selector is allowed.

## Registered market pipelines

Each market now has one dependency-ordered command:

| Market | Registered order | Failure contract |
|---|---|---|
| NSE | data load → benchmark load → optional paper update → scan → strict M8–M11/qualification → optional drift/lab/research | a required failure skips dependents and exits nonzero |
| BSE | data load → scan → strict signal tracker → strict research/B1 | a missing watchlist or B1 failure exits nonzero |
| Crypto | data load → strict signal/timing/C1/C2 | missing closed coverage, source failure or blocked C2 exits nonzero |

NSE and BSE share one OS-released equity pipeline lock. Crypto has an independent lock.
The INDstocks token provider also has a cross-process lock and re-reads shared credentials
after acquiring it. Concurrent 401 refreshes adopt the first replacement instead of deleting
it and generating a second token.

Every run writes a hash-bound per-market receipt with stage return codes, bounded output
tails, source session and failure stage. Critical failure produces `failed`; supplementary
failure produces `degraded`. A crashed `running` receipt becomes stale after six hours.
Completed NSE/BSE receipts expire after 120 hours and crypto receipts after two hours.
Receipt integrity or freshness is operational evidence only—never an accuracy pass,
baseline improvement or live authority.

## Accuracy-evidence hardening

### NSE

M9 now derives expected post-activation sessions from stored benchmark bars. An absent
whole watchlist fails as `missing_watchlist_session`; a catch-up generated after M8's frozen
next-entry deadline fails as `late_watchlist_registration`. Only a timely, present
zero-candidate watchlist is a valid zero-call session. Strict collection also fails on M8
current errors, degraded M9 integrity/stress, degraded M10/M11, or unavailable/degraded
atomic qualification.

### BSE

B1 protocol v2 requires a unique non-empty call ID and same-IST-session durable registration.
Late, missing or duplicate registration is excluded and counted, never relabeled as a loss
or development result. The forward sampler now uses a stable SHA-256-derived seed and
enforces its per-rule/session cap across reruns. This amendment occurred before any eligible
prospective B1 result; geometry and pass gates are unchanged.

### Crypto

C2 publishes its first complete result through an OS-serialized, atomically replaced
`terminal-first-look.json`. The envelope is accepted only when the canonical report, exact
immutable run manifest, C2 artifacts, implementation, pinned C1 sources, crypto-only scope,
mature pair readiness, and complete 12/12/0 denominator all verify. Mutable `state.json`
cannot grant terminal authority. The dashboard uses this same authoritative verifier.

## Dashboard behavior

The existing dashboard now shows separate collector health and collected source session
for NSE, BSE and crypto. Hash failure, missing state or expired freshness displays
`not available — not a pass`. A fresh `completed` receipt means only that its registered
stages ran; performance percentages continue to come only from each market's own evaluated
outcomes.

## Scheduler deployment

The migration preflighted all six tasks, refused mutation while a task was running, backed
up every XML definition before the first change, then read the authoritative task state back:

- enabled: `tradedesk-after-close` (PT4H), `tradedesk-bse-after-close` (PT4H), and
  `tradedesk-crypto-tracker` (PT2H), all preserving `IgnoreNew` and `StartWhenAvailable`;
- disabled as redundant: `tradedesk-research-tracker`, `tradedesk-bse-tracker`, and
  `tradedesk-bse-research-tracker`; and
- recoverable backup:
  `data/scheduler-backups/accuracy-r2-20261005-054026/` with six XML files.

Triggers and principals were preserved. No NSE or BSE after-close task was run intraday as
part of deployment.

## Evidence status

| Market | Valid performance evidence after R2 | Baseline/live authority |
|---|---|---|
| NSE | canonical 149/693 = 21.50%; locked 186/223 = 83.41% remains historical-only; fresh prospective 0/4 ready at the prior checkpoint | unchanged / false |
| BSE | prospective accuracy unavailable; 0/30 sessions and 0/4 rules ready at the prior checkpoint; 64.52% best transport result remains development-only and -0.126R | unavailable / false |
| Crypto | verified scheduled run `20261005T060002-b67671f3b4`: 339/339 active pairs had the closed 4 October session; C1 remains at 2/30 minimum pair sessions; C2 remains 0/12 evaluated | unavailable / false |

R2 improves the probability that the next rows are valid evidence. It does **not** improve
any baseline percentage by itself.

## Verification

- 116 focused broker, tracker, pipeline, dashboard, NSE/BSE/crypto integrity and terminal
  tests passed after the final hardening review.
- Scoped Ruff, Python compilation, PowerShell preview and Git whitespace checks passed.
- The Task Scheduler migration and all six recoverable backups were independently read back.
- The migrated crypto task completed both critical stages with result 0; its latest and
  immutable run receipts passed hash, identity and freshness verification. It detected three
  signals, zero tradeable calls and added no duplicate call on the repeated source session.
- The wider suites retain two unrelated pre-existing failures in unchanged files: the frozen
  SSM evidence records an older CLI checksum, and one replacement-search retest expectation
  disagrees with the current implementation. Neither frozen evidence nor replacement-search
  code was rewritten as part of this collector-integrity checkpoint.

## Next accuracy action

Let the registered pipelines collect fresh source sessions. Do not add C3, a new BSE
geometry, or another NSE selector until the current frozen evidence reaches its next valid
decision boundary. A collector failure should be repaired and rerun; it must never be
counted as a failed call, a zero-call session, or a performance pass.
