"""LLM advisor for aq_loop: propose the next sweep batch from results so far.

Reads queue.json (baseline, done items with metrics, champion) and asks the
model for up to 4 NEW single-knob aq items. Output contract is strict JSON;
anything else is rejected and the queue is left untouched.

Only these knobs exist (anything else -> item rejected):
  stake_double: float in [0.5, 3.0]
  max_entries: int in [1, 6]
  window_minutes: int in [15, 240]

Usage: python3 advise.py /path/to/queue.json   (prints what it appended)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import complete

ALLOWED = {
    "stake_double": (0.5, 3.0),
    "max_entries": (1, 6),
    "window_minutes": (15, 240),
}
# current file defaults: proposing these alone re-runs the baseline
DEFAULTS = {"stake_double": 1.5, "max_entries": 2, "window_minutes": 60}
MAX_NEW = 4

SYSTEM = (
    'You tune a crypto dip-buy strategy execution knobs. '
    'Reply with NOTHING but a JSON object of this shape: '
    '{"items": [{"id": "short-name", "aq": {"knob": number}, '
    '"why": "one line"}]}. Between 0 and 4 items. Prefer single-knob '
    'changes near winners. Never repeat a tried aq dict. '
    'No prose, no markdown, JSON only.'
)


def build_prompt(q: dict) -> str:
    lines = [
        f"baseline totals by window: {json.dumps(q['baseline'])}",
        f"champion so far: {q.get('champion')}",
        "tried items (id, aq, status, robust_sharpe, worst_dd):",
    ]
    for i in q["items"]:
        if i["status"] != "pending":
            lines.append(
                f"  {i['id']} {json.dumps(i.get('aq', {}))} {i['status']} "
                f"sharpe={i.get('robust_sharpe')} dd={i.get('worst_dd')}"
            )
    lines.append(
        "Context: stake_double scales double-signal size; max_entries caps "
        "entries per window_minutes (crash-leg guard). Winter 2022 is the "
        "binding window (thin signals); 2024-26 is the volume window. "
        "Propose the next batch."
    )
    return "\n".join(lines)


def validate(items: list, tried: set) -> list:
    out = []
    for it in items[:MAX_NEW]:
        if not isinstance(it, dict):
            continue
        aq = it.get("aq")
        iid = it.get("id", "")
        if not isinstance(aq, dict) or not iid or not isinstance(iid, str):
            continue
        if len(aq) != 1:
            continue
        (k, v), = aq.items()
        if k not in ALLOWED or not isinstance(v, (int, float)):
            continue
        lo, hi = ALLOWED[k]
        if not (lo <= v <= hi):
            continue
        if k == "max_entries" and int(v) != v:
            continue
        if len(aq) == 1 and DEFAULTS.get(k) == v:
            continue  # identical to current file default: wasteful re-run
        key = json.dumps(aq, sort_keys=True)
        if key in tried:
            continue
        tried.add(key)
        out.append({"id": iid[:24], "aq": {k: v}, "status": "pending",
                    "why": str(it.get("why", ""))[:200]})
    return out


def main() -> None:
    qpath = Path(sys.argv[1])
    q = json.loads(qpath.read_text())
    tried = {json.dumps(i.get("aq", {}), sort_keys=True)
             for i in q["items"] if i.get("aq")}
    prompt = build_prompt(q)
    raw = complete(prompt, system=SYSTEM, max_tokens=6000)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise SystemExit(f"advisor returned non-JSON: {raw[:200]}")
    data = json.loads(raw[start:end + 1])
    new_items = validate(data.get("items", []), tried)
    if not new_items:
        print("advisor: no valid items, queue untouched")
        return
    q["items"].extend(new_items)
    qpath.write_text(json.dumps(q, indent=1))
    for it in new_items:
        print(f"queued {it['id']}: {json.dumps(it['aq'])} ({it['why']})")


if __name__ == "__main__":
    main()
