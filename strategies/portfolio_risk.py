"""Closed-candle portfolio sizing shared by research and the candidate bot."""
import numpy as np
import pandas as pd


def cap_weights(weights, covariance, vol_target=.40, gross_cap=.80):
    weights = np.asarray(weights, dtype=float)
    covariance = np.asarray(covariance, dtype=float)
    if not np.isfinite(weights).all() or not np.isfinite(covariance).all():
        return np.zeros_like(weights)
    vol = np.sqrt(max(0., weights @ covariance @ weights))
    gross = np.abs(weights).sum()
    scale = min(1., vol_target / vol if vol > 0 else 1.,
                gross_cap / gross if gross > 0 else 1.)
    return weights * scale


def portfolio_weights(closes):
    """Two-asset 30-day TSMOM; same timestamp output uses only past/current closes."""
    returns = np.log(closes).diff()
    vol = returns.rolling(30).std() * np.sqrt(365)
    raw = (.5 * np.minimum(1., .60 / vol)).where(closes / closes.shift(30) > 1., 0.)
    cov = returns.rolling(30).cov() * 365
    out = raw.copy() * 0.
    for date in closes.index:
        matrix = cov.loc[date].reindex(index=closes.columns, columns=closes.columns).to_numpy()
        out.loc[date] = cap_weights(raw.loc[date].to_numpy(), matrix)
    return out
