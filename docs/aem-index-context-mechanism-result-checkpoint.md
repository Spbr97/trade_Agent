# AEM index-context mechanism result checkpoint

Evaluated: 30 September 2026

Outcome: **mechanism absent; stop before selector**

The real AEM outcomes were opened once under committed protocol
`f8543543eb46c06710389882a5d791cb0886dbd0e30e31978f124034e3c6852d`.
Run `e3857bf8400c496d9af081e54f8346a5` reproduced the unchanged baseline exactly:
149 strict wins from 693 resolved fills (21.50%), Wilson lower bound 18.60%, and
-0.27471R mean net R.

## Results

| Frozen rule | Strict result | Wilson lower | Mean net R | Active sessions | Accuracy shuffle p | Net-R shuffle p | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| NIFTY 50 positive, 5m | 101/421 (23.99%) | 20.16% | -0.23284R | 110 | 0.1712 | 0.2646 | Fail |
| Majority positive, 5m | 103/410 (25.12%) | 21.17% | -0.20519R | 111 | 0.0195 | 0.0817 | Fail |
| All positive, 5m | 73/303 (24.09%) | 19.62% | -0.23603R | 104 | 0.3891 | 0.4358 | Fail |
| All positive, 3m | 72/278 (25.90%) | 21.10% | -0.19418R | 108 | 0.0739 | 0.0739 | Fail |

Every rule improved pooled raw accuracy and mean net R relative to the unchanged
population, and all met the sample and availability floors. That is useful diagnostic
evidence, but it is not a qualifying mechanism:

- every selected subset still lost money after modeled costs;
- no rule cleared every matched-shuffle accuracy and net-R gate;
- the all-positive rules reversed at least one chronological fold; and
- the best pooled raw accuracy was only 25.90%, far below the 50% development target.

The five-minute majority rule cleared its accuracy shuffle control but not its net-R
control. Its apparently better hit rate therefore does not justify optimization or
promotion.

## Decision

Version 1 stops here. No selector race, model training, threshold search, candidate
registration, baseline replacement, live behavior, dashboard, management, risk,
alert, broker, or order change is authorized. The canonical baseline remains 21.50%.

Machine-readable evidence is in
`docs/evidence/aem-context-mechanism-result.json`. The ignored local population,
trials, folds, and 1,024 shuffle rows are preserved by their SHA-256 fingerprints.
