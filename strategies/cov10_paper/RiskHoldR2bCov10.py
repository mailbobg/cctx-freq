"""Research R1: the fixed R2b signal with a10% BTC/ETH covariance budget.

Historical net returns are positive, but the default5% drawdown gate failed.
No running bot uses this class. Protocol: bench/review_loop_20261004/PROTOCOL.md.
"""
import numpy as np
import pandas as pd

from RiskHoldR2b import RiskHoldR2b
from portfolio_risk import cap_weights


class RiskHoldR2bCov10(RiskHoldR2b):
    portfolio_pairs = ("BTC/USDT", "ETH/USDT")
    portfolio_vol_target = .10

    def informative_pairs(self):
        return [(pair, self.timeframe) for pair in self.portfolio_pairs]

    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        pair = metadata["pair"]
        if pair not in self.portfolio_pairs:
            df["weight"] = 0.
            return df
        closes, raw = {}, {}
        for name in self.portfolio_pairs:
            frame = df if name == pair else self.dp.get_pair_dataframe(name, self.timeframe)
            if frame is None or frame.empty:
                df["weight"] = 0.
                return df
            analyzed = RiskHoldR2b.populate_indicators(self, frame.copy(), {"pair": name})
            closes[name] = analyzed.set_index("date")["close"]
            raw[name] = analyzed.set_index("date")["weight"]
        # Keep missing calendar days: compressing them treats a multi-day
        # return as one observation and understates the intended daily risk.
        # Thirty complete returns are required again after a missing close.
        prices = pd.DataFrame(closes).sort_index().asfreq("D")
        weights = pd.DataFrame(raw).reindex(prices.index)
        cov = np.log(prices).diff().rolling(30).cov() * 365
        result = pd.DataFrame(0., index=prices.index, columns=self.portfolio_pairs)
        for day in prices.index:
            matrix = cov.loc[day].reindex(index=self.portfolio_pairs, columns=self.portfolio_pairs).to_numpy()
            result.loc[day] = cap_weights(weights.loc[day], matrix,
                                          vol_target=self.portfolio_vol_target, gross_cap=1.)
        df["weight"] = df["date"].map(result[pair]).fillna(0.)
        return df
