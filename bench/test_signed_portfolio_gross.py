"""A neutral net weight is not a zero gross exposure."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
from portfolio_risk import cap_weights


def test_offsetting_signed_positions_obey_gross_cap():
    weights = cap_weights([2., -2.], np.eye(2) * .001, vol_target=1., gross_cap=.8)
    assert np.abs(weights).sum() == pytest.approx(.8)
    assert weights.tolist() == pytest.approx([.4, -.4])


def test_long_only_budget_is_unchanged():
    weights = cap_weights([1., 1.], np.eye(2) * .001, vol_target=1., gross_cap=.8)
    assert weights.tolist() == pytest.approx([.4, .4])
