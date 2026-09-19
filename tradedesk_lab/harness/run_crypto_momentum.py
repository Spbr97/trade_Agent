"""The harness's first real run: `MomentumContinuation`
(`tradedesk_lab/crypto_intraday_research.py`) through the full validation gauntlet, on the
already-backfilled crypto H1 data. Reuses that module's already-tested fill/cost machinery
directly rather than re-deriving it - see `rules/crypto_momentum_continuation.md` for the
Phase 0 rule spec and kill criteria this validates.

Bypasses `scan_bar`/`INTRADAY_REGISTRY` (which exist for the production-shaped live/scan
path) and calls `MomentumContinuation.arm()` directly per bar, since re-deriving trades at
several perturbed parameter values (Phase 4's sensitivity sweep) is simpler as a plain
function call than juggling registry collisions across re-runs.

Compute-cost tradeoff, stated plainly: the main gauntlet run uses the FULL ~2-year H1 history
(matching the population `crypto_intraday_research.py::run_research()` already measured -
855 executable candidates, mean net R -0.69 - see the rules doc's "prior evidence" section).
The parameter-sensitivity sweep re-derives trades six more times (two parameters x three
values), which is expensive at full history, so it uses a bounded recent window instead
(`SENSITIVITY_BARS`, matching `run_research()`'s own default `bars=4000`) - a real, disclosed
approximation, not a hidden one.
"""

from __future__ import annotations

from typing import Any

from tradedesk.broker.indstocks.models import Interval
from tradedesk.config import load_config
from tradedesk.markets.costs import CryptoCostModel
from tradedesk.markets.market import crypto_market
from tradedesk.setups.intraday import IntradaySetupContext
from tradedesk_lab.artifacts import OUTPUT, ROOT, write_json
from tradedesk_lab.crypto_intraday_research import (
    MomentumContinuation,
    ResolvedCandidate,
    _read_frames,
    _trade_row,
    fill_candidate,
)
from tradedesk_lab.harness.gauntlet import run_gauntlet
from tradedesk_lab.harness.regime_split import classify_market_regime
from tradedesk_lab.harness.sensitivity import sweep_parameter
from tradedesk_lab.harness.spec import KillCriteria
from tradedesk_lab.harness.trade import HarnessTrade

SENSITIVITY_BARS = 4000
KILL_CRITERIA = KillCriteria(
    min_net_expectancy_r=0.0, min_sample_size=300, max_drawdown_pct=0.30, max_losing_streak=10
)


def _run_momentum(
    frames: dict, symbols: dict, slip: float, params: dict[str, float] | None = None
) -> list[ResolvedCandidate]:
    setup = MomentumContinuation()
    for key, value in (params or {}).items():
        setattr(setup, key, value)
    candidates: list[ResolvedCandidate] = []
    for code, frame in frames.items():
        n = len(frame)
        if n < 45:
            continue
        for i in range(40, n - 2):
            df = frame.iloc[: i + 1]
            ctx = IntradaySetupContext(
                scrip_code=code, symbol=symbols.get(code, code), interval=Interval.H1
            )
            sig = setup.arm(df, ctx, {})
            if sig is None:
                continue
            candidate, _reason = fill_candidate(sig, frame, i, slip)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _to_harness_trades(
    candidates: list[ResolvedCandidate], equity: float, risk: Any, costs: CryptoCostModel,
    qty_step: float, min_notional: float,
) -> list[HarnessTrade]:  # fmt: skip
    trades = []
    for c in candidates:
        row = _trade_row(c, equity, risk, costs, qty_step, min_notional)
        if not row["qty"]:  # unsized: a money question, not a pattern question - excluded
            continue  # from expectancy the same way crypto_intraday_research.py does
        trades.append(
            HarnessTrade(
                scrip_code=c.signal.scrip_code,
                entry_at=c.entry_at,
                exit_at=c.exit_at,
                entry_bar=c.entry_bar,
                window_start=c.window_start,
                window_end=c.window_end,
                entry=c.entry,
                stop=c.stop,
                target=c.target,
                gross_r=c.gross_r,
                net_r=row["net_r"],
            )
        )
    return trades


def main() -> dict:
    settings = load_config(ROOT)
    market = crypto_market(settings)
    costs = market.costs
    assert isinstance(costs, CryptoCostModel)
    slip = float(costs.slippage_pct)
    equity = float(settings.risk.trading_capital)

    frames, symbols = _read_frames(ROOT, bars=None)  # full ~2y history, matches prior evidence
    candidates = _run_momentum(frames, symbols, slip)
    trades = _to_harness_trades(
        candidates, equity, settings.risk, costs, market.qty_step, market.min_notional_inr
    )

    bars_by_code = {
        code: tuple(frame[c].to_numpy(float) for c in ("open", "high", "low", "close"))
        for code, frame in frames.items()
    }

    # Parameter sensitivity, on a bounded recent window (see module docstring).
    sens_frames, sens_symbols = _read_frames(ROOT, bars=SENSITIVITY_BARS)

    def expectancy_at(param: str, value: float) -> float:
        cands = _run_momentum(sens_frames, sens_symbols, slip, params={param: value})
        sens_trades = _to_harness_trades(
            cands, equity, settings.risk, costs, market.qty_step, market.min_notional_inr
        )
        if not sens_trades:
            return -10.0  # sentinel: parameter value eliminated the population entirely
        return sum(t.net_r for t in sens_trades) / len(sens_trades)

    sensitivity = [
        sweep_parameter(
            "vol_ratio_min",
            MomentumContinuation.vol_ratio_min,
            lambda v: expectancy_at("vol_ratio_min", v),
        ),
        sweep_parameter(
            "max_extension_atr",
            MomentumContinuation.max_extension_atr,
            lambda v: expectancy_at("max_extension_atr", v),
        ),
    ]

    # Regime split: label each trade's entry bar from its own code's features frame.
    regime_labels = []
    regimes_by_code = {code: classify_market_regime(frame) for code, frame in frames.items()}
    for t in trades:
        regime_labels.append(str(regimes_by_code[t.scrip_code].iloc[t.entry_bar]))

    report = run_gauntlet(
        "crypto_momentum_continuation",
        trades,
        bars_by_code,
        KILL_CRITERIA,
        n_null_cohorts=1000,
        n_monte_carlo=5000,
        sensitivity=sensitivity,
        regime_labels=regime_labels,
    )
    result = {
        "strategy_name": report.strategy_name,
        "stopped_at": report.stopped_at,
        "stages": report.stages,
        "kill_criteria": report.kill_criteria,
        "trade_count": len(trades),
    }
    destination = OUTPUT / "harness" / "crypto_momentum_continuation" / "report.json"
    write_json(destination, result)
    write_json(OUTPUT / "harness" / "crypto_momentum_continuation" / "latest.json", result)
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(main(), indent=2, default=str))
