"""L2P: L2 with Binance PREMARKET (Pre-IPO / TradFi) perpetuals added to the event set.

Spec: bench/specs/2026-09-29-l2-listings-forward.md (amendment 2026-09-29, made before the first event). Everything
else is L2 unchanged; the review reports COIN and PREMARKET events separately.
"""

from ShortNewListingsL2 import STABLE, ShortNewListingsL2

TYPES = {"COIN", "PREMARKET"}
CONTRACTS = {"PERPETUAL", "TRADIFI_PERPETUAL"}


class ShortNewListingsL2P(ShortNewListingsL2):
    def _is_candidate(self, pair: str) -> bool:
        if pair == "BTC/USDT:USDT" or not pair.endswith("/USDT:USDT"):
            return False
        m = self.dp.market(pair) if self.dp else None
        if not m:
            return False
        info = m.get("info", {}) or {}
        if info.get("underlyingType", "COIN") not in TYPES or info.get("contractType", "PERPETUAL") not in CONTRACTS:
            return False
        return m.get("base", "").upper() not in STABLE
