# Self-learning Milestone 10 checkpoint — fail-closed personal calls

Completed: 2026-10-06

The Calls tab now begins with a separate Personal Calls panel for NSE, BSE and crypto. Its
default and current output is:

`NO QUALIFIED PERSONAL CALL TODAY`

This panel is intentionally independent from the existing watchlist, call-history and
Learning > Predictions tables. Those broader tables continue to show successes, failures,
rejected/shadow calls, pending calls, never-triggered calls and invalid calls so weak agent
performance cannot be hidden by a precision filter.

## Fail-closed qualification

A row can enter the personal panel only when all of the following are true:

1. a contract-specific challenger exists;
2. every final historical, uncertainty, economic, random-control and prospective gate is
   marked passed;
3. an explicit promotion record names that exact challenger and records user approval;
4. the row is a sealed live `qualified_call` under the approved contract;
5. it has no rejection reasons and remains a current pending call.

Missing, unreadable or mismatched evidence returns zero calls. Research-only, shadow and
rejected calls cannot be promoted by this endpoint.

## Displayed evidence

When a future approved call exists, the panel shows symbol, market, setup, entry range,
stop, targets, expiry, suggested risk percentage/amount/quantity, calibrated confidence,
contract, locked evidence sample size, strict accuracy, Wilson lower bound and the exact
qualification reasons.

The panel currently shows no approved contract, no available locked accuracy and no
promotion authority. That is correct: the new sealed ledgers still have zero mature rows,
matched random timing is unfinished, no prospective challenger has passed, and the user has
not approved a model promotion.
