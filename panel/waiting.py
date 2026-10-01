"""One-line "what is this bot waiting for" notes for event-driven bots (shown on the 8090 console).

A bot that trades on a schedule (a weekly decision) or on an external event (a new Binance listing) is silent most of
the time; the note says when the next chance is, so silence can be told apart from a dead bot.
"""

from datetime import datetime, timedelta, timezone

WEEKDAYS = "一二三四五六日"
ENTRY_DELAY = timedelta(days=3)          # new-listing bots enter at D0 + 3 days 00:00 UTC, for 24 h
ENTRY_WINDOW = timedelta(days=1)


def _left(delta: timedelta) -> str:
    s = max(0, int(delta.total_seconds()))
    d, h = s // 86400, s % 86400 // 3600
    return f"{d} 天 {h} 小时" if d else f"{h} 小时 {s % 3600 // 60} 分钟"


def weekly_note(now: datetime, weekday: int, hour: int, text: str = "") -> str:
    """Next weekly decision at `weekday` (Monday = 0) `hour`:00 UTC."""
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=(weekday - now.weekday()) % 7)
    if nxt <= now:
        nxt += timedelta(days=7)
    base = f"下次决定 {nxt:%m-%d %H:%M} UTC（周{WEEKDAYS[nxt.weekday()]}），还有 {_left(nxt - now)}"
    return f"{base}。{text}" if text else base


def listings_note(now: datetime, rows: list[tuple[float, str, str]], types: set[str]) -> str:
    """rows: (onboard epoch seconds, base asset, underlyingType) of every listed perpetual; newest decides."""
    mine = sorted((r for r in rows if r[2] in types), key=lambda r: r[0])
    if not mine:
        return "暂时读不到新合约列表"
    live, upcoming = [], []
    for ts, sym, _ in mine:
        d0 = datetime.fromtimestamp(ts, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = d0 + ENTRY_DELAY, d0 + ENTRY_DELAY + ENTRY_WINDOW
        if start <= now < end:
            live.append(f"{sym}（窗口至 {end:%m-%d %H:%M} UTC）")
        elif now < start:
            upcoming.append(f"{sym}（{start:%m-%d %H:%M} UTC 入场）")
    parts = []
    if live:
        parts.append("入场窗口进行中：" + "、".join(live))
    if upcoming:
        parts.append("即将入场：" + "、".join(upcoming))
    if parts:
        return "；".join(parts)
    ts, sym, kind = mine[-1]
    age = now - datetime.fromtimestamp(ts, timezone.utc)
    return f"等待新币上线 · 最近一个是 {sym}（{age.days} 天前，入场窗口已过）"
