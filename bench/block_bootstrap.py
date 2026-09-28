"""Circular daily-block bootstrap of additive portfolio P&L, not IID trades.

Preserves within-block dependence only; not a model-selection-adjusted CI.
Input must contain every calendar day, including zero-P&L days.
"""
import numpy as np


def block_interval(pnl, block_days, draws=5000, seed=0):
    pnl = np.asarray(pnl, dtype=float)
    if not len(pnl) or not np.isfinite(pnl).all() or block_days < 1:
        raise ValueError('Finite daily P&L and a positive block size are required')
    n = len(pnl)
    block_days = min(block_days, n)
    rng = np.random.default_rng(seed)
    totals = np.empty(draws)
    # Bounded memory even on long daily histories.
    for i in range(draws):
        starts = rng.integers(0, n, size=int(np.ceil(n / block_days)))
        idx = (starts[:, None] + np.arange(block_days)) % n
        totals[i] = pnl[idx.ravel()[:n]].sum()
    return tuple(float(x) for x in np.percentile(totals, [2.5, 97.5]))
