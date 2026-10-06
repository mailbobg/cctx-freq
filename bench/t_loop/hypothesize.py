"""Autonomous researcher for t_loop: the loop invents its own hypotheses.

Runs at every refill BEFORE propose.py. Reviews current and four recent archived rounds (tried files,
metrics, failure reasons, champion lineage, past explore briefs) and decides:

- EXPLOIT: recent passes -> refine winners (fork champion, single clear change)
- EXPLORE: stagnation (tail of fails, champion stale) -> pivot to a DIFFERENT
  signal family. Must name what to avoid (tried directions) and what to try.

Output: <loop>/explore.json {mode, brief, why}. propose.py enforces it.
No user input anywhere in this path.

Usage: python3 hypothesize.py /path/to/queue.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "aq_loop"))
from llm import complete  # noqa: E402
from research_control import generation_allowed  # noqa: E402

SYSTEM = (
    "You are the research lead of an automated crypto strategy lab. "
    "Reply with NOTHING but JSON: "
    '{"mode": "explore" or "exploit", "brief": "2-4 sentences: concrete direction '
    'for the next batch (signals, filters, exits, sizing)", '
    '"why": "one line: what history justifies this"}. '
    "Doctrine (trend family vs buy-and-hold baseline): "
    "(1) Bear-2022 is won by sitting out: 0% beats BTC -65%, longs bleed; "
    "reward sitting-out logic, punish bear longs. Bull years must beat BTC. "
    "(2) Clone detection: items with near-identical totals/trades are the same logic renamed; "
    "in explore mode demand a DIFFERENT trade-count regime or signal family, not a rename. "
    "(3) Risk-adjusted: prefer higher full-period total per unit of worst-dd; "
    "a +30% return for 2x dd is a bad trade. "
    "(4) Frequency: dead-quiet strategies waste live slots; demand >=10 trades "
    "in EVERY window and prefer >=0.5 trades/week in the volume window. "
    "In explore mode the brief MUST name a signal family/logic NOT in the tried "
    "list (different indicators, different entry thesis, or different regime "
    "handling). In exploit mode the brief refines the champion with one change. "
    "No prose, no markdown, JSON only."
)


def tail_statuses(items: list, n: int = 4) -> list:
    return [i.get("status", "") for i in items if i.get("status") != "pending"][-n:]


def build_prompt(q: dict, parent: str) -> str:
    lines = [
        f"parent: {parent}.py | champion: {q.get('champion')} | "
        f"refills used: {q.get('refills', 0)} | baseline: {json.dumps(q.get('baseline'))}",
        "history (file, status, sharpe, dd, why/reasons):",
    ]
    for i in q["items"]:
        if i["status"] == "pending":
            continue
        lines.append(
            f"  {i.get('file')} {i['status']} sharpe={i.get('robust_sharpe')} "
            f"dd={i.get('worst_dd')} totals={json.dumps(i.get('totals', {}))} "
            f"why={i.get('why', '')[:120]} "
            f"reasons={json.dumps(i.get('reasons', []))[:160]}")
    lines.append("past explore briefs:")
    for e in q.get("explore_log", [])[-4:]:
        lines.append(f"  r{e.get('refill')}:{e.get('mode')}: {e.get('brief', '')[:160]}")
    tail = tail_statuses(q["items"])
    lines.append(
        f"recent tail: {tail}. "
        "If the tail is all fails or the champion is stale, EXPLORE a new direction; "
        "if recent items pass, EXPLOIT the winner. Propose the next direction.")
    return "\n".join(lines)


def main() -> None:
    qpath = Path(sys.argv[1])
    q = json.loads(qpath.read_text())
    if not generation_allowed(qpath):
        raise SystemExit("research task or global gate disabled")
    tried = {i.get("file") for i in q["items"] if i.get("file")}
    prompt = build_prompt(q, q["parent"])
    archives = sorted((qpath.parent / "archive").glob("r*/queue.json"), key=lambda p: int(p.parent.name[1:]))[-4:]
    for path in archives:
        archived = json.loads(path.read_text())
        prompt += "\nARCHIVED ROUND:\n" + build_prompt(archived, archived["parent"])
    try:
        # Muse includes reasoning tokens in this budget; 1500 can end before JSON.
        raw = complete(prompt, system=SYSTEM, max_tokens=8000)
    except Exception as e:
        print(f"hypothesize: LLM call failed: {e}")
        raise SystemExit(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        print(f"hypothesize: non-JSON reply, skipped ({raw[:120]!r})")
        raise SystemExit(1)
    try:
        data = json.loads(raw[start:end + 1])
        mode = data.get("mode", "exploit")
        brief = str(data.get("brief", "")).strip()[:1200]
        why = str(data.get("why", "")).strip()[:200]
        assert mode in ("explore", "exploit") and brief
    except Exception as e:
        print(f"hypothesize: bad shape ({e}), skipped")
        raise SystemExit(1)
    if not generation_allowed(qpath):
        raise SystemExit("research paused during generation; result discarded")
    out = {"mode": mode, "brief": brief, "why": why, "round": q.get("round", 1), "parent": q["parent"],
           "refill": q.get("refills", 0) + 1, "tried": sorted(tried)}
    (qpath.parent / "explore.json").write_text(json.dumps(out, indent=1))
    q.setdefault("explore_log", []).append(
        {"refill": out["refill"], "mode": mode, "brief": brief, "why": why})
    qpath.write_text(json.dumps(q, indent=1))
    print(f"hypothesis [{mode}]: {brief[:120]}")


if __name__ == "__main__":
    main()
