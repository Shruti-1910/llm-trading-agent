"""Multi-market research (approved by the user 2026-10-30): run the BTC trend-following rules,
unchanged, on other markets' DAILY candles.

Same rules as edges.py "trend": at a daily close, BUY if the close is above the highest high of
the previous 20 days and above the 50-day average (SELL: mirror). Initial stop 2 x daily ATR,
trailing stop 3 x ATR behind the best price (never moved away), exit after 30 days. One trade
at a time. On daily candles the stop is checked against each day's high/low (a gap through the
stop exits at that day's open), then the trail is moved using that day's best price.

Costs: crypto = Delta India (taker 0.05% + 18% GST each way, funding 0.01% per 8h held);
everything else = 0.05% each way all-in, no funding.

Data: Binance public daily candles for crypto, Yahoo Finance daily candles for the rest.
Cached as CSV under data/markets/.
"""
import csv
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from trader import edges, indicators, scoring
from trader.setups import DOWN, UP

DAY = 86400

CRYPTO_FEES = {"taker_rate": 0.0005, "gst_rate": 0.18, "funding_rate_8h": 0.0001}
OTHER_FEES = {"taker_rate": 0.0005, "gst_rate": 0.0, "funding_rate_8h": 0.0}

# name -> (source, symbol, group)
MARKETS = {
    "BTC": ("binance", "BTCUSDT", "crypto"),
    "ETH": ("binance", "ETHUSDT", "crypto"),
    "SOL": ("binance", "SOLUSDT", "crypto"),
    "XRP": ("binance", "XRPUSDT", "crypto"),
    "BNB": ("binance", "BNBUSDT", "crypto"),
    "NIFTY": ("yahoo", "^NSEI", "india"),
    "BANKNIFTY": ("yahoo", "^NSEBANK", "india"),
    "USDINR": ("yahoo", "INR=X", "india"),
    "GOLD": ("yahoo", "GC=F", "commodities"),
    "SILVER": ("yahoo", "SI=F", "commodities"),
    "CRUDE": ("yahoo", "CL=F", "commodities"),
}

TRAIN_YEARS = list(range(2019, 2024))     # same verdict years as BTC
CHECK = (2024, 2025)
SEEN = 2026


def fees_for(name):
    return CRYPTO_FEES if MARKETS[name][2] == "crypto" else OTHER_FEES


# ---------------------------------------------------------------- data

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _binance(symbol):
    rows, start = [], 1483228800000          # 2017-01-01
    while True:
        batch = _get(f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}"
                     f"&interval=1d&startTime={start}&limit=1000")
        if not batch:
            break
        rows += [(int(b[0]) // 1000, float(b[1]), float(b[2]), float(b[3]), float(b[4])) for b in batch]
        start = int(batch[-1][0]) + DAY * 1000
        if len(batch) < 1000:
            break
        time.sleep(0.2)
    now_day = int(time.time()) // DAY * DAY
    return [r for r in rows if r[0] < now_day]          # drop today's unfinished candle


def _yahoo(symbol):
    sym = urllib.request.quote(symbol)
    res = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?period1=946684800&period2={int(time.time())}&interval=1d")["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for t, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"]):
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue                          # missing days and non-positive prices (crude, Apr 2020)
        rows.append((int(t), o, h, l, c))
    today = datetime.now(timezone.utc).date()
    return [r for r in rows if datetime.fromtimestamp(r[0], timezone.utc).date() < today]


def load(name, cache_dir, refresh=False):
    """Daily candles as dict of numpy arrays t, o, h, l, c."""
    path = Path(cache_dir) / f"{name}.csv"
    if refresh or not path.exists():
        source, symbol, _ = MARKETS[name]
        rows = _binance(symbol) if source == "binance" else _yahoo(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "open", "high", "low", "close"])
            w.writerows(rows)
    with open(path) as f:
        rows = list(csv.reader(f))[1:]
    a = np.array(rows, dtype=float)
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4]}


# ---------------------------------------------------------------- strategy

def trend_trades(d, e, fees):
    """The approved trend rules on daily candles. Last trade may be 'open' (PENDING)."""
    t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
    atr = indicators.atr(h, l, c, e["atr_days"])
    n, trades, j = len(c), [], 0
    warmup = max(e["breakout_days"], e["average_days"])
    while j < n:
        if j < warmup or np.isnan(atr[j]):
            j += 1
            continue
        hi, lo = h[j - e["breakout_days"]:j].max(), l[j - e["breakout_days"]:j].min()
        avg = c[j - e["average_days"] + 1:j + 1].mean()
        direction = UP if (c[j] > hi and c[j] > avg) else DOWN if (c[j] < lo and c[j] < avg) else None
        if direction is None:
            j += 1
            continue
        trades.append(_run(d, j, direction, atr, e, fees))
        j = trades[-1].exit_index + 1
    return trades


def _run(d, j, direction, atr, e, fees):
    t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
    up = direction == UP
    entry = c[j]
    risk = e["initial_stop_atr"] * atr[j]
    stop = initial = entry - risk if up else entry + risk
    best = entry
    end = len(c) - 1
    last = min(end, j + e["max_days"])
    still_open = last == end and j + e["max_days"] > end
    reason, exit_price, k = "time", None, last
    for k in range(j + 1, last + 1):
        if up and l[k] <= stop:
            exit_price, reason = (o[k] if o[k] <= stop else stop), ("stop" if stop == initial else "trail")
            break
        if not up and h[k] >= stop:
            exit_price, reason = (o[k] if o[k] >= stop else stop), ("stop" if stop == initial else "trail")
            break
        best = max(best, h[k]) if up else min(best, l[k])
        trail = best - e["trail_atr"] * atr[k] if up else best + e["trail_atr"] * atr[k]
        stop = max(stop, trail) if up else min(stop, trail)
    if exit_price is None:
        exit_price, k = c[last], last
        if still_open:
            reason = "open"
    hours = int(t[k] - t[j]) // 3600
    gross = ((exit_price - entry) if up else (entry - exit_price)) / risk
    net = gross - edges._costs_r(entry, risk, hours, fees)
    result = scoring.PENDING if reason == "open" else scoring.CORRECT if net > 0 else scoring.WRONG
    return edges.Trade("trend", direction, j, int(t[j]), entry, initial, None, k, exit_price,
                       reason, result, gross, net, hours, stop)


# ---------------------------------------------------------------- evaluation

def year(ts):
    return datetime.fromtimestamp(ts, timezone.utc).year


def evaluate(trades):
    """Per-year table, period totals and the approved pass rule (same as BTC)."""
    closed = [tr for tr in trades if tr.exit_reason != "open"]
    by_year = {y: edges.summarize([tr for tr in closed if year(tr.entry_time) == y])
               for y in sorted({year(tr.entry_time) for tr in closed})}
    pick = lambda ys: edges.summarize([tr for tr in closed if year(tr.entry_time) in ys])
    train, check, seen = pick(TRAIN_YEARS), pick(CHECK), pick((SEEN,))
    early = pick([y for y in by_year if y < TRAIN_YEARS[0]])
    missing = [y for y in TRAIN_YEARS if y not in by_year]
    ok, why = edges.verdict(by_year, TRAIN_YEARS, train, check)
    if missing:
        why += f" (no trades/data in {', '.join(map(str, missing))})"
    nets = np.array([tr.net_r for tr in closed])
    stats = {}
    if len(nets):
        eq = np.cumsum(nets)
        stats = {"trades": len(nets), "net_total": float(nets.sum()), "net_avg": float(nets.mean()),
                 "t_stat": float(nets.mean() / (nets.std(ddof=1) / np.sqrt(len(nets)))) if len(nets) > 1 else 0.0,
                 "max_dd": float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()),
                 "top5_share": float(np.sort(nets)[-5:].sum() / nets.sum()) if nets.sum() > 0 else None,
                 "win_rate": float((nets > 0).mean())}
    return {"by_year": by_year, "train": train, "check": check, "seen": seen, "early": early,
            "pass": ok, "why": why, "stats": stats, "open": [tr for tr in trades if tr.exit_reason == "open"]}


def portfolio(trades_by_market):
    """Equal risk (1R) per trade in every market, trades summed by exit date."""
    rows = sorted((tr.entry_time + tr.hours * 3600, tr.net_r, m)
                  for m, trs in trades_by_market.items() for tr in trs if tr.exit_reason != "open")
    if not rows:
        return {}
    nets = np.array([r[1] for r in rows])
    eq = np.cumsum(nets)
    by_year = {}
    for ts, r, _ in rows:
        by_year[year(ts)] = by_year.get(year(ts), 0.0) + r
    return {"trades": len(rows), "net_total": float(nets.sum()), "net_avg": float(nets.mean()),
            "max_dd": float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()), "by_year": by_year}
