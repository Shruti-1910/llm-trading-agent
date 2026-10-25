import numpy as np

from trader import config, patterns
from trader.data import Candle

CFG = config.load()


def candles_from_path(points, legs=5, wick=0.2):
    """Piecewise-linear close path through `points`, `legs` candles per segment."""
    closes = [points[0]]
    for a, b in zip(points, points[1:]):
        closes += list(np.linspace(a, b, legs + 1)[1:])
    out, prev = [], closes[0]
    for n, c in enumerate(closes):
        out.append(Candle(n * 300, prev, max(prev, c) + wick, min(prev, c) - wick, c, 100.0))
        prev = c
    return out


def random_walk(n, seed):
    rng = np.random.default_rng(seed)
    closes = 50000 + np.cumsum(rng.normal(0, 60, n))
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        w = abs(rng.normal(0, 30))
        out.append(Candle(i * 300, prev, max(prev, c) + w, min(prev, c) - w, c, rng.uniform(50, 150)))
        prev = c
    return out


def scanner(candles):
    return patterns.Scanner(candles, CFG["patterns"], CFG["prediction"], CFG["fees"])


def test_no_lookahead():
    """Signals up to candle M are identical whether or not later candles exist."""
    full = random_walk(4000, seed=7)
    m = 2500
    a = [(s.index, s.patterns, s.entry, s.stop) for s in scanner(full).scan(end=m)]
    b = [(s.index, s.patterns, s.entry, s.stop) for s in scanner(full[:m]).scan()]
    assert a == b and len(a) > 0


def test_double_top_detected():
    # prior uptrend (higher highs, higher lows), two equal tops, neckline, break down
    path = [90, 95, 92, 98, 95, 101, 97, 110, 105, 110, 101, 99]
    ctx = scanner(candles_from_path(path)).ctx
    found = [s for b in range(ctx.warmup, len(ctx.c)) for s in patterns.detect_doubles(ctx, b)]
    assert [s.pattern for s in found] == ["double_top"]
    s = found[0]
    assert s.direction == patterns.DOWN
    assert abs(s.level - 105) < 0.5 and abs(s.stop - 110.2) < 0.01


def test_double_bottom_detected():
    path = [110, 105, 108, 102, 105, 99, 103, 90, 95, 90, 99, 101]
    ctx = scanner(candles_from_path(path)).ctx
    found = [s for b in range(ctx.warmup, len(ctx.c)) for s in patterns.detect_doubles(ctx, b)]
    assert [s.pattern for s in found] == ["double_bottom"]


def test_signal_has_stop_and_target_on_correct_sides():
    for s in scanner(random_walk(6000, seed=3)).scan():
        if s.direction == patterns.UP:
            assert s.stop < s.entry < s.target
        else:
            assert s.target < s.entry < s.stop
        assert abs(abs(s.target - s.entry) - CFG["prediction"]["reward_risk"] * s.risk) < 1e-6


def test_fee_filter_rejects_tight_stops():
    sc = scanner(random_walk(3000, seed=5))
    min_rate = sc.min_risk_rate
    assert all(s.risk / s.entry >= min_rate for s in sc.scan())


def test_only_filter_ignores_other_patterns():
    candles = random_walk(6000, seed=3)
    full = scanner(candles).scan()
    only = patterns.Scanner(candles, CFG["patterns"], CFG["prediction"], CFG["fees"],
                            only={"ascending_staircase"}).scan()
    assert only and all(s.patterns == ["ascending_staircase"] for s in only)
    assert len(only) >= sum(s.patterns == ["ascending_staircase"] for s in full)
