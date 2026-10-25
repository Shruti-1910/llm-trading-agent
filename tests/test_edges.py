import numpy as np

from trader import config, edges, patterns, scoring
from trader.data import Candle
from trader.setups import DOWN, UP

CFG = config.load()
FEES = CFG["fees"]


def ctx_from(closes, wick=10.0, vols=None):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        v = 100.0 if vols is None else vols[i]
        out.append(Candle(i * 3600, prev, max(prev, c) + wick, min(prev, c) - wick, c, v))
        prev = c
    return patterns.Context(out, CFG["patterns"])


def test_daily_candles_and_closed_index():
    ctx = ctx_from(list(np.linspace(100, 147, 48)))
    d, index = edges.daily(ctx.t, ctx.o, ctx.h, ctx.l, ctx.c, ctx.v)
    assert len(d["c"]) == 2 and d["c"][0] == ctx.c[23] and d["o"][0] == ctx.o[0]
    assert d["h"][0] == ctx.h[:24].max() and d["v"][0] == 2400
    assert index[22] == -1 and index[23] == 0 and index[47] == 1


def test_costs_include_fees_and_funding():
    # entry 100, risk 2: fees 0.118% -> 0.059 R; funding 0.01%/8h for 16h -> 0.01 R
    r = edges._costs_r(100.0, 2.0, 16, FEES)
    assert abs(r - (0.00118 + 0.0002) * 50) < 1e-9


def test_trend_trade_trailing_stop_never_moves_away():
    # 60 flat days, breakout, run up, then a slow fall: exit must be by the trailing stop
    flat = [100 + (i % 2) for i in range(60 * 24)]
    up = list(np.linspace(102, 160, 15 * 24))
    down = list(np.linspace(160, 120, 10 * 24))
    ctx = ctx_from(flat + up + down, wick=0.5)
    trades = edges.trend_trades(ctx, CFG["edges"]["trend"], FEES)
    assert trades and trades[0].direction == UP
    t = trades[0]
    assert t.exit_reason == "trail" and t.exit_price > t.entry and t.result == scoring.CORRECT
    assert t.stop < t.entry


def test_trend_waits_for_daily_close():
    flat = [100 + (i % 2) for i in range(60 * 24)]
    ctx = ctx_from(flat + list(np.linspace(102, 130, 48)), wick=0.5)
    d, index = edges.daily(ctx.t, ctx.o, ctx.h, ctx.l, ctx.c, ctx.v)
    trades = edges.trend_trades(ctx, CFG["edges"]["trend"], FEES)
    assert trades and trades[0].entry_index in set(d["close_hour"].tolist())


def test_overshoot_buy_after_crash_that_does_not_extend():
    closes = [50000 + (i % 3) * 20 for i in range(200)]
    vols = [100.0] * 200
    ctx = ctx_from(closes, vols=vols)
    candles = [Candle(int(t), o, h, l, c, v) for t, o, h, l, c, v in
               zip(ctx.t, ctx.o, ctx.h, ctx.l, ctx.c, ctx.v)]
    k = 200
    candles.append(Candle(k * 3600, 50000, 50010, 49000, 49100, 500.0))      # crash, 5x volume
    candles.append(Candle((k + 1) * 3600, 49100, 49200, 49050, 49150, 150.0))  # no new low
    for j in range(30):
        candles.append(Candle((k + 2 + j) * 3600, 49150, 49600, 49100, 49550, 100.0))
    ctx = patterns.Context(candles, CFG["patterns"])
    trades = edges.overshoot_trades(ctx, CFG["edges"]["overshoot"], FEES, 0.0)
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == UP and t.entry == 49150 and t.stop == 49000
    assert abs(t.target - 49505) < 1e-6 and t.result == scoring.CORRECT


def test_verdict_rule():
    y = lambda net: {"net_total": net}
    by_year = {2019: y(1), 2020: y(2), 2021: y(-1), 2022: y(3), 2023: y(1)}
    ok, _ = edges.verdict(by_year, [2019, 2020, 2021, 2022, 2023], y(6), y(0.5))
    assert ok
    by_year[2023] = y(-0.1)
    ok, _ = edges.verdict(by_year, [2019, 2020, 2021, 2022, 2023], y(6), y(0.5))
    assert not ok
