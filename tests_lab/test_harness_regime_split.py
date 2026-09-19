import pandas as pd
import pytest
from tradedesk_lab.harness.regime_split import (
    MarketRegime,
    classify_market_regime,
    split_trades_by_regime,
)


def _features(adx: list[float], atr_pct: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"adx14": adx, "atr_pct": atr_pct})


def test_high_vol_takes_priority_over_trending():
    # High ADX but also in the top atr_pct percentile -> HIGH_VOL, not TRENDING.
    features = _features(adx=[30] * 9 + [30], atr_pct=[1.0] * 9 + [10.0])
    labels = classify_market_regime(features, atr_pct_high_vol_percentile=0.80)
    assert labels.iloc[-1] == MarketRegime.HIGH_VOL.value


def test_trending_when_adx_high_and_not_high_vol():
    # Distinct, increasing atr_pct values so the 80th-percentile cutoff sits well above the
    # evaluated (low-index) bar - only ADX should decide that bar's label.
    atr_pct = [float(i) for i in range(1, 21)]  # 1..20; p80 cutoff sits around 16
    labels = classify_market_regime(
        _features(adx=[10] * 4 + [30] + [10] * 15, atr_pct=atr_pct),
        atr_pct_high_vol_percentile=0.80,
    )
    assert labels.iloc[4] == MarketRegime.TRENDING.value


def test_ranging_when_neither():
    atr_pct = [float(i) for i in range(1, 21)]
    labels = classify_market_regime(
        _features(adx=[10] * 20, atr_pct=atr_pct), atr_pct_high_vol_percentile=0.80
    )
    assert labels.iloc[4] == MarketRegime.RANGING.value


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="adx14"):
        classify_market_regime(pd.DataFrame({"atr_pct": [1.0]}))


def test_split_trades_by_regime_groups_and_averages():
    regimes = [MarketRegime.TRENDING, MarketRegime.TRENDING, MarketRegime.RANGING]
    rs = [1.0, -0.5, -1.0]
    grouped = split_trades_by_regime(regimes, rs)
    assert grouped["trending"] == {"trades": 2, "mean_r": pytest.approx(0.25)}
    assert grouped["ranging"] == {"trades": 1, "mean_r": pytest.approx(-1.0)}


def test_split_requires_matching_lengths():
    with pytest.raises(ValueError):
        split_trades_by_regime([MarketRegime.TRENDING], [1.0, 2.0])
