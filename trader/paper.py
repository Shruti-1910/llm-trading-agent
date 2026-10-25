"""Paper trading of the trend-following edge on live Delta India 1h candles (no orders, no money).

Approved by the user 2026-10-25:
- Same rules, fees and funding as the backtest (`[edges.trend]`, `[fees]`).
- Simulated account: `[paper] start_capital`, risk `risk_pct` of equity per trade, position
  value capped at `max_notional_pct` of equity (the plan's 10% rule).
- Only daily closes after the paper start time can open trades.
- Graduation: >= graduate_min_trades closed trades, positive after fees and funding, and the
  worst drawdown no deeper than -pause_drawdown_r. Reaching that drawdown pauses new entries
  until the user runs `paper resume`.

Every run recomputes all paper trades from the start time over the stored candles, so it is
deterministic and a missed run loses nothing.
"""
import json
import sqlite3
import time
from dataclasses import dataclass, field

from trader import edges

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_trades (
    entry_time   INTEGER PRIMARY KEY,
    direction    TEXT NOT NULL,
    entry        REAL NOT NULL,
    initial_stop REAL NOT NULL,
    status       TEXT NOT NULL,      -- open | closed
    exit_time    INTEGER,
    exit_price   REAL,
    exit_reason  TEXT,
    current_stop REAL,
    size_btc     REAL NOT NULL,
    notional     REAL NOT NULL,
    risk_usd     REAL NOT NULL,
    net_r        REAL NOT NULL,
    pnl_usd      REAL NOT NULL,
    snapshot     TEXT
);
"""


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM paper_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO paper_state VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def stored_trades(self):
        cols = [d[1] for d in self.db.execute("PRAGMA table_info(paper_trades)")]
        return {r[0]: dict(zip(cols, r)) for r in
                self.db.execute("SELECT * FROM paper_trades ORDER BY entry_time")}

    def save_trades(self, rows):
        self.db.execute("DELETE FROM paper_trades")
        for r in rows:
            self.db.execute(f"INSERT INTO paper_trades ({', '.join(r)}) VALUES "
                            f"({', '.join('?' * len(r))})", list(r.values()))
        self.db.commit()


@dataclass
class Book:
    """Result of replaying paper trading up to the latest candle."""
    rows: list = field(default_factory=list)   # one dict per trade, oldest first
    equity: float = 0.0
    net_r: float = 0.0
    max_drawdown_r: float = 0.0
    paused_since: int = None


def replay(ctx, cfg, start_time, resumes):
    """Recompute every paper trade since start_time. `resumes`: times the user resumed."""
    p, fees = cfg["paper"], cfg["fees"]
    book = Book(equity=p["start_capital"])

    def pause_time(trades, t):
        """When new entries were paused (None = not paused) as of time t. Paused when the
        drawdown over closed trades reaches -pause_drawdown_r; a resume clears it and resets
        the peak."""
        peak = cum = 0.0
        paused = None
        for tr in trades:
            if tr.exit_reason == "open":
                continue
            exit_t = int(ctx.t[tr.exit_index])
            if paused is not None and any(paused <= r < exit_t for r in resumes):
                paused, peak = None, cum
            cum += tr.net_r
            peak = max(peak, cum)
            if paused is None and cum - peak <= -p["pause_drawdown_r"]:
                paused = exit_t
        if paused is not None and any(paused <= r <= t for r in resumes):
            paused = None
        return paused

    def can_open(trades, t):
        return pause_time(trades, t) is None

    trades = edges.trend_trades(ctx, cfg["edges"]["trend"], fees, start_time, can_open)
    book.paused_since = pause_time(trades, int(ctx.t[-1]))
    peak = cum = 0.0
    for tr in trades:
        risk_target = book.equity * p["risk_pct"]
        dist = abs(tr.entry - tr.stop)
        size = risk_target / dist
        if size * tr.entry > p["max_notional_pct"] * book.equity:
            size = p["max_notional_pct"] * book.equity / tr.entry
        risk_usd = size * dist
        pnl = tr.net_r * risk_usd
        closed = tr.exit_reason != "open"
        book.rows.append({
            "entry_time": tr.entry_time, "direction": tr.direction, "entry": tr.entry,
            "initial_stop": tr.stop, "status": "closed" if closed else "open",
            "exit_time": int(ctx.t[tr.exit_index]) if closed else None,
            "exit_price": tr.exit_price if closed else None,
            "exit_reason": tr.exit_reason if closed else None,
            "current_stop": tr.current_stop, "size_btc": size, "notional": size * tr.entry,
            "risk_usd": risk_usd, "net_r": tr.net_r, "pnl_usd": pnl, "snapshot": None,
            "_index": (tr.entry_index, tr.exit_index), "_mark": tr.exit_price})
        if closed:
            book.equity += pnl
            cum += tr.net_r
            peak = max(peak, cum)
            book.max_drawdown_r = min(book.max_drawdown_r, cum - peak)
    book.net_r = cum
    return book


def graduation(book, cfg):
    p = cfg["paper"]
    closed = [r for r in book.rows if r["status"] == "closed"]
    checks = [
        (f"at least {p['graduate_min_trades']} closed trades", len(closed) >= p["graduate_min_trades"],
         f"{len(closed)}"),
        ("positive after fees and funding", book.net_r > 0, f"{book.net_r:+.2f} R"),
        (f"worst drawdown no deeper than -{p['pause_drawdown_r']} R",
         book.max_drawdown_r > -p["pause_drawdown_r"], f"{book.max_drawdown_r:.2f} R"),
    ]
    return all(ok for _, ok, _ in checks), checks


def events(old, new_rows):
    """Human-readable changes since the last run (for notifications)."""
    out = []
    for r in new_rows:
        before = old.get(r["entry_time"])
        side = "BUY" if r["direction"] == "UP" else "SELL"
        if before is None:
            out.append(f"New paper {side} at {r['entry']:,.0f}, stop {r['initial_stop']:,.0f}")
            before = {"status": "open"}
        if r["status"] == "closed" and before["status"] == "open":
            out.append(f"Paper {side} closed at {r['exit_price']:,.0f} ({r['exit_reason']}): "
                       f"{r['net_r']:+.2f} R, ${r['pnl_usd']:+,.2f}")
    return out


def now():
    return int(time.time())
