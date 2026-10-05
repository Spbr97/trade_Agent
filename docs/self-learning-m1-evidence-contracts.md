# Self-learning Milestone 1 — evidence and outcome contracts

Completed: 2026-10-06

## Outcome

Every tracker record now exposes two independent, explicit classifications:

- Recommendation authority: `qualified_call`, `shadow_call`, or `rejected_call`.
- Outcome lifecycle: `pending_call`, `resolved_call`, `invalid_call`, or
  `never_triggered`.

This prevents a pending or unavailable row from being counted as a result and prevents a
shadow or rejected research observation from appearing as a personal trading call.
Only known mature price outcomes are eligible for scoreboards or learning inputs.

## Frozen contracts

Three immutable, versioned contracts are registered in `tradedesk.evidence`:

| Version | Kind | Target | Entry validity | Maximum hold | Same-bar rule |
| --- | --- | ---: | ---: | ---: | --- |
| `legacy-t1-tracker-v1` | legacy | historical | historical | historical | stop first |
| `quick-profit-v1` | quick profit | 0.75R | 3 sessions | 3 sessions | stop first |
| `swing-v1` | swing | 2.0R | 3 sessions | 10 sessions | stop first |

Quick-profit and swing success additionally require a positive net result after costs.
They are definitions only at this milestone. Existing and newly logged tracker calls stay
on the legacy contract until Milestone 3 implements and tests the deterministic resolver.
This avoids claiming that historical outcomes followed rules which did not yet exist.

## Compatibility and separation

- Historical JSONL records are enriched in memory and remain stored as legacy evidence.
- Existing call/result rows are not deleted or silently rewritten.
- A market stored on a row must match its dedicated NSE, BSE, or crypto log; mismatches
  fail closed.
- Unknown evidence classes, contract versions, contract-kind mismatches, and unknown
  outcomes fail closed rather than being counted as successes.
- Dashboard call tables show evidence class and contract kind.
- Pending, resolved, invalid, and never-triggered rows use lifecycle-aware filtering.
- Invalid and never-triggered rows remain visible but are excluded from learning scores.

## Audit of the existing logs

The compatibility layer loaded all current logs without rewriting them:

| Market | Rows | Evidence classification | Lifecycle classification |
| --- | ---: | --- | --- |
| NSE | 503 | 388 rejected, 115 shadow | 388 resolved, 115 pending |
| BSE | 1,761 | 749 qualified, 1,012 rejected | 1,761 resolved |
| Crypto | 522 | 113 qualified, 14 shadow, 395 rejected | 513 resolved, 9 pending |

All 2,786 historical rows remain on `legacy-t1-tracker-v1`. These counts describe data
availability and classification only; they are not an accuracy claim.

## Verification gate

- Evidence/category unit tests cover classification, lifecycle, contract immutability,
  legacy enrichment, market separation, and fail-closed validation.
- Tracker, scan, and dashboard endpoint tests cover backward compatibility and exposure.
- The next milestone is the immutable prediction ledger. It must preserve the original
  prediction payload before any outcome becomes knowable.
