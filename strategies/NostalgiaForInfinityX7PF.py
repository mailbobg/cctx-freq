"""NFI X7 with liquidity-aware (phantom-fill) sizing — see phantom_fill.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from NostalgiaForInfinityX7 import NostalgiaForInfinityX7  # noqa: E402
from phantom_fill import PhantomFillMixin  # noqa: E402
from nfi_backtest_state import FreshBacktestStateMixin  # noqa: E402


class NostalgiaForInfinityX7PF(FreshBacktestStateMixin, PhantomFillMixin, NostalgiaForInfinityX7):
    pass
