"""Round rollover for 24/7 operation: archive finished round, open the next one.

Called by loop.sh when the queue drains with refills exhausted, or when the
queue goes dead (6 consecutive fails). Never exits nonzero on bot-switch
failure (the loop must keep running); prints what it did.

- archive/r<N>/ holds queue.json + results-strat.tsv + per-item result dirs
- next round parent = round champion strategy (or same parent if none passed)
- live bot e1v1dbl2x switches to the round champion with a fresh db, restarted
  through the panel API on localhost (panel must be up; else logged + skipped)

Usage: python3 rollover.py /path/to/queue.json <reason>
"""

import json
import shutil
import sys
import urllib.request
from pathlib import Path

UD = Path("/opt/ccxt/user_data")
BOT = "e1v1dbl2x"


def main() -> None:
    qpath = Path(sys.argv[1])
    reason = sys.argv[2] if len(sys.argv) > 2 else "exhausted"
    slo = qpath.parent
    q = json.loads(qpath.read_text())
    n = int(q.get("round", 1))
    arch = slo / "archive" / f"r{n}"
    arch.mkdir(parents=True, exist_ok=True)
    shutil.copy2(qpath, arch / "queue.json")
    tsv = slo / "results-strat.tsv"
    if tsv.exists():
        shutil.copy2(tsv, arch / "results-strat.tsv")
    resdir = slo / "results"
    if resdir.exists():
        for sub in sorted(resdir.iterdir()):
            shutil.move(str(sub), str(arch / sub.name))
    resdir.mkdir(parents=True, exist_ok=True)

    champ_id = q.get("champion")
    champ_strat = None
    if champ_id:
        for i in q["items"]:
            if i["id"] == champ_id:
                champ_strat = i.get("strategy")
                break
    new_parent = (sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None) or champ_strat or q["parent"]
    brief = (sys.argv[4] if len(sys.argv) > 4 else None) or None
    nq = {"parent": new_parent, "round": n + 1, "brief": brief,
          "baseline": q.get("baseline", {}), "windows": q.get("windows", []), "pairs": q.get("pairs", []),
          "timeframe": q.get("timeframe", "4h"), "items": [], "champion": None,
          "refills": 0, "consecutive_fail": 0, "status": "open"}
    qpath.write_text(json.dumps(nq, indent=1))

    bot_note = "no champion: bot untouched"
    # t_loop v1: no auto live-switch; champion started manually from its stopped entry
    if champ_strat:
        bot_note = f"champion {champ_strat} awaits manual start (no auto-switch in t_loop v1)"
    print(f"rollover r{n}->{n + 1} ({reason}) parent={new_parent} | {bot_note}")


if __name__ == "__main__":
    main()
