"""Analysis variants (not for dry-run): E0V1ENPF and X12 with the close-based trailing stop (close_trail.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from close_trail import CloseTrailMixin  # noqa: E402
from E0V1ENPF import E0V1ENPF  # noqa: E402
from E0V1ENX12 import E0V1ENX12  # noqa: E402


class E0V1ENPFCT(CloseTrailMixin, E0V1ENPF):
    pass


class E0V1ENX12CT(CloseTrailMixin, E0V1ENX12):
    pass
