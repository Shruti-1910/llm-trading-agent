from types import SimpleNamespace

import numpy as np

from trader import config, patterns, strategy
from trader.data import Candle
from trader.setups import DOWN, UP

CFG = config.load()


def fake_ctx(o, h, l, c, atr=10.0):
    n = len(c)
    return SimpleNamespace(o=np.array(o, float), h=np.array(h, float), l=np.array(l, float),
                           c=np.array(c, float), atr=np.full(n, atr), cfg=CFG["patterns"])


def test_strong_candle():
    # range 12 >= 1 ATR, body 10 (83%), closes at the high
    ctx = fake_ctx([0, 100], [0, 111], [0, 99], [0, 110])
    assert strategy.strong_candle(ctx, 1, UP)
    assert not strategy.strong_candle(ctx, 1, DOWN)
    # same body but range below 1 ATR
    ctx = fake_ctx([0, 100], [0, 106], [0, 99], [0, 105], atr=10)
    assert not strategy.strong_candle(ctx, 1, UP)


def test_rejection_candle():
    # long lower wick: low 90, body 99-100, high 101 -> wick 9 of range 11
    ctx = fake_ctx([0, 99], [0, 101], [0, 90], [0, 100])
    assert strategy.rejection_candle(ctx, 1, UP)
    assert not strategy.rejection_candle(ctx, 1, DOWN)


def test_clusters_group_close_prices():
    pts = [(1, 100.0), (5, 100.4), (9, 103.0), (12, 103.2), (20, 110.0)]
    groups = strategy._clusters(pts, tol=0.5, min_count=2)
    assert [[p[0] for p in g] for g in groups] == [[1, 5], [9, 12]]


def range_market(n=260, seed=1):
    """Sideways market bouncing between ~49,800 and ~50,200, ending with a sweep candle."""
    rng = np.random.default_rng(seed)
    closes = 50000 + 200 * np.sin(np.arange(n) / 6) + rng.normal(0, 10, n)
    out, prev = [], closes[0]
    for i, cl in enumerate(closes):
        out.append(Candle(i * 3600, prev, max(prev, cl) + 15, min(prev, cl) - 15, cl, 100.0))
        prev = cl
    return out


def sweep_setup():
    candles = range_market()
    ctx = patterns.Context(candles, CFG["patterns"])
    b = len(candles)
    support = min(z[0] for z in strategy.zones(ctx, b - 1))
    candles.append(Candle(b * 3600, support + 20, support + 45, support - 60, support + 40, 300.0))
    return patterns.Context(candles, CFG["patterns"]), b


def test_sweep_buy_blocked_against_downtrend(monkeypatch):
    ctx, b = sweep_setup()
    monkeypatch.setattr(strategy, "trend", lambda ctx, b: DOWN)
    assert strategy.detect_sweep(ctx, b, 1.5) == []
    monkeypatch.setattr(strategy, "trend", lambda ctx, b: UP)
    assert [s.pattern for s in strategy.detect_sweep(ctx, b, 1.5)] == ["sweep_buy"]


def test_sweep_buy_detected_on_wick_below_support(monkeypatch):
    monkeypatch.setattr(strategy, "trend", lambda ctx, b: None)   # range market
    candles = range_market()
    ctx = patterns.Context(candles, CFG["patterns"])
    b = len(candles)
    support = min(z[0] for z in strategy.zones(ctx, b - 1))
    prev = candles[-1]
    # wick 60 below the support zone, close back above it near the high, triple volume
    sweep = Candle(b * 3600, support + 20, support + 45, support - 60, support + 40, 300.0)
    candles.append(sweep)
    ctx = patterns.Context(candles, CFG["patterns"])
    assert prev.close > support
    found = strategy.detect_sweep(ctx, b, CFG["prediction"]["reward_risk"])
    assert [s.pattern for s in found] == ["sweep_buy"]
    assert abs(found[0].stop - (sweep.low - 0.1 * ctx.atr[b])) < 1e-6


def test_no_sweep_without_volume(monkeypatch):
    monkeypatch.setattr(strategy, "trend", lambda ctx, b: None)
    candles = range_market()
    ctx = patterns.Context(candles, CFG["patterns"])
    b = len(candles)
    support = min(z[0] for z in strategy.zones(ctx, b - 1))
    candles.append(Candle(b * 3600, support + 20, support + 45, support - 60, support + 40, 100.0))
    ctx = patterns.Context(candles, CFG["patterns"])
    assert strategy.detect_sweep(ctx, b, 1.5) == []


def test_levels_no_lookahead():
    from tests.test_patterns import random_walk
    full = random_walk(4000, seed=11)
    m = 2500
    only = patterns.GROUPS["levels"]
    mk = lambda cs: patterns.Scanner(cs, CFG["patterns"], CFG["prediction"], CFG["fees"], only=only)
    a = [(s.index, s.patterns, s.entry, s.stop) for s in mk(full).scan(end=m)]
    b = [(s.index, s.patterns, s.entry, s.stop) for s in mk(full[:m]).scan()]
    assert a == b and len(a) > 0


def hourly_walk(n, seed):
    rng = np.random.default_rng(seed)
    closes = 50000 + np.cumsum(rng.normal(0, 150, n))
    out, prev = [], closes[0]
    for i, cl in enumerate(closes):
        w = abs(rng.normal(0, 60))
        out.append(Candle(i * 3600, prev, max(prev, cl) + w, min(prev, cl) - w, cl,
                          rng.uniform(50, 200)))
        prev = cl
    return out


def test_4h_candles_built_from_closed_hours_only():
    candles = hourly_walk(50, seed=2)
    ctx = patterns.Context(candles, CFG["patterns"])
    big, index = strategy._htf(ctx)
    # first 4h candle = hours 0..3; usable from hour 3 (its last hour) onwards
    assert list(index[:9]) == [-1, -1, -1, 0, 0, 0, 0, 1, 1]
    assert big.h[0] == max(c.high for c in candles[:4])
    assert big.c[0] == candles[3].close and big.o[0] == candles[0].open
    assert big.v[0] == sum(c.volume for c in candles[:4])


def test_levels4h_no_lookahead():
    full = hourly_walk(6000, seed=4)
    m = 4500
    only = patterns.GROUPS["levels4h"]
    mk = lambda cs: patterns.Scanner(cs, CFG["patterns"], CFG["prediction"], CFG["fees"], only=only)
    a = [(s.index, s.patterns, s.entry, s.stop) for s in mk(full).scan(end=m)]
    b = [(s.index, s.patterns, s.entry, s.stop) for s in mk(full[:m]).scan()]
    assert a == b and len(a) > 0


def test_zone_target_capped_and_fallback():
    pred = dict(CFG["prediction"], target_mode="zone", max_reward_risk=3.0)
    sc = patterns.Scanner(hourly_walk(300, seed=1), CFG["patterns"], pred, CFG["fees"])
    entry = sc.ctx.c[100]
    stop = entry - 500
    mk = lambda zt: strategy.Setup("sweep_buy", UP, 100, entry, stop, 0.0, ("x", zt), 90, {}, zt)
    assert abs(sc.make_signal(mk(entry + 1000), 100).target - (entry + 1000)) < 1e-6  # 2R zone
    assert abs(sc.make_signal(mk(entry + 9000), 100).target - (entry + 1500)) < 1e-6  # capped 3R
    assert abs(sc.make_signal(mk(None), 100).target - (entry + 1500)) < 1e-6          # no zone
