"""NFI X8 with liquidity-aware (phantom-fill) sizing — see phantom_fill.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from NostalgiaForInfinityX8 import NostalgiaForInfinityX8  # noqa: E402
from phantom_fill import PhantomFillMixin  # noqa: E402
from nfi_backtest_state import FreshBacktestStateMixin  # noqa: E402


class NostalgiaForInfinityX8PF(FreshBacktestStateMixin, PhantomFillMixin, NostalgiaForInfinityX8):
    pass
