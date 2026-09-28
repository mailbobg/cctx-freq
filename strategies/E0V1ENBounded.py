"""Research only: unchanged dip entries, bounded losses and time in position."""
import sys
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).parent))
from E0V1ENPF import E0V1ENPF


class E0V1ENBounded(E0V1ENPF):
    trailing_stop = False
    minimal_roi = {'0': .03}
    ignore_roi_if_entry_signal = False
    stoploss = -.10

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=24):
            return 'max_hold_24h'
        return super().custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)
