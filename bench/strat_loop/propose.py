"""LLM strategist for strat_loop: propose whole new strategy files.

Reads queue.json (parent, baseline, champion, tried items) plus the parent
strategy source, asks the model for up to MAX_NEW=2 NEW single-file strategies.
Each model call yields ONE file; output contract per file:

  # FILE: E1V1xxxH.py
  # WHY: one line hypothesis
  ```python
  <full strategy source, class == filename, IStrategy V3>
  ```

Files passing validate.py are written to user_data/strategies/ and queued as
pending. Invalid proposals are queued as done-fail-validate with reasons
(code never touches the strategies dir, so freqtrade never loads it).

Usage: python3 propose.py /path/to/queue.json
"""

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "aq_loop"))
from llm import complete  # noqa: E402

from validate import validate_file  # noqa: E402

MAX_NEW = 2
MAX_TOKENS = 12000

SYSTEM = (
    "You write freqtrade IStrategy files. Reply with EXACTLY this shape, nothing else: "
    "first line `# FILE: E1V1xxxH.py` (E1V1 + 1-16 uppercase alnum + H), "
    "second line `# WHY: one sentence hypothesis`, then ONE python fenced block "
    "with the COMPLETE strategy file. Hard constraints: single self-contained file, "
    "class name == filename stem, INTERFACE_VERSION=3, timeframe 1h, "
    "startup_candle_count<=400, must define populate_indicators and "
    "populate_entry_trend, docstring first line holds `Parent: <file> + change`. "
    "No local imports, no pandas_ta, no exec/eval/open/network/shell. "
    "No lookahead: never use future data (shifted compares only on past bars; "
    "informative merges are already forward-filled by freqtrade). "
    "Pair basket is fixed to BTC/USDT SOL/USDT AVAX/USDT spot. "
    "No prose outside the 2 header lines + 1 code block."
)

FILE_RE = re.compile(r"^#\s*FILE:\s*(E1V1[A-Z0-9]{1,16}H)\.py\s*$", re.M)
WHY_RE = re.compile(r"^#\s*WHY:\s*(.+?)\s*$", re.M)
CODE_RE = re.compile(r"```python\n(.*?)```", re.S)


def strategies_dir(qpath: Path) -> Path:
    # <user_data>/bench/strat_loop/queue.json -> <user_data>/strategies
    return qpath.parent.parent.parent / "strategies"


def build_prompt(q: dict, parent_src: str) -> str:
    lines = [
        f"baseline totals by window: {json.dumps(q['baseline'])}",
        f"parent strategy file: {q['parent']}.py",
        f"champion so far: {q.get('champion')}",
        "tried files (file, status, sharpe, dd, reasons):",
    ]
    for i in q["items"]:
        if i["status"] != "pending":
            det = " ".join([
                str(i.get("file") or i.get("id")),
                str(i["status"]),
                f"sharpe={i.get('robust_sharpe')}",
                f"dd={i.get('worst_dd')}",
                f"reasons={json.dumps(i.get('reasons', []) + ([i.get('error', '')] if i.get('error') else []))[:300]}",
            ])
            lines.append(f"  {det}")
    lines += [
        "Context: 1h dip-buy on BTC/SOL/AVAX spot. Winter 2022 is the binding "
        "window (thin signals, must stay >0); 2024-26 is the volume window. "
        "Parent doubles logic + crash guard is the reference; fork it with ONE "
        "clear signal/exit/sizing change. Never repeat a tried filename or logic.",
        "--- parent source ---",
        parent_src,
    ]
    return "\n".join(lines)


def extract_one(raw: str) -> tuple[str, str, str] | None:
    fm = FILE_RE.search(raw)
    wm = WHY_RE.search(raw)
    cm = CODE_RE.search(raw)
    if not (fm and cm):
        return None
    return fm.group(1), (wm.group(1).strip()[:200] if wm else ""), cm.group(1)


def main() -> None:
    qpath = Path(sys.argv[1])
    q = json.loads(qpath.read_text())
    sdir = strategies_dir(qpath)
    parent_src = (sdir / f"{q['parent']}.py").read_text()
    tried_names = {i.get("file") for i in q["items"] if i.get("file")}
    tried_hash = set()
    for i in q["items"]:
        h = i.get("code_sha")
        if h:
            tried_hash.add(h)
    prompt = build_prompt(q, parent_src)
    added = 0
    for _ in range(MAX_NEW):
        try:
            raw = complete(prompt, system=SYSTEM, max_tokens=MAX_TOKENS)
        except Exception as e:
            print(f"propose: LLM call failed: {e}")
            break
        ext = extract_one(raw)
        if not ext:
            print(f"propose: unparseable reply, skipped ({raw[:120]!r})")
            continue
        fname, why, code = ext
        if fname in tried_names:
            print(f"propose: duplicate filename {fname}, skipped")
            continue
        sha = hashlib.sha256(code.encode()).hexdigest()[:16]
        if sha in tried_hash:
            print(f"propose: duplicate logic {fname} (sha {sha}), skipped")
            continue
        with tempfile.TemporaryDirectory() as td:
            tmp = str(Path(td) / f"{fname}.py")
            Path(tmp).write_text(code)
            ok, reasons = validate_file(tmp)
        if not ok:
            q["items"].append({"id": fname.lower(), "file": fname,
                               "status": "done-fail-validate",
                               "why": why, "reasons": reasons, "code_sha": sha})
            print(f"propose: {fname} FAIL-validate: {reasons[0] if reasons else ''}")
            tried_names.add(fname)
            tried_hash.add(sha)
            continue
        # valid: move into strategies dir (validate class==filename already)
        dest = sdir / f"{fname}.py"
        if dest.exists():
            print(f"propose: {fname}.py already on disk, skipped")
            tried_names.add(fname)
            continue
        dest.write_text(code)
        # compile check on final path
        ok2, reasons2 = validate_file(dest)
        if not ok2:
            dest.unlink(missing_ok=True)
            q["items"].append({"id": fname.lower(), "file": fname,
                               "status": "done-fail-validate",
                               "why": why, "reasons": reasons2, "code_sha": sha})
            print(f"propose: {fname} FAIL-validate@dest: {reasons2[0]}")
            continue
        q["items"].append({"id": fname.lower(), "file": fname, "strategy": fname,
                           "status": "pending", "why": why, "code_sha": sha})
        print(f"queued {fname}: {why}")
        tried_names.add(fname)
        tried_hash.add(sha)
        added += 1
    qpath.write_text(json.dumps(q, indent=1))
    if not added:
        print("propose: no valid files, queue holds only fail-validate records")


if __name__ == "__main__":
    main()
