"""New edge research (approved by the user 2026-10-25): two strategies on 1h candles.

A. Trend following ("trend"): at a daily close, BUY if the close is above the highest daily
   high of the previous 20 days and above the 50-day average (SELL: mirror). Initial stop
   2 x daily ATR. Trailing stop 3 x daily ATR behind the best price reached (never moved
   away). Exit after 30 days at the latest. One trade at a time.
   Correct = closes with a profit after fees and funding; otherwise Wrong.
C. Liquidation overshoot ("overshoot"): a 1h candle with range >= 3 x ATR and volume >= 3 x
   the 20-candle average. If the next candle doesn't extend the move, fade it at that
   candle's close: stop at the extreme candle's low (high), target the midpoint of the
   extreme candle (skipped if that is < 1.5 x risk away), judged within 24 candles.

Both: round-trip taker fees plus funding of `funding_rate_8h` per 8 hours held, charged on
every trade. Daily candles are built from 1h candles and used only once the day has closed.
"""
from dataclasses import dataclass

import numpy as np

from trader import indicators, scoring
from trader.setups import DOWN, UP

DAY = 86400


@dataclass
class Trade:
    edge: str
    direction: str
    entry_index: int
    entry_time: int
    entry: float
    stop: float          # initial stop
    target: float        # None for trend trades (trailing exit)
    exit_index: int
    exit_price: float
    exit_reason: str     # stop | trail | time | target | horizon
    result: str          # CORRECT | WRONG | UNSURE
    gross_r: float
    net_r: float         # after fees and funding
    hours: int
    current_stop: float = None   # trend trades: stop level at exit (or now, if still open)


def daily(t, o, h, l, c, v):
    """Daily candles (UTC) and, for each 1h candle, the index of the last CLOSED day (-1)."""
    days = t // DAY
    starts = np.flatnonzero(np.r_[True, days[1:] != days[:-1]])
    ends = np.r_[starts[1:], len(t)] - 1
    full = (t[ends] - t[starts]) == DAY - 3600           # skip partial days at the edges
    starts, ends = starts[full], ends[full]
    d = {"t": days[starts] * DAY, "o": o[starts], "c": c[ends],
         "h": np.array([h[s:e + 1].max() for s, e in zip(starts, ends)]),
         "l": np.array([l[s:e + 1].min() for s, e in zip(starts, ends)]),
         "v": np.array([v[s:e + 1].sum() for s, e in zip(starts, ends)])}
    index = np.full(len(t), -1)
    for j, e in enumerate(ends):
        index[e:] = j
    d["close_hour"] = ends                                # 1h index where day j closes
    return d, index


def _costs_r(entry, risk, hours, fees):
    fee = 2 * fees["taker_rate"] * (1 + fees["gst_rate"])
    funding = fees["funding_rate_8h"] * hours / 8
    return (fee + funding) * entry / risk


def trend_trades(ctx, cfg, fees, start_time=None, can_open=None):
    """Edge A over the whole series. start_time: only daily closes at/after it may open a
    trade (paper mode). can_open(trades, time) -> bool lets paper mode pause new entries.
    The last trade may be still open (exit_reason "open", result PENDING)."""
    e = cfg
    d, index = daily(ctx.t, ctx.o, ctx.h, ctx.l, ctx.c, ctx.v)
    datr = indicators.atr(d["h"], d["l"], d["c"], e["atr_days"])
    trades = []
    close_hours = set(d["close_hour"].tolist())
    m = 0
    n = len(ctx.c)
    while m < n:
        if m not in close_hours:
            m += 1
            continue
        j = index[m]
        if j < max(e["breakout_days"], e["average_days"]) or np.isnan(datr[j]):
            m += 1
            continue
        if (start_time is not None and ctx.t[m] < start_time) or \
                (can_open is not None and not can_open(trades, int(ctx.t[m]))):
            m += 1
            continue
        hi = d["h"][j - e["breakout_days"]:j].max()
        lo = d["l"][j - e["breakout_days"]:j].min()
        avg = d["c"][j - e["average_days"] + 1:j + 1].mean()
        close = d["c"][j]
        direction = UP if (close > hi and close > avg) else DOWN if (close < lo and close < avg) else None
        if direction is None:
            m += 1
            continue
        trades.append(_run_trend(ctx, m, direction, datr, index, e, fees))
        m = trades[-1].exit_index + 1
    return trades


def _run_trend(ctx, m, direction, datr, index, e, fees):
    up = direction == UP
    entry = ctx.c[m]
    risk = e["initial_stop_atr"] * datr[index[m]]
    stop = entry - risk if up else entry + risk
    initial = stop
    best = entry
    data_end = len(ctx.c) - 1
    last = min(data_end, m + e["max_days"] * 24)
    still_open = last == data_end and m + e["max_days"] * 24 > data_end
    reason, exit_price, k = "time", None, last
    for k in range(m + 1, last + 1):
        o, h, l = ctx.o[k], ctx.h[k], ctx.l[k]
        # stop first (a gap through the stop exits at the open)
        if up and l <= stop:
            exit_price, reason = (o if o <= stop else stop), ("stop" if stop == initial else "trail")
            break
        if not up and h >= stop:
            exit_price, reason = (o if o >= stop else stop), ("stop" if stop == initial else "trail")
            break
        best = max(best, h) if up else min(best, l)
        atr_now = datr[index[k]]
        trail = best - e["trail_atr"] * atr_now if up else best + e["trail_atr"] * atr_now
        stop = max(stop, trail) if up else min(stop, trail)   # never moved away
    if exit_price is None:
        exit_price, k = ctx.c[last], last
        if still_open:          # data ran out before any exit: marked to the latest close
            reason = "open"
    hours = k - m
    gross = ((exit_price - entry) if up else (entry - exit_price)) / risk
    net = gross - _costs_r(entry, risk, hours, fees)
    result = scoring.PENDING if reason == "open" else scoring.CORRECT if net > 0 else scoring.WRONG
    return Trade("trend", direction, m, int(ctx.t[m]), entry, initial, None, k, exit_price, reason,
                 result, gross, net, hours, stop)


def overshoot_trades(ctx, cfg, fees, min_risk_rate):
    """Edge C over the whole series."""
    e = cfg
    out, last_signal = [], {}
    n = len(ctx.c)
    for k in range(ctx.warmup, n - 2):
        rng = ctx.h[k] - ctx.l[k]
        if rng < e["range_atr"] * ctx.atr[k - 1] or ctx.v[k] < e["volume_mult"] * ctx.vol_avg[k]:
            continue
        i = k + 1                                   # the candle that must not extend the move
        crash = ctx.c[k] < ctx.o[k]
        direction = UP if crash else DOWN
        if crash and ctx.l[i] < ctx.l[k]:
            continue
        if not crash and ctx.h[i] > ctx.h[k]:
            continue
        if direction in last_signal and i - last_signal[direction] <= e["horizon"]:
            continue
        entry = ctx.c[i]
        stop = ctx.l[k] if crash else ctx.h[k]
        target = ctx.l[k] + e["retrace"] * rng if crash else ctx.h[k] - e["retrace"] * rng
        risk = (entry - stop) if crash else (stop - entry)
        reward = (target - entry) if crash else (entry - target)
        if risk <= 0 or risk / entry < min_risk_rate or reward < e["min_reward_risk"] * risk:
            continue
        last_signal[direction] = i
        sl = slice(i + 1, i + 1 + e["horizon"])
        s = scoring.score(direction, entry, stop, target, ctx.h[sl], ctx.l[sl], ctx.c[sl],
                          e["horizon"])
        if s.result == scoring.PENDING:
            continue
        exit_index = i + s.candles_to_result
        exit_price = entry + s.final_r * risk if crash else entry - s.final_r * risk
        net = s.final_r - _costs_r(entry, risk, s.candles_to_result, fees)
        reason = {scoring.CORRECT: "target", scoring.WRONG: "stop", scoring.UNSURE: "horizon"}[s.result]
        out.append(Trade("overshoot", direction, i, int(ctx.t[i]), entry, stop, target, exit_index,
                         exit_price, reason, s.result, s.final_r, net, s.candles_to_result))
    return out


# ---------------------------------------------------------------- evaluation

def summarize(trades):
    t = scoring.tally(tr.result for tr in trades)
    nets = [tr.net_r for tr in trades]
    t["net_total"] = float(sum(nets))
    t["net_avg"] = float(np.mean(nets)) if nets else 0.0
    return t


def verdict(by_year, train_years, train, check):
    """Approved pass rule: positive after fees on train, in >= 4 of the 5 training years,
    and on the 2024-25 check."""
    positive_years = sum(1 for y in train_years if by_year.get(y) and by_year[y]["net_total"] > 0)
    ok = train["net_total"] > 0 and positive_years >= 4 and check["net_total"] > 0
    why = (f"train {train['net_total']:+.1f} R, positive in {positive_years}/{len(train_years)} "
           f"training years, check {check['net_total']:+.1f} R")
    return ok, why
