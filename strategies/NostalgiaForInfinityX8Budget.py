"""Research only: cap invested cost, suppress loss additions, preserve X8 exits."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from NostalgiaForInfinityX8PF import NostalgiaForInfinityX8PF


class NostalgiaForInfinityX8Budget(NostalgiaForInfinityX8PF):
    position_budget = .15
    add_loss_floor = -.05

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        stake = super().custom_stake_amount(pair, current_time, current_rate, proposed_stake,
                                           min_stake, max_stake, leverage, entry_tag, side, **kwargs)
        stake = min(stake, max_stake, self.wallets.get_total_stake_amount() * self.position_budget)
        return stake if stake >= (min_stake or 0.) else 0.

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit,
                              min_stake, max_stake, current_entry_rate, current_exit_rate,
                              current_entry_profit, current_exit_profit, **kwargs):
        result = super().adjust_trade_position(trade, current_time, current_rate, current_profit,
                                              min_stake, max_stake, current_entry_rate, current_exit_rate,
                                              current_entry_profit, current_exit_profit, **kwargs)
        stake, tag = result if isinstance(result, tuple) else (result, None)
        if stake is None or stake <= 0:
            return result
        if current_profit <= self.add_loss_floor:
            return None
        remaining = self.wallets.get_total_stake_amount() * self.position_budget - trade.stake_amount
        stake = min(stake, remaining, max_stake)
        if stake <= 0 or stake < (min_stake or 0.):
            return None
        return (stake, tag) if isinstance(result, tuple) else stake
