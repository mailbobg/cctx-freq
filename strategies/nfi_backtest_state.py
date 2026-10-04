"""Independent historical profit-target state; live persistence is left to NFI."""


class MemoryProfitCache:
    def __init__(self):
        self.data = {}

    def save(self):
        pass

    def load(self):
        pass


class FreshBacktestStateMixin:
    def __init__(self, config):
        mode = config.get("runmode")
        mode = getattr(mode, "value", mode)
        self._isolated_profit_targets = mode in ("backtest", "hyperopt")
        if self._isolated_profit_targets:
            # Set this before NFI's constructor can read its on-disk cache.
            self.target_profit_cache = MemoryProfitCache()
        super().__init__(config)

    def bot_start(self, **kwargs):
        if self._isolated_profit_targets:
            # The same strategy object can be reused for another simulation.
            self.target_profit_cache = MemoryProfitCache()
        super().bot_start(**kwargs)
