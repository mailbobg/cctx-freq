"""Research R2: isolate a 5% per-name cost cap, preserve loss additions.

Not selected or deployed. Protocol: bench/review_loop_20261004/PROTOCOL.md.
The cap is on invested cost; it does not cap marked value or portfolio drawdown.
"""

from NostalgiaForInfinityX8Budget import NostalgiaForInfinityX8Budget


class NostalgiaForInfinityX8CostCap(NostalgiaForInfinityX8Budget):
    position_budget = .05
    add_loss_floor = float("-inf")
