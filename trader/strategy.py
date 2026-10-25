"""Levels strategy: support/resistance, trend, trendlines, liquidity, volume, candle strength.

Approved by the user 2026-10-25 (rules in docs/levels-strategy.md). Two setups:
  A. Liquidity sweep reversal: wick through a support zone / equal lows, close back above,
     rejection candle, volume >= 1.5x. Only with the trend or in a range. (Mirror for sells.)
  B. Trend pullback: in a trend, price dips into a support zone or onto the trendline,
     then a strong candle closes with volume >= 1.2x. (Mirror for sells.)
Both predict at the close of the signal candle itself (no extra confirmation candle).
The *_4h versions take zones, liquidity and trend from 4-hour candles (built from the entry
candles, used only once closed); entries and stops stay on the entry timeframe.
Everything uses only candles up to the signal candle and swings already confirmed.
"""
import numpy as np

from trader import indicators
from trader.setups import DOWN, UP, Setup

EDGE_REASONS = {
    "sweep_buy": "Stops under obvious lows got triggered and price snapped back: sellers are "
                 "exhausted and bigger buyers absorbed the selling",
    "sweep_sell": "Stops above obvious highs got triggered and price snapped back: buyers are "
                  "exhausted and bigger sellers absorbed the buying",
    "pullback_buy": "Uptrend: trend followers buy the dip at support; a strong candle shows "
                    "buyers are back in control",
    "pullback_sell": "Downtrend: trend followers sell the rally at resistance; a strong candle "
                     "shows sellers are back in control",
}
for _n in list(EDGE_REASONS):
    EDGE_REASONS[_n + "_4h"] = EDGE_REASONS[_n] + " (4-hour levels and trend)"
SETUPS = [n for n in EDGE_REASONS if not n.endswith("_4h")]
SETUPS_4H = [n for n in EDGE_REASONS if n.endswith("_4h")]


# ---------------------------------------------------------------- building blocks

def _extras(ctx):
    """Per-candle arrays the strategy needs, computed once and kept on the context."""
    if not hasattr(ctx, "levels_extra"):
        cfg = ctx.cfg["levels"]
        ctx.levels_extra = {"ema": indicators.ema(ctx.c, cfg["trend_ema"])}
    return ctx.levels_extra


def trend(ctx, b):
    """UP / DOWN / None (range): swing structure and price vs the long average must agree."""
    ema = _extras(ctx)["ema"][b]
    hs, ls = ctx.highs.last(b, 2), ctx.lows.last(b, 2)
    if len(hs) < 2 or len(ls) < 2:
        return None
    if hs[1][1] > hs[0][1] and ls[1][1] > ls[0][1] and ctx.c[b] > ema:
        return UP
    if hs[1][1] < hs[0][1] and ls[1][1] < ls[0][1] and ctx.c[b] < ema:
        return DOWN
    return None


def _clusters(points, tol, min_count):
    """Group prices that sit within `tol` of the group's lowest price."""
    out, group = [], []
    for idx, price in sorted(points, key=lambda p: p[1]):
        if group and price - group[0][1] > tol:
            if len(group) >= min_count:
                out.append(group)
            group = []
        group.append((idx, price))
    if len(group) >= min_count:
        out.append(group)
    return out


def zones(ctx, b):
    """Support/resistance zones: >= min_touches swing points (highs or lows) from the last
    `zone_lookback` candles within zone_tol_atr x ATR. Returns [(low, high, touches, points)]."""
    cfg = ctx.cfg["levels"]
    since = max(0, b - cfg["zone_lookback"])
    pts = ctx.highs.confirmed(b, since=since) + ctx.lows.confirmed(b, since=since)
    groups = _clusters(pts, cfg["zone_tol_atr"] * ctx.atr[b], cfg["zone_min_touches"])
    return [(g[0][1], g[-1][1], len(g), g) for g in groups]


def equal_levels(ctx, b, kind):
    """Liquidity: 2+ swing lows (kind='low') or highs within equal_tol_atr x ATR.
    Returns [(level, points)] where level is the outermost price (where stops sit beyond)."""
    cfg = ctx.cfg["levels"]
    since = max(0, b - cfg["zone_lookback"])
    sw = ctx.lows if kind == "low" else ctx.highs
    groups = _clusters(sw.confirmed(b, since=since), cfg["equal_tol_atr"] * ctx.atr[b], 2)
    return [((g[0][1] if kind == "low" else g[-1][1]), g) for g in groups]


def trendline(ctx, b, direction):
    """Line through the last 2 rising swing lows (UP) or falling swing highs (DOWN).
    Returns (line_fn, points) or None."""
    sw = ctx.lows if direction == UP else ctx.highs
    two = sw.last(b, 2)
    if len(two) < 2:
        return None
    (i1, p1), (i2, p2) = two
    if (p2 <= p1) if direction == UP else (p2 >= p1):
        return None
    slope = (p2 - p1) / (i2 - i1)
    return (lambda x: p1 + slope * (x - i1)), two


def candle_range(ctx, b):
    return ctx.h[b] - ctx.l[b]


def strong_candle(ctx, b, direction):
    """Body >= 60% of range, close in the top (bottom) 25%, range >= 1 x ATR."""
    cfg = ctx.cfg["levels"]
    rng = candle_range(ctx, b)
    if rng <= 0 or rng < cfg["strong_min_atr"] * ctx.atr[b - 1]:
        return False
    body = ctx.c[b] - ctx.o[b] if direction == UP else ctx.o[b] - ctx.c[b]
    close_pos = (ctx.c[b] - ctx.l[b]) / rng if direction == UP else (ctx.h[b] - ctx.c[b]) / rng
    return body >= cfg["strong_body_frac"] * rng and close_pos >= 1 - cfg["strong_close_zone"]


def rejection_candle(ctx, b, direction):
    """Wick pointing into the level is >= 50% of the candle (lower wick for a buy)."""
    rng = candle_range(ctx, b)
    if rng <= 0:
        return False
    wick = (min(ctx.o[b], ctx.c[b]) - ctx.l[b]) if direction == UP \
        else (ctx.h[b] - max(ctx.o[b], ctx.c[b]))
    return wick >= ctx.cfg["levels"]["rejection_wick_frac"] * rng


def volume_ratio(ctx, b):
    avg = ctx.vol_avg[b]
    return ctx.v[b] / avg if avg and avg > 0 else 0.0


def next_opposing(zs, direction, entry):
    """Nearest edge of a zone beyond the entry in the trade direction (None if there is none)."""
    if direction == UP:
        edges = [low for low, _, _, _ in zs if low > entry]
        return min(edges) if edges else None
    edges = [high for _, high, _, _ in zs if high < entry]
    return max(edges) if edges else None


def room_ok(zs, direction, entry, stop, reward_risk):
    """Skip if the next opposing zone is closer than the target."""
    nxt = next_opposing(zs, direction, entry)
    return nxt is None or abs(nxt - entry) >= reward_risk * abs(entry - stop)


# ---------------------------------------------------------------- views
# A "view" is where zones, liquidity and trend come from: the entry timeframe itself
# (levels group) or the 4-hour chart (levels4h group). Entries and stops are always
# on the entry timeframe.

class View:
    def __init__(self, zones, eq_lows, eq_highs, trend, atr, trendline, suffix):
        self.zones, self.eq_lows, self.eq_highs = zones, eq_lows, eq_highs
        self.trend, self.atr, self.trendline, self.suffix = trend, atr, trendline, suffix


def local_view(ctx, b):
    return View(zones(ctx, b), equal_levels(ctx, b, "low"), equal_levels(ctx, b, "high"),
                trend(ctx, b), ctx.atr[b], True, "")


def _htf(ctx):
    """Higher-timeframe context built from the entry candles, plus for each entry candle
    the index of the last FULLY CLOSED higher-timeframe candle (-1 if none yet)."""
    if not hasattr(ctx, "htf"):
        from trader.data import RESOLUTION_SECONDS, Candle
        from trader.patterns import Context
        step = int(ctx.t[1] - ctx.t[0])
        big = RESOLUTION_SECONDS[ctx.cfg["levels"]["htf_resolution"]]
        per = big // step
        blocks, order = {}, []
        for n, t in enumerate(ctx.t):
            start = int(t) // big * big
            if start not in blocks:
                blocks[start] = []
                order.append(start)
            blocks[start].append(n)
        candles, last_n = [], []
        for start in order:
            ns = blocks[start]
            if len(ns) != per:          # incomplete block (data edge or gap): skip it
                continue
            candles.append(Candle(start, ctx.o[ns[0]], ctx.h[ns].max(), ctx.l[ns].min(),
                                  ctx.c[ns[-1]], ctx.v[ns].sum()))
            last_n.append(ns[-1])       # the big candle is known once this candle closes
        index = np.full(len(ctx.t), -1)
        for j, n in enumerate(last_n):
            index[n:] = j
        ctx.htf = Context(candles, ctx.cfg) if candles else None
        ctx.htf_index = index
    return ctx.htf, ctx.htf_index


def htf_view(ctx, b):
    big, index = _htf(ctx)
    if big is None:
        return None
    j = int(index[b])
    if j < big.warmup or np.isnan(big.atr[j]):
        return None
    return View(zones(big, j), equal_levels(big, j, "low"), equal_levels(big, j, "high"),
                trend(big, j), big.atr[j], False, "_4h")


# ---------------------------------------------------------------- setups

def _setup(ctx, name, direction, b, level, stop, points, zone_target):
    start = min([p[0] for pts in points.values() for p in pts] + [b])
    if points and name.endswith("_4h"):
        start = b                       # 4h point indices are on another chart
        points = {}
    return Setup(name, direction, b, level, stop, 0.0, (name, b), start, points, zone_target)


def _sweep(ctx, b, reward_risk, view):
    """Setup A at candle b (instant: the prediction is made at b's close)."""
    cfg = ctx.cfg["levels"]
    out = []
    if view is None or volume_ratio(ctx, b) < cfg["sweep_volume_mult"]:
        return out
    buf = cfg["stop_buffer_atr"] * ctx.atr[b]
    for direction in (UP, DOWN):
        if view.trend is not None and view.trend != direction:   # never against a clear trend
            continue
        if not rejection_candle(ctx, b, direction):
            continue
        if direction == UP:
            levels = [(z[0], z[3]) for z in view.zones] + view.eq_lows
            hit = [(lv, pts) for lv, pts in levels
                   if ctx.l[b] < lv < ctx.c[b] and ctx.c[b - 1] > lv]
            stop = ctx.l[b] - buf
        else:
            levels = [(z[1], z[3]) for z in view.zones] + view.eq_highs
            hit = [(lv, pts) for lv, pts in levels
                   if ctx.c[b] < lv < ctx.h[b] and ctx.c[b - 1] < lv]
            stop = ctx.h[b] + buf
        if not hit:
            continue
        level, pts = max(hit, key=lambda h: len(h[1]))   # strongest level swept
        if not room_ok(view.zones, direction, ctx.c[b], stop, reward_risk):
            continue
        name = ("sweep_buy" if direction == UP else "sweep_sell") + view.suffix
        out.append(_setup(ctx, name, direction, b, level, stop,
                          {"lows" if direction == UP else "highs": pts},
                          next_opposing(view.zones, direction, ctx.c[b])))
    return out


def _pullback(ctx, b, reward_risk, view):
    """Setup B at candle b (instant)."""
    cfg = ctx.cfg["levels"]
    if view is None:
        return []
    direction = view.trend
    if direction is None or volume_ratio(ctx, b) < cfg["pullback_volume_mult"]:
        return []
    if not strong_candle(ctx, b, direction):
        return []
    w0 = max(0, b - cfg["pullback_window"] + 1)
    tol = cfg["zone_tol_atr"] * view.atr
    up = direction == UP
    extreme = ctx.l[w0:b + 1].min() if up else ctx.h[w0:b + 1].max()
    touched, points = None, None
    # Support (resistance) zone: the pullback entered it but didn't break far through it,
    # and the strong candle closed back beyond it.
    for low, high, _, pts in view.zones:
        if up and extreme <= high and extreme >= low - tol and ctx.c[b] > high:
            touched, points = high, {"lows": pts}
        if not up and extreme >= low and extreme <= high + tol and ctx.c[b] < low:
            touched, points = low, {"highs": pts}
    # Trendline (entry timeframe only): the pullback came within tol of the line and the
    # close held beyond it.
    if touched is None and view.trendline:
        tl = trendline(ctx, b, direction)
        if tl:
            line, pts = tl
            j = int(np.argmin(ctx.l[w0:b + 1]) if up else np.argmax(ctx.h[w0:b + 1])) + w0
            if j > pts[1][0] and abs(extreme - line(j)) <= tol and \
                    (ctx.c[b] > line(b) if up else ctx.c[b] < line(b)):
                touched, points = line(b), {"lows" if up else "highs": pts}
    if touched is None:
        return []
    buf = cfg["stop_buffer_atr"] * ctx.atr[b]
    stop = extreme - buf if up else extreme + buf
    if not room_ok(view.zones, direction, ctx.c[b], stop, reward_risk):
        return []
    name = ("pullback_buy" if up else "pullback_sell") + view.suffix
    return [_setup(ctx, name, direction, b, touched, stop, points,
                   next_opposing(view.zones, direction, ctx.c[b]))]


def detect_sweep(ctx, b, reward_risk):
    return _sweep(ctx, b, reward_risk, local_view(ctx, b))


def detect_pullback(ctx, b, reward_risk):
    return _pullback(ctx, b, reward_risk, local_view(ctx, b))


def detect_sweep_4h(ctx, b, reward_risk):
    return _sweep(ctx, b, reward_risk, htf_view(ctx, b))


def detect_pullback_4h(ctx, b, reward_risk):
    return _pullback(ctx, b, reward_risk, htf_view(ctx, b))


INSTANT_DETECTORS = {
    detect_sweep: {"sweep_buy", "sweep_sell"},
    detect_pullback: {"pullback_buy", "pullback_sell"},
    detect_sweep_4h: {"sweep_buy_4h", "sweep_sell_4h"},
    detect_pullback_4h: {"pullback_buy_4h", "pullback_sell_4h"},
}
