"""A Freqtrade form must reproduce the frozen portfolio weights causally."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

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
