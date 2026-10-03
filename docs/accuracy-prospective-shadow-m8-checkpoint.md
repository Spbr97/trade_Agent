# M8 checkpoint: prospective shadow validation

Activated: 2026-10-03 23:18 IST  
Forward cutoff: `2026-10-03`  
Frozen model SHA-256: `dc4ab66f2958a7e2ee229b8b9b1d33b6a0f940a44b039457423234c7c27ad538`  
Source M7 artifact SHA-256: `70c4d22b0711ea4c651f60930749729622d2917610ede58a610636860e504b12`  
Initial evidence: 0 evaluated, 0 selected, 0 resolved; correctly
`collecting_insufficient_evidence`.

- [x] Freeze the exact M7 setup, geometry, model family, feature order, threshold and top-k.
- [x] Use a strict activation cutoff based on the activation date, latest stored NSE
  benchmark session, and latest existing watchlist (whichever is later).
- [x] Reject historical backfill and late scoring from prospective evidence.
- [x] Record every newly evaluated `trend_pullback` candidate and freeze the session ranking.
- [x] Resolve selected calls with next-open fills, 1 ATR stops, 0.5R targets, three-session
  holds, slippage, sizing and NSE charges.
- [x] Keep the collector isolated from signals, alerts, grades, live sizing, management and
  orders; collector failure cannot fail the established NSE tracker.
- [x] Add dashboard fields for fresh selected/resolved counts, accuracy, Wilson lower bound,
  active sessions, session-target rate, expectancy and evidence status.
- [x] Keep small samples visibly `collecting_insufficient_evidence` rather than treating
  unavailable metrics as a pass.
- [ ] Collect at least 100 freshly resolved selected calls across at least 30 sessions.
- [ ] Apply every preregistered performance gate once that sample exists.
- [ ] If and only if the prospective gate passes, prepare a separate human-reviewed proposal;
  do not automatically alter the baseline or enable calls.
