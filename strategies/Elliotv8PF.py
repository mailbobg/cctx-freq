"""Elliotv8 with liquidity-aware (phantom-fill) sizing — see phantom_fill.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ElliotV8 import Elliotv8  # noqa: E402
from phantom_fill import PhantomFillMixin  # noqa: E402


class Elliotv8PF(PhantomFillMixin, Elliotv8):
    pass
