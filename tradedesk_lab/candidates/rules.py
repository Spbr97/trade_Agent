"""The self-review replacement search's strategy space: a small grammar of chart-reading
rules (trigger x trend filter x exit style) plus "improved" variants of each production
setup (its own pattern, re-tested under a new filter/exit). Every expression is plain,
causal pandas over the `daily_features` frame (rolling/shift only - no future bars, no
recursive smoothing), so evaluating it on a tail of the frame gives the same last value as
evaluating it on the whole frame; `tests_lab/test_rule_candidates.py` checks exactly that
for every trigger and filter.

The same expression strings are emitted verbatim into the production module a passing
candidate becomes (`production_source`), so the code a human approves is literally the
logic that was validated, not a re-implementation of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tradedesk.engine.signals import ExitPlan, SetupKind
from tradedesk.setups import REGISTRY
from tradedesk.setups.base import SetupContext

from .base import LabSignal

WARMUP_BARS = 210
TAIL_BARS = 300  # >= the longest lookback below (rolling 120 over a 20-bar stdev + shifts)

# name -> (family, expression). Family decides which exit styles it is paired with.
TRIGGERS: dict[str, tuple[str, str]] = {
    "donchian20": (
        "momentum",
        'df["close"] > df["high"].shift(1).rolling(20, min_periods=20).max()',
    ),
    "donchian55": (
        "momentum",
        'df["close"] > df["high"].shift(1).rolling(55, min_periods=55).max()',
    ),
    "tsmom60": (
        "momentum",
        '(df["close"] > df["close"].shift(60)) & (df["close"].shift(1) <= df["close"].shift(61))',
    ),
    "ema20_50_cross": (
        "momentum",
        '(df["ema20"] > df["ema50"]) & (df["ema20"].shift(1) <= df["ema50"].shift(1))',
    ),
    "golden_cross": (
        "momentum",
        '(df["ema50"] > df["ema200"]) & (df["ema50"].shift(1) <= df["ema200"].shift(1))',
    ),
    "macd_cross": (
        "momentum",
        '(df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))',
    ),
    "squeeze_break": (
        "momentum",
        '(df["bb_width"].shift(1) <= df["bb_width"].shift(1).rolling(120, min_periods=120)'
        '.quantile(0.2)) & (df["close"] > df["close"].rolling(20, min_periods=20).mean() '
        '+ 2 * df["close"].rolling(20, min_periods=20).std(ddof=0))',
    ),
    "volume_thrust": (
        "momentum",
        '(df["close"] > df["high"].shift(1).rolling(20, min_periods=20).max()) '
        '& (df["vol_ratio50"] >= 2.0)',
    ),
    "adx_thrust": (
        "momentum",
        '(df["adx14"] > 25) & (df["adx14"].shift(1) <= 25) & (df["plus_di"] > df["minus_di"])',
    ),
    "ema20_bounce": (
        "momentum",
        '(df["ema20"] > df["ema50"]) & (df["low"] <= df["ema20"]) & (df["close"] > df["ema20"]) '
        '& (df["close"] > df["open"])',
    ),
    "rsi2_dip": ("reversion", 'df["rsi2"] < 10'),
    "bollinger_dip": (
        "reversion",
        'df["close"] < df["close"].rolling(20, min_periods=20).mean() '
        '- 2 * df["close"].rolling(20, min_periods=20).std(ddof=0)',
    ),
    "rsi14_turn": ("reversion", '(df["rsi14"] >= 30) & (df["rsi14"].shift(1) < 30)'),
}

FILTERS: dict[str, str] = {
    "any": 'pd.Series(True, index=df.index)',
    "above_ema200": 'df["close"] > df["ema200"]',
    "bull_stack": 'df["ema50"] > df["ema200"]',
    "trending": '(df["adx14"] > 20) & (df["plus_di"] > df["minus_di"])',
    "strong_trend": '(df["adx14"] > 30) & (df["plus_di"] > df["minus_di"])',
    "rsi_momentum": 'df["rsi14"] > 55',
    "volume_confirm": 'df["vol_ratio50"] >= 1.5',
    "calm_vol": 'df["atr_pct_rank"] < 50',
}


@dataclass(frozen=True)
class ExitStyle:
    stop_atr: float
    partial_at_r: float
    partial_fraction: float
    trail: str
    trail_atr_mult: float
    max_hold: int
    time_stop_sessions: int
    time_stop_min_r: float = 1.0

    def plan(self) -> ExitPlan:
        return ExitPlan(
            partial_at_r=self.partial_at_r,
            partial_fraction=self.partial_fraction,
            trail=self.trail,  # type: ignore[arg-type]
            trail_atr_mult=self.trail_atr_mult,
            time_stop_sessions=self.time_stop_sessions,
            time_stop_min_r=self.time_stop_min_r,
            max_hold_sessions=self.max_hold,
        )


# time_stop_sessions > max_hold disables the early time stop for trend exits: a trend
# trade is supposed to be allowed to sit flat before it moves.
EXITS: dict[str, ExitStyle] = {
    "trend_trail": ExitStyle(2.5, 2.0, 0.5, "atr", 3.0, 40, 41),
    "target_3r": ExitStyle(2.0, 3.0, 1.0, "atr", 3.0, 20, 21),
    "revert_1r": ExitStyle(3.0, 1.0, 1.0, "ema10_close", 2.0, 5, 6),
    "revert_2r": ExitStyle(3.0, 2.0, 1.0, "ema10_close", 2.0, 10, 5),
}
FAMILY_EXITS: dict[str, tuple[str, ...]] = {
    "momentum": ("trend_trail", "target_3r"),
    "reversion": ("revert_1r", "revert_2r"),
}


def _compile(expr: str) -> Any:
    return eval(f"lambda df: {expr}", {"pd": pd, "np": np})  # noqa: S307 - module constants only


_TRIGGER_FNS = {name: _compile(expr) for name, (_, expr) in TRIGGERS.items()}
_FILTER_FNS = {name: _compile(expr) for name, expr in FILTERS.items()}


def evaluate(expr_fn: Any, df: pd.DataFrame) -> pd.Series:
    return expr_fn(df).fillna(False).astype(bool)


# (setup, params, code, first bar) -> (evaluated-positions bitmap, {position: Signal}).
# A production setup's arm() on a causal prefix is a pure function of that prefix, and all
# 16 improvement variants of one setup ask it the same question on the same prefixes - so
# it is answered once per run instead of 16 times. `clear_base_cache()` at the start of
# every search run keeps an entry from outliving the frames it was computed on.
_BASE_CACHE: dict[tuple[Any, ...], tuple[bytearray, dict[int, Any]]] = {}


def clear_base_cache() -> None:
    _BASE_CACHE.clear()


def _cached_base_arm(
    base: str, params: dict[str, Any], df: pd.DataFrame, ctx: SetupContext
) -> Any:
    key = (
        base, repr(sorted(params.items())), ctx.scrip_code,
        pd.Timestamp(df.index[0]), float(df["close"].iloc[0]),
    )  # fmt: skip
    evaluated, hits = _BASE_CACHE.setdefault(key, (bytearray(), {}))
    pos = len(df) - 1
    if pos < len(evaluated) and evaluated[pos]:
        return hits.get(pos)
    result = REGISTRY[SetupKind(base)].arm(df, ctx, params)
    if pos >= len(evaluated):
        evaluated.extend(bytes(pos + 1 - len(evaluated)))
    evaluated[pos] = 1
    if result is not None:
        hits[pos] = result
    return result


def _lab_signal(
    name: str,
    ctx: SetupContext,
    df: pd.DataFrame,
    *,
    trigger: float,
    stop: float,
    exit_style: ExitStyle,
    reasons: tuple[str, ...],
) -> LabSignal | None:
    last = df.iloc[-1]
    atr = float(last["atr14"])
    risk = trigger - stop
    if not (atr > 0 and trigger > 0 and stop > 0 and risk > 0):
        return None
    t1 = trigger + exit_style.partial_at_r * risk
    t2 = max(t1, trigger + 3 * risk)
    armed_on = pd.Timestamp(df.index[-1]).date()
    return LabSignal(
        id=f"{name}:{ctx.scrip_code}:{armed_on.isoformat()}",
        scrip_code=ctx.scrip_code,
        symbol=ctx.symbol,
        armed_on=armed_on,
        trigger=trigger,
        stop=stop,
        t1=t1,
        t2=t2,
        atr=atr,
        exit_plan=exit_style.plan(),
        valid_sessions=1,
        reasons=reasons,
    )


@dataclass(frozen=True)
class RuleCandidate:
    """A new-method candidate: `trigger` AND `filter` on the signal bar, enter at/after its
    close, stop `stop_atr` x ATR below, managed by `exit_name`'s exit style."""

    trigger: str
    filter: str
    exit_name: str
    kind: str = "rule"

    @property
    def name(self) -> str:
        return f"r_{self.trigger}_{self.filter}_{self.exit_name}"

    @property
    def family(self) -> str:
        return TRIGGERS[self.trigger][0]

    def describe(self) -> str:
        return f"{self.trigger} entry, {self.filter} filter, {self.exit_name} exit"

    @property
    def gate_column(self) -> str:
        return f"_sig_{self.name}"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        hit = evaluate(_TRIGGER_FNS[self.trigger], df) & evaluate(_FILTER_FNS[self.filter], df)
        return df.assign(**{f"_sig_{self.name}": hit})

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> LabSignal | None:
        if len(df) < WARMUP_BARS:
            return None
        column = f"_sig_{self.name}"
        if column in df.columns:
            fired = bool(df[column].iloc[-1])
        else:
            fired = bool(self.prepare(df.iloc[-TAIL_BARS:])[column].iloc[-1])
        if not fired:
            return None
        style = EXITS[self.exit_name]
        last = df.iloc[-1]
        trigger = float(last["close"])
        return _lab_signal(
            self.name, ctx, df,
            trigger=trigger, stop=trigger - style.stop_atr * float(last["atr14"]),
            exit_style=style, reasons=(self.describe(),),
        )  # fmt: skip

    def production_source(self, setup_kind_name: str) -> str:
        style = EXITS[self.exit_name]
        return _PRODUCTION_HEADER.format(description=self.describe(), name=self.name) + f'''
TAIL_BARS = {TAIL_BARS}


def _trigger(df: pd.DataFrame) -> pd.Series:
    fired = {TRIGGERS[self.trigger][1]}  # noqa: E501
    return fired.fillna(False).astype(bool)


def _filter(df: pd.DataFrame) -> pd.Series:
    passes = {FILTERS[self.filter]}  # noqa: E501
    return passes.fillna(False).astype(bool)


class {_class_name(self.name)}:
    kind = SetupKind.{setup_kind_name}

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < {WARMUP_BARS}:
            return None
        tail = df.iloc[-TAIL_BARS:]
        if not bool((_trigger(tail) & _filter(tail)).iloc[-1]):
            return None
        last = df.iloc[-1]
        trigger = float(last["close"])
        stop = trigger - {style.stop_atr} * float(last["atr14"])
        return make_signal(
            kind=self.kind, df=df, trigger=trigger, stop=stop, final_target=None,
            exit_plan={_exit_plan_literal(style)},
            ctx=ctx, geometry={{"stop_atr": {style.stop_atr}}},
            reasons=["{self.describe()}"],
            valid_sessions=1,
        )
'''


@dataclass(frozen=True)
class ImprovedSetup:
    """An improvement variant of a production setup: its own pattern detection and
    structural stop, unchanged, plus a new trend filter and exit style. `base_params` is
    the setup's config/setups.yaml block at search time, frozen into the candidate (and
    into its production module) so what is validated is exactly what would run."""

    base: str
    filter: str
    exit_name: str
    base_params: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    kind: str = "improve"

    @property
    def name(self) -> str:
        return f"imp_{self.base}_{self.filter}_{self.exit_name}"

    @property
    def family(self) -> str:
        return "improve"

    def describe(self) -> str:
        return f"{self.base} pattern, {self.filter} filter, {self.exit_name} exit"

    @property
    def gate_column(self) -> str:
        return f"_flt_{self.name}"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.assign(**{f"_flt_{self.name}": evaluate(_FILTER_FNS[self.filter], df)})

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> LabSignal | None:
        if len(df) < WARMUP_BARS:
            return None
        column = f"_flt_{self.name}"
        if column in df.columns:
            passes = bool(df[column].iloc[-1])
        else:
            passes = bool(self.prepare(df.iloc[-TAIL_BARS:])[column].iloc[-1])
        if not passes:
            return None
        base_sig = _cached_base_arm(self.base, self.base_params, df, ctx)
        if base_sig is None:
            return None
        return _lab_signal(
            self.name, ctx, df,
            trigger=float(base_sig.trigger), stop=float(base_sig.stop),
            exit_style=EXITS[self.exit_name], reasons=(self.describe(),),
        )  # fmt: skip

    def production_source(self, setup_kind_name: str) -> str:
        style = EXITS[self.exit_name]
        base_cls = type(REGISTRY[SetupKind(self.base)]).__name__
        return _PRODUCTION_HEADER.format(description=self.describe(), name=self.name) + f'''\
from tradedesk.setups.{self.base} import {base_cls} as _Base

TAIL_BARS = {TAIL_BARS}
BASE_PARAMS: dict[str, Any] = {dict(self.base_params)!r}  # noqa: E501


def _filter(df: pd.DataFrame) -> pd.Series:
    passes = {FILTERS[self.filter]}  # noqa: E501
    return passes.fillna(False).astype(bool)


class {_class_name(self.name)}:
    kind = SetupKind.{setup_kind_name}

    def arm(self, df: pd.DataFrame, ctx: SetupContext, params: dict[str, Any]) -> Signal | None:
        if len(df) < {WARMUP_BARS}:
            return None
        if not bool(_filter(df.iloc[-TAIL_BARS:]).iloc[-1]):
            return None
        base = _Base().arm(df, ctx, BASE_PARAMS)
        if base is None:
            return None
        return make_signal(
            kind=self.kind, df=df, trigger=base.trigger, stop=base.stop, final_target=None,
            exit_plan={_exit_plan_literal(style)},
            ctx=ctx, geometry=dict(base.geometry),
            reasons=[*base.reasons, "{self.filter} filter, {self.exit_name} exit"],
            valid_sessions=1,
        )
'''


_PRODUCTION_HEADER = '''"""Self-review replacement candidate `{name}`.

{description}.

Generated from tradedesk_lab/candidates/rules.py and validated through the full harness
gauntlet before it was proposed - see docs/self-review-applied-log.md for the approval
that added it.
"""

from __future__ import annotations

from typing import Any

import numpy as np  # noqa: F401 - available to the generated expressions
import pandas as pd

from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.setups.base import SetupContext, make_signal
'''


def _class_name(name: str) -> str:
    # Must match apply.py's derivation: "".join(part.capitalize() for part in name.split("_"))
    return "".join(part.capitalize() for part in name.split("_"))


def _exit_plan_literal(style: ExitStyle) -> str:
    pad = " " * 16
    return (
        "ExitPlan(\n"
        f"{pad}partial_at_r={style.partial_at_r}, partial_fraction={style.partial_fraction},\n"
        f'{pad}trail="{style.trail}", trail_atr_mult={style.trail_atr_mult},\n'
        f"{pad}time_stop_sessions={style.time_stop_sessions}, "
        f"time_stop_min_r={style.time_stop_min_r},\n"
        f"{pad}max_hold_sessions={style.max_hold},\n"
        f"{' ' * 12})"
    )


def improvement_candidates(
    setup: str, base_params: dict[str, Any]
) -> list[ImprovedSetup]:
    return [
        ImprovedSetup(setup, flt, exit_name, base_params=base_params)
        for flt in FILTERS
        for exit_name in ("trend_trail", "target_3r")
    ]


def rule_candidates() -> list[RuleCandidate]:
    return [
        RuleCandidate(trigger, flt, exit_name)
        for trigger, (family, _) in TRIGGERS.items()
        for flt in FILTERS
        for exit_name in FAMILY_EXITS[family]
    ]
