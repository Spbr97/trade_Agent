# AEM v2 Precision Ladder — Milestone 0 protocol checkpoint

Date: 26 September 2026

Branch: `codex/aem-v2-accuracy-first`

Artifact: `b13c8567f6434a3e96c94206df121653`

Status: **protocol frozen; algorithm not yet implemented or evaluated**

## Outcome

Milestone 0 converts the approved 50% plan into immutable, machine-readable research
contracts. It establishes what AEM v2 may use, how much may be searched and what must
pass before a candidate can advance.

It does not detect opportunities, score calls, replay outcomes or improve the
baseline. The canonical AEM v1 result remains **149/693 strict wins (21.50%)**, an
18.60% Wilson lower bound and **−0.27471R per resolved fill**.

## Frozen research design

Entry modes:

- anticipatory impulse;
- confirmed pullback; and
- breakout retest.

The feature registry contains 34 decision-time fields across opening structure,
impulse, breakout state, VWAP, participation, cross-sectional context, remaining
room, execution quality and regime. Outcome and execution-result fields are forbidden
from the prediction registry.

| Geometry | Target | Stop | Maximum hold | Gross R:R |
|---|---:|---:|---:|---:|
| `quick_35_30_25` | 0.35% | 0.30% | 25 minutes | 1.167 |
| `quick_45_35_40` | 0.45% | 0.35% | 40 minutes | 1.286 |
| `quick_60_45_60` | 0.60% | 0.45% | 60 minutes | 1.333 |

Every geometry keeps target above stop, remains below the six-geometry budget and is
a separate research contract. None satisfies the production `min_net_rr=2.0` rule,
so the protocol explicitly records `production_compatible=false`. This cannot be
silently relaxed during research or promotion.

## Frozen evidence gates

A development candidate requires at least:

- 50% observed strict success;
- 40% Wilson 95% lower bound;
- 100 resolved selected fills;
- 30 active sessions;
- 40% active-session coverage;
- no unresolved selected calls;
- positive mean net R under every mandatory stress; and
- at least +0.10R versus matched random timing.

The initial complete-pipeline trial budget is 24. The eventual 80%/500-total/100-OOS
eligibility requirements remain recorded separately and unchanged.

## Fingerprints

- Plan: `54c08dccffb72782f5ab1676b7b8833b74cde2da610037e5b2d0d9cf3ca61490`
- Implementation: `ae28ac6e335fd6f4953f28c9334c9d5b52669f18b619fe45e5d23da9ed763efb`
- Contract: `87d416c314bd6376307834805f500f385484de59db3407796da7d931813976cb`
- Protocol: `0b699dba2a177d4d4b51f63796e8b4ad58d0dbdf5fb7abb2b986313ed1f9a299`
- Bundle: `097ccb416ab54f60815c3abedbc73f86b9e308e201f72f274e9fa906a617da26`

Machine-readable evidence is in
[`docs/evidence/aem-v2-protocol.json`](evidence/aem-v2-protocol.json).

## Safety and next step

No production scanner, management, risk, alert or order module imports the AEM v2
contract. No network request or production-database write occurred. The parked
market/sector context store remains unused.

Milestone 1 is the causal event and outcome engine. It must implement completed-bar
ordering, executable fills, conservative target/stop ambiguity, cost-aware outcomes
and failing-case tests before any selector is trained.

Verification completed with **921 passed tests** and two pre-existing skips across
the currently runnable production and research suites. Five SciPy-dependent modules
and one known scikit-learn tuning test remain excluded under the documented Windows
Application Control limitation. Targeted contract, CLI and dashboard API checks
passed 16 tests; Ruff, JavaScript syntax, JSON parsing and diff checks also passed.
