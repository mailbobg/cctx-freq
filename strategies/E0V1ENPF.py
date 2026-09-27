"""E0V1EN with liquidity-aware (phantom-fill) sizing — see phantom_fill.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from E0V1EN import E0V1EN  # noqa: E402
from phantom_fill import PhantomFillMixin  # noqa: E402


class E0V1ENPF(PhantomFillMixin, E0V1EN):
    pass
