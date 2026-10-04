"""A Freqtrade form must reproduce the frozen portfolio weights causally."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import numpy as np
import pytest

from r1_r2b_risk import DATA, RiskHoldR2b, risk_weights, signals


def strategy_class():
    path = Path(__file__).resolve().parents[2] / "strategies/RiskHoldR2bCov10.py"
    spec = importlib.util.spec_from_file_location("RiskHoldR2bCov10", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RiskHoldR2bCov10


def frames(px):
    return {f"{asset}/USDT": pd.DataFrame({"date": px.index, "close": px[f"{asset}_close"].to_numpy()})
            for asset in ("BTC", "ETH")}


def test_weights_equal_frozen_r1_even_if_companion_has_future_rows():
    px = pd.read_parquet(DATA).loc[:"2026-09-21"]
    inputs = frames(px)
    expected = risk_weights(px, signals(px, RiskHoldR2b))
    for length in (240, len(px)):
        for pair in inputs:
            strategy = strategy_class()({})
            strategy.dp = SimpleNamespace(get_pair_dataframe=lambda p, tf: inputs[p])
            actual = strategy.populate_indicators(inputs[pair].iloc[:length].copy(), {"pair": pair})
            pd.testing.assert_series_equal(actual.set_index("date")["weight"],
                                           expected[pair.split("/")[0]].iloc[:length],
                                           check_names=False)


def test_missing_companion_data_gives_no_entry_weight():
    px = pd.read_parquet(DATA).iloc[:240]
    strategy = strategy_class()({})
    strategy.dp = SimpleNamespace(get_pair_dataframe=lambda p, tf: pd.DataFrame(columns=["date", "close"]))
    actual = strategy.populate_indicators(frames(px)["BTC/USDT"], {"pair": "BTC/USDT"})
    assert (actual.weight == 0).all()


@pytest.mark.parametrize("drop_primary", [False, True])
def test_internal_daily_gap_does_not_compress_covariance_history(drop_primary):
    dates = pd.date_range("2020-01-01", periods=400, tz="UTC")
    t = np.arange(len(dates))
    inputs = {f"{asset}/USDT": pd.DataFrame({
        "date": dates, "close": 100 * np.exp(.002 * t + .005 * np.sin(t / period))})
        for asset, period in (("BTC", 3), ("ETH", 5))}
    gap = 350
    inputs["ETH/USDT"] = inputs["ETH/USDT"].drop(index=gap)
    if drop_primary:
        inputs["BTC/USDT"] = inputs["BTC/USDT"].drop(index=gap)
    strategy = strategy_class()({})
    strategy.dp = SimpleNamespace(get_pair_dataframe=lambda p, tf: inputs[p])
    actual = strategy.populate_indicators(inputs["BTC/USDT"].copy(), {"pair": "BTC/USDT"})
    weight = actual.set_index("date").weight
    assert weight.loc[dates[gap - 1]] > 0
    # A missing close invalidates two daily returns; require thirty
    # consecutive completed returns before taking portfolio risk again.
    assert weight.reindex(dates[gap:gap + 31]).fillna(0).eq(0).all()
    assert weight.loc[dates[gap + 31]] > 0
