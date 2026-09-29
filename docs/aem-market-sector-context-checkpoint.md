# AEM market/sector context collection checkpoint

Date: 30 September 2026

Branch: `codex/aem-context-m1-collection`

Frozen dataset: `ec539cf66bea4c18ba994506f85a52d6`

Status: **bounded collection complete with one reproducible source gap; context
experiment not yet run; no baseline improvement established**

## Outcome

The isolated collector acquired causal one-minute context for the frozen 120-session
evaluation calendar:

- NIFTY 50: `NSE_40000001` — 45,000/45,000 bars;
- BANK NIFTY: `NSE_40000003` — 45,000/45,000 bars; and
- Nifty Financial: `NSE_40000100` — 44,987/45,000 bars.

Run `d213de5b8c564896b24c2b424d902bd2` used 26 read-only requests and collected
**134,987/135,000 bars (99.990%)**, covering **359/360 index-sessions** completely.
Run `2051f3c5c247476daa723ab6ef7dff05` repeated the only incomplete window in one
additional request and returned the identical gap.

The missing coordinate is Nifty Financial on **7 July 2026**, from **15:17 through
15:29 IST** (13 consecutive closing minutes). It is retained as a source-data
exception. It will not be filled, interpolated, or silently dropped. A later causal
join must fail closed for any event whose registered lookback needs those bars.

## Safety result

The first preflight initially classified the running `uv run tradedesk mcp` process
chain as unknown. Inspection showed that the repository command serves local,
read-only store and journal analysis over stdio and does not construct a broker
client or market-data loop. The lab-only preflight now classifies only the exact
`tradedesk mcp` operation as `read_only_mcp`; broker operations, opaque Python
processes, unknown research consumers, and running/imminent scheduled tasks still
block.

Forty-seven focused preflight tests pass. The real preflight then allowed collection
outside the protected market window. It re-ran before every request. No process or
scheduled task was stopped, and no credential, production call, dashboard,
management, risk, alert, or order path changed.

## Accuracy interpretation

Collection is a data milestone, not an accuracy result. The canonical broader AEM
baseline remains **149/693 strict wins (21.50%)** with **-0.27471R per resolved
fill**. No model or selector has consumed the new index data, so the measured
improvement remains exactly zero.

All 120 sessions are already outcome-inspected development data. Any mechanism test
on them can reject a weak hypothesis or justify freezing a later trial, but cannot
establish a new baseline. A promotion claim requires later unconsumed or prospective
evidence under the unchanged accuracy, Wilson-bound, availability, after-cost, stress,
and concentration gates.

## Frozen next step

The next track is recorded in
[`plan-aem-index-context-accuracy.md`](plan-aem-index-context-accuracy.md). Its first
version avoids historical membership leakage by using all three index series only as
uniform market/risk context for every candidate. It does **not** assign stocks to
current Nifty or sector membership. The order is:

1. build and audit the causal decision-time join;
2. verify the one known gap fails closed;
3. run the preregistered mechanism and placebo checks on consumed development data;
4. freeze at most one simple context rule only if the mechanism and economics pass;
5. test that frozen rule once on later unconsumed/prospective evidence; and
6. add dashboard visibility only after a real candidate exists, never from collection
   progress alone.

The structured evidence is in
[`docs/evidence/aem-market-sector-context-readiness.json`](evidence/aem-market-sector-context-readiness.json).
