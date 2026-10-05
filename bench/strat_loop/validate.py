"""Static validation for strat_loop LLM-generated strategy files.

Usage: python3 validate.py /path/to/E1V1xxxH.py
Prints JSON {"ok": bool, "reasons": [...]} to stdout, exit 0 if ok else 2.
Also importable: from validate import validate_file(path) -> (ok, reasons).
"""

import py_compile
import re
import sys
from pathlib import Path

# filename: PiE1V1 + alnum signal tag + H, e.g. PiE1V1RSICTIH.py
# ("Pi-" with a hyphen is illegal in Python class names, so Pi is prefixed directly)
NAME_RE = re.compile(r"^PiE1V1[A-Z0-9]{1,12}H$")
CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(\s*IStrategy\s*\)", re.M)
TIMEFRAME_RE = re.compile(r"""timeframe\s*=\s*["']1h["']""")
STARTUP_RE = re.compile(r"startup_candle_count\s*=\s*(\d+)")
INTERFACE_RE = re.compile(r"INTERFACE_VERSION\s*=\s*3")

FORBIDDEN_RES = [
    (re.compile(r"^\s*(import|from)\s+\."), "relative import"),
    (re.compile(r"^\s*(import|from)\s+(aq_loop|strat_loop|user_data)"), "local harness import"),
    (re.compile(r"^\s*(import|from)\s+E[01]\w*"), "import of another strategy file"),
    (re.compile(r"import\s+pandas_ta|from\s+pandas_ta|pandas_ta\."), "pandas_ta dependency (not installed)"),
    (re.compile(r"(?<![\w.])exec\s*\("), "exec() banned"),
    (re.compile(r"(?<![\w.])eval\s*\("), "eval() banned"),
    (re.compile(r"__import__"), "__import__ banned"),
    (re.compile(r"os\s*\.\s*system|subprocess|socket|urllib|requests"), "network/shell banned"),
    (re.compile(r"\bopen\s*\("), "open() banned in strategy"),
]


def validate_file(path: str | Path) -> tuple[bool, list[str]]:
    p = Path(path)
    reasons: list[str] = []
    stem = p.stem
    if not NAME_RE.match(stem):
        reasons.append(f"filename {stem}.py must match E1V1<1-16 ALNUM>H.py")
    try:
        text = p.read_text()
    except OSError as e:
        return False, [f"cannot read: {e}"]
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as e:
        return False, [f"syntax error: {e}"]
    m = CLASS_RE.search(text)
    if not m:
        reasons.append("no `class <Name>(IStrategy)` found")
    elif m.group(1) != stem:
        reasons.append(f"class {m.group(1)} != filename {stem} (harness requires equal)")
    if not INTERFACE_RE.search(text):
        reasons.append("missing INTERFACE_VERSION = 3")
    if not TIMEFRAME_RE.search(text):
        reasons.append('timeframe must be "1h"')
    m = STARTUP_RE.search(text)
    if not m:
        reasons.append("missing startup_candle_count")
    elif int(m.group(1)) > 400:
        reasons.append(f"startup_candle_count {m.group(1)} > 400")
    if "def populate_entry_trend" not in text:
        reasons.append("missing populate_entry_trend")
    if "def populate_indicators" not in text:
        reasons.append("missing populate_indicators")
    if "Parent:" not in text:
        reasons.append("docstring must contain a `Parent: ...` lineage line")
    for rx, label in FORBIDDEN_RES:
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                reasons.append(f"line {i}: {label}: {line.strip()[:100]}")
                break
    return (len(reasons) == 0), reasons


def main() -> None:
    import json

    ok, reasons = validate_file(sys.argv[1])
    print(json.dumps({"ok": ok, "reasons": reasons}))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
