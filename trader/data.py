"""Module 1: market data.

Downloads closed candles from Delta Exchange India's public history API
(no API key, no orders) and stores them in SQLite. Updates are incremental:
only candles after the last stored one are fetched.
"""
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

RESOLUTION_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                      "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
MAX_CANDLES_PER_REQUEST = 4000  # Delta caps each history response at 4000 candles

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol     TEXT    NOT NULL,
    resolution TEXT    NOT NULL,
    time       INTEGER NOT NULL,  -- candle open time, unix seconds UTC
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    PRIMARY KEY (symbol, resolution, time)
)
"""


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def http_get_json(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": "trader/2"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


class CandleStore:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute(SCHEMA)

    def last_time(self, symbol, resolution):
        row = self.db.execute(
            "SELECT MAX(time) FROM candles WHERE symbol=? AND resolution=?",
            (symbol, resolution)).fetchone()
        return row[0]

    def first_time(self, symbol, resolution):
        row = self.db.execute(
            "SELECT MIN(time) FROM candles WHERE symbol=? AND resolution=?",
            (symbol, resolution)).fetchone()
        return row[0]

    def insert(self, symbol, resolution, candles):
        self.db.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)",
            [(symbol, resolution, c.time, c.open, c.high, c.low, c.close, c.volume)
             for c in candles])
        self.db.commit()

    def load(self, symbol, resolution, start=None, end=None):
        """Candles in time order, optionally limited to [start, end]."""
        sql = "SELECT time, open, high, low, close, volume FROM candles WHERE symbol=? AND resolution=?"
        args = [symbol, resolution]
        if start is not None:
            sql += " AND time >= ?"
            args.append(start)
        if end is not None:
            sql += " AND time <= ?"
            args.append(end)
        rows = self.db.execute(sql + " ORDER BY time", args).fetchall()
        return [Candle(*r) for r in rows]

    def gaps(self, symbol, resolution):
        """List of (after_time, missing_count) where consecutive candles are not one step apart."""
        step = RESOLUTION_SECONDS[resolution]
        rows = self.db.execute(
            "SELECT time FROM candles WHERE symbol=? AND resolution=? ORDER BY time",
            (symbol, resolution)).fetchall()
        out = []
        for (a,), (b,) in zip(rows, rows[1:]):
            if b - a != step:
                out.append((a, (b - a) // step - 1))
        return out

    def summary(self, symbol, resolution):
        return self.db.execute(
            "SELECT COUNT(*), MIN(time), MAX(time) FROM candles WHERE symbol=? AND resolution=?",
            (symbol, resolution)).fetchone()


class DeltaCandleClient:
    def __init__(self, api_base, get_json=http_get_json):
        self.api_base = api_base.rstrip("/")
        self.get_json = get_json

    def fetch(self, symbol, resolution, start, end):
        """Closed candles with open time in [start, end], oldest first, paging past the 4000 cap."""
        step = RESOLUTION_SECONDS[resolution]
        window = step * MAX_CANDLES_PER_REQUEST
        by_time = {}
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + window - step, end)
            query = urllib.parse.urlencode(
                {"resolution": resolution, "symbol": symbol, "start": cursor, "end": chunk_end})
            payload = self.get_json(f"{self.api_base}/v2/history/candles?{query}")
            if not payload.get("success", True):
                raise RuntimeError(f"Delta API error: {payload.get('error')}")
            for r in payload.get("result") or []:
                t = int(r["time"])
                if start <= t <= end:
                    by_time[t] = Candle(t, float(r["open"]), float(r["high"]), float(r["low"]),
                                        float(r["close"]), float(r.get("volume") or 0))
            cursor = chunk_end + step
        return [by_time[t] for t in sorted(by_time)]


def update(store, client, symbol, resolution, history_days, now=None):
    """Download everything missing: older candles back to `history_days` ago (backfill) and
    newer ones up to the last fully closed candle. Returns count fetched."""
    step = RESOLUTION_SECONDS[resolution]
    now = int(now if now is not None else time.time())
    last_closed_open = (now // step) * step - step  # the candle still forming is excluded
    earliest = ((now - history_days * 86400) // step) * step
    first, last = store.first_time(symbol, resolution), store.last_time(symbol, resolution)
    ranges = []
    if last is None:
        ranges.append((earliest, last_closed_open))
    else:
        if earliest < first:
            ranges.append((earliest, first - step))
        if last + step <= last_closed_open:
            ranges.append((last + step, last_closed_open))
    fetched = 0
    for start, end in ranges:
        candles = client.fetch(symbol, resolution, start, end)
        store.insert(symbol, resolution, candles)
        fetched += len(candles)
    return fetched


def download_range(store, client, api_symbol, store_symbol, resolution, start, end):
    """Fetch [start, end] from `client` and store it under `store_symbol`. Returns count."""
    candles = client.fetch(api_symbol, resolution, start, end)
    store.insert(store_symbol, resolution, candles)
    return len(candles)


def load_research(store, cfg):
    """Delta global candles before the splice date, then Delta India candles."""
    from datetime import datetime, timezone
    r, m = cfg["research"], cfg["market"]
    splice = int(datetime.strptime(r["splice"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    older = store.load(r["global_store_symbol"], r["resolution"], end=splice - 1)
    india = store.load(m["symbol"], r["resolution"], start=splice)
    if older and older[-1].time + RESOLUTION_SECONDS[r["resolution"]] != splice:
        raise RuntimeError("global history doesn't reach the splice date; run data research-history")
    return older + india
