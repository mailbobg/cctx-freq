"""Catalog every local strategy and its existing exports without ranking headlines."""

import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from freqtrade.data.btanalysis import load_backtest_stats

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIELDS = ("timeframe", "INTERFACE_VERSION", "can_short", "use_exit_signal", "trailing_stop",
          "stoploss", "position_adjustment_enable", "startup_candle_count")


def main():
    nodes = {}
    for path in sorted((ROOT / "user_data/strategies").glob("*.py")):
        module = ast.parse(path.read_text())
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            attrs, methods = {}, []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
                if isinstance(item, (ast.Assign, ast.AnnAssign)):
                    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in FIELDS:
                            try:
                                attrs[target.id] = ast.literal_eval(item.value)
                            except (ValueError, TypeError):
                                attrs[target.id] = ast.unparse(item.value)
            nodes[node.name] = {"file": str(path.relative_to(ROOT)), "bases": bases, "own": attrs,
                                "own_methods": methods,
                                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def strategy(name, seen=()):
        if name == "IStrategy":
            return True
        return name in nodes and name not in seen and any(strategy(b, (*seen, name)) for b in nodes[name]["bases"])

    def inherited(name, seen=()):
        attrs, methods = {}, set()
        if name not in nodes or name in seen:
            return attrs, methods
        for base in reversed(nodes[name]["bases"]):
            a, m = inherited(base, (*seen, name)); attrs.update(a); methods.update(m)
        attrs.update(nodes[name]["own"]); methods.update(nodes[name]["own_methods"])
        return attrs, methods

    index = defaultdict(list)
    for meta in sorted((ROOT / "user_data/backtest_results").rglob("*.meta.json")):
        zipped = meta.with_suffix("").with_suffix(".zip")
        if zipped.exists():
            for name in json.loads(meta.read_text()):
                index[name].append(str(zipped.relative_to(ROOT)))
    rows, history, errors = {}, [], []
    for name, node in sorted(nodes.items()):
        if not strategy(name):
            continue
        attrs, methods = inherited(name)
        rows[name] = {**node, "inherited": attrs, "custom_exit": "custom_exit" in methods,
                      "legacy_signal_methods": any(m in methods for m in ("populate_buy_trend", "populate_sell_trend")),
                      "existing_exports": index.get(name, []),
                      "dispatch_warning": attrs.get("use_exit_signal") is False and "custom_exit" in methods}
    for path in sorted({p for paths in index.values() for p in paths}):
        try:
            stats = load_backtest_stats(ROOT / path)
            for name, s in stats["strategy"].items():
                history.append({"strategy": name, "archive": path,
                                **{k: s.get(k) for k in ("backtest_start", "backtest_end", "timeframe", "timeframe_detail",
                                      "starting_balance", "max_open_trades", "total_trades", "profit_total", "max_drawdown_account",
                                      "fee_open", "fee_close", "stoploss", "trailing_stop", "pairlist", "strategy_name")}})
        except Exception as exc:
            errors.append({"archive": path, "error": f"{type(exc).__name__}: {exc}"})
    output = {"strategies": rows, "historical_stats": history, "export_errors": errors,
              "limitations": "Class discovery is not constructor/exchange validation. Export drawdown is closed-trade accounting, not MTM. Old profits are not current-strategy validation."}
    (HERE / "strategy_catalog.json").write_text(json.dumps(output, indent=2, default=str) + "\n")
    print("strategy_classes", len(rows), "export_rows", len(history), "export_errors", len(errors))
    print("disabled_custom_exit", [n for n, r in rows.items() if r["dispatch_warning"]])
    print("without_freqtrade_exports", [n for n, r in rows.items() if not r["existing_exports"]])
    legacy = {"BinHV45", "ClucHAnix_5m", "ClucMay72018", "CombinedBinHAndCluc", "MultiMA_TSL3", "NASOSv5",
              "NotAnotherSMAOffsetStrategy", "OversoldReversion", "ReinforcedSmoothScalp", "SMAOffsetProtectOptV1", "Scalp", "SmoothScalp", "Elliotv8PF"}
    for row in history:
        if row["strategy"] in legacy:
            print({k: row[k] for k in ("strategy", "backtest_start", "backtest_end", "timeframe", "timeframe_detail",
                                      "total_trades", "profit_total", "max_drawdown_account")})


if __name__ == "__main__":
    main()
