"""Research candidate: covariance-aware portfolio cap on the fixed weekly C rule."""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from RiskHoldC import RiskHoldC
from portfolio_risk import portfolio_weights


class RiskHoldCPortfolio(RiskHoldC):
    def informative_pairs(self):
        return [('BTC/USDT', '1d'), ('ETH/USDT', '1d')]

    def populate_indicators(self, df, metadata):
        df = super().populate_indicators(df, metadata)
        series = {}
        for pair in ('BTC/USDT', 'ETH/USDT'):
            frame = df if pair == metadata['pair'] else self.dp.get_pair_dataframe(pair, '1d')
            series[pair] = frame.set_index('date')['close']
        closes = pd.DataFrame(series).sort_index().dropna()
        weights = portfolio_weights(closes)
        df['weight'] = df['date'].map(weights[metadata['pair']]).fillna(0.)
        return df
