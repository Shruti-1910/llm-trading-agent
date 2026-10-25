"""Module 2: pattern detection (rules in docs/pattern-rules.md).

Each detector looks at candle `b` and reports a Setup when a pattern breaks out on
that candle. The Scanner then waits one confirmation candle (i = b + 1) and turns
the setup into a Signal: direction, entry, stop, target. Detectors only use data
up to candle b, and swings only once they are confirmed, so there is no lookahead.
"""
from dataclasses import dataclass

import numpy as np

from trader import indicators, strategy
from trader.setups import DOWN, UP, Setup  # noqa: F401  (re-exported)

EDGE_REASONS = {
    "ascending_staircase": "Trend followers buy pullbacks; momentum persists",
    "descending_staircase": "Trend followers sell rallies; momentum persists",
    "ascending_triangle": "Sellers defend a fixed level while buyers step in higher; stops cluster above resistance",
    "descending_triangle": "Buyers at a fixed level get exhausted; stops cluster below support",
    "symmetrical_triangle": "Volatility compression is followed by expansion",
    "bull_flag": "Short pause after a strong impulse, then the trend resumes",
    "bear_flag": "Short pause after a strong impulse, then the trend resumes",
    "rising_wedge": "Momentum fading inside the up move (each push smaller)",
    "falling_wedge": "Momentum fading inside the down move (each push smaller)",
    "double_top": "Buyers fail twice at the same level; sellers take control",
    "double_bottom": "Sellers fail twice at the same level; buyers take control",
    "head_and_shoulders": "Each rally weaker; failed new high traps late buyers",
    "rounded_top": "Gradual shift from buyers to sellers",
    "rounded_bottom": "Gradual shift from sellers to buyers",
    "cup_and_handle": "Second shakeout before the uptrend; sellers exhausted",
}
CHART_PATTERNS = list(EDGE_REASONS)
EDGE_REASONS.update(strategy.EDGE_REASONS)
PATTERNS = list(EDGE_REASONS)   # chart patterns + levels-strategy setups
GROUPS = {"chart": set(CHART_PATTERNS), "levels": set(strategy.SETUPS),
          "levels4h": set(strategy.SETUPS_4H)}


@dataclass
class Signal:
    index: int        # confirmation candle; prediction made at its close
    time: int
    direction: str
    patterns: list
    entry: float
    stop: float
    target: float
    risk: float
    height: float
    start: int
    edge_reasons: list
    setups: list


def line_through(p1, p2):
    (x1, y1), (x2, y2) = p1, p2
    slope = (y2 - y1) / (x2 - x1)
    return lambda x: y1 + slope * (x - x1), slope


def fit_quadratic(y):
    """Returns (a, vertex_fraction, r2) for a quadratic fit over y."""
    x = np.arange(len(y), dtype=float)
    a, b, c = np.polyfit(x, y, 2)
    pred = (a * x + b) * x + c
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ((y - pred) ** 2).sum() / ss_tot if ss_tot > 0 else 0.0
    vertex = -b / (2 * a) / (len(y) - 1) if a != 0 else -1.0
    return a, vertex, r2


class Context:
    """Arrays and helpers shared by all detectors."""

    def __init__(self, candles, cfg):
        self.cfg = cfg
        self.t = np.array([c.time for c in candles])
        self.o = np.array([c.open for c in candles], dtype=float)
        self.h = np.array([c.high for c in candles], dtype=float)
        self.l = np.array([c.low for c in candles], dtype=float)
        self.c = np.array([c.close for c in candles], dtype=float)
        self.v = np.array([c.volume for c in candles], dtype=float)
        self.atr = indicators.atr(self.h, self.l, self.c, cfg["atr_period"])
        self.vol_avg = indicators.rolling_mean_prior(self.v, cfg["volume_avg_period"])
        k = cfg["swing_k"]
        self.highs = indicators.Swings(indicators.find_swings(self.h, k, "high"), self.h, k)
        self.lows = indicators.Swings(indicators.find_swings(self.l, k, "low"), self.l, k)
        self.warmup = max(cfg["atr_period"], cfg["volume_avg_period"]) + 2

    def tol(self, b):
        return self.cfg["equal_tol_atr"] * self.atr[b]

    def brk(self, b):
        return self.cfg["breakout_atr"] * self.atr[b]

    def window_start(self, b):
        return max(0, b - self.cfg["max_pattern_candles"])

    def broke_up(self, b, level_b, level_prev):
        return self.c[b] > level_b + self.brk(b) and self.c[b - 1] <= level_prev + self.brk(b)

    def broke_down(self, b, level_b, level_prev):
        return self.c[b] < level_b - self.brk(b) and self.c[b - 1] >= level_prev - self.brk(b)

    def volume_ok(self, b):
        return self.v[b] >= self.cfg["volume_mult"] * self.vol_avg[b]

    def prior_trend(self, b, before):
        """'up' / 'down' / None from the 2 swing highs and 2 swing lows before index `before`."""
        hs = self.highs.last(b, 2, before=before)
        ls = self.lows.last(b, 2, before=before)
        if len(hs) < 2 or len(ls) < 2:
            return None
        if hs[1][1] > hs[0][1] and ls[1][1] > ls[0][1]:
            return "up"
        if hs[1][1] < hs[0][1] and ls[1][1] < ls[0][1]:
            return "down"
        return None


# ---------------------------------------------------------------- detectors

def detect_staircase(ctx, b):
    out = []
    k = ctx.cfg["swing_k"]
    hs, ls = ctx.highs.last(b, 2), ctx.lows.last(b, 2)
    if len(hs) < 2 or len(ls) < 2:
        return out
    (h1i, h1), (h2i, h2) = hs
    (l1i, l1), (l2i, l2) = ls
    # Ascending: higher highs + higher lows, newest swing is the pullback low.
    if h2 > h1 and l2 > l1 and l2i > h2i:
        trig = lambda j: ctx.c[j] > ctx.h[j - 1]
        first = l2i + k
        if b >= first and trig(b) and ctx.c[b] > l2 and not any(trig(j) for j in range(first, b)):
            out.append(Setup("ascending_staircase", UP, b, ctx.h[b - 1], l2, h2 - l2,
                             ("ascending_staircase", l2i), h1i,
                             {"highs": hs, "lows": ls}))
    # Descending: lower highs + lower lows, newest swing is the rally high.
    if h2 < h1 and l2 < l1 and h2i > l2i:
        trig = lambda j: ctx.c[j] < ctx.l[j - 1]
        first = h2i + k
        if b >= first and trig(b) and ctx.c[b] < h2 and not any(trig(j) for j in range(first, b)):
            out.append(Setup("descending_staircase", DOWN, b, ctx.l[b - 1], h2, h2 - l2,
                             ("descending_staircase", h2i), l1i,
                             {"highs": hs, "lows": ls}))
    return out


def _equal_run(swings, tol):
    """Most recent run of consecutive swings all within tol of the newest one."""
    if not swings:
        return []
    ref = swings[-1][1]
    run = []
    for s in reversed(swings):
        if abs(s[1] - ref) > tol:
            break
        run.insert(0, s)
    return run


def _strictly_moving(swings, tol, rising):
    return all((b[1] - a[1] > tol) if rising else (a[1] - b[1] > tol)
               for a, b in zip(swings, swings[1:]))


def detect_flat_triangles(ctx, b):
    out = []
    ws, tol = ctx.window_start(b), ctx.tol(b)
    # Ascending: flat highs, rising lows, prior uptrend, breakout up with volume.
    run = _equal_run(ctx.highs.confirmed(b, since=ws), tol)
    if len(run) >= 2:
        res = max(p for _, p in run)
        lows = ctx.lows.confirmed(b, since=run[0][0])
        if len(lows) >= 2 and _strictly_moving(lows, tol, rising=True):
            start = min(run[0][0], lows[0][0])
            if (ctx.prior_trend(b, start) == "up" and ctx.broke_up(b, res, res)
                    and ctx.volume_ok(b)):
                out.append(Setup("ascending_triangle", UP, b, res, lows[-1][1], res - lows[0][1],
                                 ("ascending_triangle", run[0][0]), start,
                                 {"highs": run, "lows": lows}))
    # Descending: flat lows, falling highs, prior downtrend, breakout down with volume.
    run = _equal_run(ctx.lows.confirmed(b, since=ws), tol)
    if len(run) >= 2:
        sup = min(p for _, p in run)
        highs = ctx.highs.confirmed(b, since=run[0][0])
        if len(highs) >= 2 and _strictly_moving(highs, tol, rising=False):
            start = min(run[0][0], highs[0][0])
            if (ctx.prior_trend(b, start) == "down" and ctx.broke_down(b, sup, sup)
                    and ctx.volume_ok(b)):
                out.append(Setup("descending_triangle", DOWN, b, sup, highs[-1][1], highs[0][1] - sup,
                                 ("descending_triangle", run[0][0]), start,
                                 {"highs": highs, "lows": run}))
    return out


def _two_lines(ctx, b):
    """Upper line through the last 2 swing highs, lower through the last 2 swing lows."""
    ws = ctx.window_start(b)
    hs, ls = ctx.highs.last(b, 2), ctx.lows.last(b, 2)
    if len(hs) < 2 or len(ls) < 2 or min(hs[0][0], ls[0][0]) < ws:
        return None
    upper, su = line_through(*hs)
    lower, sl = line_through(*ls)
    start = min(hs[0][0], ls[0][0])
    if upper(b) <= lower(b) or upper(start) <= lower(start):
        return None
    return hs, ls, upper, su, lower, sl, start


def detect_symmetrical_triangle(ctx, b):
    lines = _two_lines(ctx, b)
    if not lines:
        return []
    hs, ls, upper, _, lower, _, start = lines
    tol = ctx.tol(b)
    if not (hs[1][1] < hs[0][1] - tol and ls[1][1] > ls[0][1] + tol) or not ctx.volume_ok(b):
        return []
    height = upper(start) - lower(start)
    pts = {"highs": hs, "lows": ls}
    key = ("symmetrical_triangle", start)
    if ctx.broke_up(b, upper(b), upper(b - 1)):
        return [Setup("symmetrical_triangle", UP, b, upper(b), ls[1][1], height, key, start, pts)]
    if ctx.broke_down(b, lower(b), lower(b - 1)):
        return [Setup("symmetrical_triangle", DOWN, b, lower(b), hs[1][1], height, key, start, pts)]
    return []


def detect_wedges(ctx, b):
    lines = _two_lines(ctx, b)
    if not lines:
        return []
    hs, ls, upper, su, lower, sl, start = lines
    tol = ctx.tol(b)
    height = upper(start) - lower(start)
    pts = {"highs": hs, "lows": ls}
    rising = hs[1][1] > hs[0][1] + tol and ls[1][1] > ls[0][1] + tol and sl > su
    falling = hs[1][1] < hs[0][1] - tol and ls[1][1] < ls[0][1] - tol and su < sl
    if rising and ctx.broke_down(b, lower(b), lower(b - 1)):
        return [Setup("rising_wedge", DOWN, b, lower(b), hs[1][1], height,
                      ("rising_wedge", start), start, pts)]
    if falling and ctx.broke_up(b, upper(b), upper(b - 1)):
        return [Setup("falling_wedge", UP, b, upper(b), ls[1][1], height,
                      ("falling_wedge", start), start, pts)]
    return []


def detect_flags(ctx, b):
    cfg = ctx.cfg
    out = []
    lo_f, hi_f = cfg["flag_min_candles"], cfg["flag_max_candles"]
    s = b - 1 - hi_f
    if s - cfg["flag_pole_max_candles"] < 0:
        return out
    for bull in (True, False):
        seg = ctx.h[s:b] if bull else ctx.l[s:b]
        p1 = s + int(np.argmax(seg) if bull else np.argmin(seg))
        flag_len = (b - 1) - p1
        if not lo_f <= flag_len <= hi_f:
            continue
        ps = p1 - cfg["flag_pole_max_candles"]
        if bull:
            p0 = ps + int(np.argmin(ctx.l[ps:p1 + 1]))
            pole = ctx.h[p1] - ctx.l[p0]
        else:
            p0 = ps + int(np.argmax(ctx.h[ps:p1 + 1]))
            pole = ctx.h[p0] - ctx.l[p1]
        if pole < cfg["flag_pole_atr"] * ctx.atr[p1]:
            continue
        x = np.arange(p1 + 1, b, dtype=float)
        sh, ih = np.polyfit(x, ctx.h[p1 + 1:b], 1)
        sl, il = np.polyfit(x, ctx.l[p1 + 1:b], 1)
        against = (sh < 0 and sl < 0) if bull else (sh > 0 and sl > 0)
        if not against or abs(sh - sl) > cfg["flag_slope_tol"] * max(abs(sh), abs(sl)):
            continue
        flag_low, flag_high = ctx.l[p1 + 1:b].min(), ctx.h[p1 + 1:b].max()
        retrace = (ctx.h[p1] - flag_low) if bull else (flag_high - ctx.l[p1])
        if retrace > cfg["flag_max_retrace"] * pole:
            continue
        upper = lambda j: ih + sh * j
        lower = lambda j: il + sl * j
        width = upper(p1 + 1) - lower(p1 + 1)
        pts = {"pole": [(p0, ctx.l[p0] if bull else ctx.h[p0]), (p1, ctx.h[p1] if bull else ctx.l[p1])],
               "upper": [(p1 + 1, upper(p1 + 1)), (b, upper(b))],
               "lower": [(p1 + 1, lower(p1 + 1)), (b, lower(b))]}
        if bull and ctx.broke_up(b, upper(b), upper(b - 1)):
            out.append(Setup("bull_flag", UP, b, upper(b), flag_low, width, ("bull_flag", p1), p0, pts))
        if not bull and ctx.broke_down(b, lower(b), lower(b - 1)):
            out.append(Setup("bear_flag", DOWN, b, lower(b), flag_high, width, ("bear_flag", p1), p0, pts))
    return out


def detect_doubles(ctx, b):
    out = []
    cfg = ctx.cfg
    ws, tol = ctx.window_start(b), ctx.tol(b)
    neck_min = cfg["double_neck_atr"] * ctx.atr[b]
    for top in (True, False):
        swings = (ctx.highs if top else ctx.lows).confirmed(b, since=ws)
        opposite = ctx.lows if top else ctx.highs
        # The second peak may be followed by smaller bounces, so try the last few.
        for j in range(len(swings) - 1, max(-1, len(swings) - 4), -1):
            t2i, t2 = swings[j]
            later = swings[j + 1:]
            if any((p >= t2 - tol) if top else (p <= t2 + tol) for _, p in later):
                break
            found = None
            for i in range(j - 1, -1, -1):
                t1i, t1 = swings[i]
                between = swings[i + 1:j]
                if any((p > min(t1, t2)) if top else (p < max(t1, t2)) for _, p in between):
                    break
                if t2i - t1i < cfg["double_min_gap"] or abs(t2 - t1) > tol:
                    continue
                if top and t2 > t1 + tol:
                    continue
                if not top and t2 < t1 - tol:
                    continue
                mids = [p for idx, p in opposite.confirmed(b, since=t1i) if idx < t2i]
                if not mids:
                    continue
                neck = min(mids) if top else max(mids)
                extreme = max(t1, t2) if top else min(t1, t2)
                if abs(extreme - neck) < neck_min:
                    continue
                if ctx.prior_trend(b, t1i) != ("up" if top else "down"):
                    continue
                found = (t1i, t1, neck, extreme)
                break
            if not found:
                continue
            t1i, t1, neck, extreme = found
            # no new extreme beyond the tops since the second top
            seg = ctx.h[t2i + 1:b + 1] if top else ctx.l[t2i + 1:b + 1]
            if len(seg) and ((seg.max() > extreme + tol) if top else (seg.min() < extreme - tol)):
                break
            pts = {"peaks": [(t1i, t1), (t2i, t2)], "neck": neck}
            if top and ctx.broke_down(b, neck, neck):
                out.append(Setup("double_top", DOWN, b, neck, extreme, extreme - neck,
                                 ("double_top", t1i, t2i), t1i, pts))
            if not top and ctx.broke_up(b, neck, neck):
                out.append(Setup("double_bottom", UP, b, neck, extreme, neck - extreme,
                                 ("double_bottom", t1i, t2i), t1i, pts))
            break
    return out


def detect_head_and_shoulders(ctx, b):
    cfg = ctx.cfg
    ws, tol = ctx.window_start(b), ctx.tol(b)
    highs = ctx.highs.confirmed(b, since=ws)
    for offset in (0, 1, 2):
        end = len(highs) - offset
        if end < 3:
            break
        ls_, hd, rs = highs[end - 3:end]
        if any(p >= rs[1] for _, p in highs[end:]):
            break
        if not (hd[1] > ls_[1] + tol and hd[1] > rs[1] + tol):
            continue
        if abs(ls_[1] - rs[1]) > cfg["hs_shoulder_tol_atr"] * ctx.atr[b]:
            continue
        lows = ctx.lows.confirmed(b, since=ls_[0])
        t1 = [s for s in lows if ls_[0] < s[0] < hd[0]]
        t2 = [s for s in lows if hd[0] < s[0] < rs[0]]
        if not t1 or not t2:
            continue
        t1, t2 = min(t1, key=lambda s: s[1]), min(t2, key=lambda s: s[1])
        if ctx.prior_trend(b, ls_[0]) != "up":
            continue
        neck, _ = line_through(t1, t2)
        if ctx.broke_down(b, neck(b), neck(b - 1)):
            return [Setup("head_and_shoulders", DOWN, b, neck(b), rs[1], hd[1] - neck(hd[0]),
                          ("head_and_shoulders", ls_[0]), ls_[0],
                          {"peaks": [ls_, hd, rs], "neck": [t1, t2]})]
        return []
    return []


def _rounded_ok(ctx, y, bottom):
    cfg = ctx.cfg
    a, vertex, r2 = fit_quadratic(y)
    lo, hi = cfg["rounded_vertex_range"]
    return (a > 0 if bottom else a < 0) and lo <= vertex <= hi and r2 >= cfg["rounded_min_r2"]


def detect_rounded(ctx, b):
    cfg = ctx.cfg
    depth_min = cfg["rounded_depth_atr"] * ctx.atr[b]
    out = []
    for bottom in (True, False):
        for w in cfg["rounded_windows"]:
            s = b - w
            if s < 0:
                continue
            rim = ctx.c[s]
            crossed = ctx.broke_up(b, rim, rim) if bottom else ctx.broke_down(b, rim, rim)
            if not crossed:
                continue
            y = ctx.c[s:b]
            depth = (rim - y.min()) if bottom else (y.max() - rim)
            if depth < depth_min or not _rounded_ok(ctx, y, bottom):
                continue
            swing = (ctx.lows if bottom else ctx.highs).last(b, 1)
            if not swing:
                continue
            name = "rounded_bottom" if bottom else "rounded_top"
            out.append(Setup(name, UP if bottom else DOWN, b, rim, swing[0][1], depth,
                             (name, b), s, {"rim": rim, "window": (s, b - 1)}))
            break
    return out


def detect_cup_and_handle(ctx, b):
    cfg = ctx.cfg
    atr_b = ctx.atr[b]
    windows = cfg["rounded_windows"]
    max_handle = int(max(windows) * cfg["cup_handle_max_frac"])
    hh = -np.inf
    for hl in range(1, max_handle + 1):
        hh = max(hh, ctx.h[b - hl])
        if hl < cfg["cup_handle_min_candles"]:
            continue
        c1 = b - hl - 1
        if ctx.c[b] <= hh + ctx.brk(b) or ctx.h[c1] < hh:
            continue
        handle_low = ctx.l[b - hl:b].min()
        for w in windows:
            c0 = c1 - w + 1
            if c0 < 0 or hl > cfg["cup_handle_max_frac"] * w:
                continue
            left, right = ctx.c[c0], ctx.c[c1]
            if abs(right - left) > cfg["cup_rim_tol_atr"] * atr_b:
                continue
            y = ctx.c[c0:c1 + 1]
            depth = min(left, right) - y.min()
            pullback = right - handle_low
            if depth < cfg["rounded_depth_atr"] * atr_b:
                continue
            if not 0 < pullback <= cfg["cup_handle_max_retrace"] * depth:
                continue
            if not _rounded_ok(ctx, y, bottom=True):
                continue
            return [Setup("cup_and_handle", UP, b, hh, handle_low, depth,
                          ("cup_and_handle", c1), c0,
                          {"rim": (left, right), "cup": (c0, c1), "handle": (c1 + 1, b - 1)})]
    return []


DETECTORS = [detect_staircase, detect_flat_triangles, detect_symmetrical_triangle,
             detect_flags, detect_wedges, detect_doubles, detect_head_and_shoulders,
             detect_rounded, detect_cup_and_handle]

# Which patterns each detector can report, so a Scanner limited to some patterns
# can skip the other detectors.
DETECTOR_PATTERNS = {
    detect_staircase: {"ascending_staircase", "descending_staircase"},
    detect_flat_triangles: {"ascending_triangle", "descending_triangle"},
    detect_symmetrical_triangle: {"symmetrical_triangle"},
    detect_flags: {"bull_flag", "bear_flag"},
    detect_wedges: {"rising_wedge", "falling_wedge"},
    detect_doubles: {"double_top", "double_bottom"},
    detect_head_and_shoulders: {"head_and_shoulders"},
    detect_rounded: {"rounded_top", "rounded_bottom"},
    detect_cup_and_handle: {"cup_and_handle"},
}


# ---------------------------------------------------------------- scanner

def round_trip_fee_rate(fees):
    return 2 * fees["taker_rate"] * (1 + fees["gst_rate"])


class Scanner:
    """Runs all detectors candle by candle and emits confirmed Signals."""

    def __init__(self, candles, pattern_cfg, prediction_cfg, fees, only=None, ctx=None):
        """only: set of patterns to use (None = all). Others are ignored completely, so they
        can't merge with or block the patterns in use."""
        self.ctx = ctx or Context(candles, pattern_cfg)
        self.only = set(PATTERNS) if only is None else set(only)
        self.detectors = [d for d in DETECTORS if DETECTOR_PATTERNS[d] & self.only]
        self.instant = [d for d, names in strategy.INSTANT_DETECTORS.items() if names & self.only]
        self.pred = prediction_cfg
        self.min_risk_rate = prediction_cfg["min_risk_fee_mult"] * round_trip_fee_rate(fees)
        self.used_keys = set()
        self.last_signal = {}   # (pattern, direction) -> index, for the cooldown
        self.rejected = {}      # reason -> count, for reporting

    def _reject(self, reason):
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def confirm(self, setup, i):
        """Turn a setup from candle i-1 into a candidate Signal at candle i, or None."""
        ctx = self.ctx
        up = setup.direction == UP
        green = ctx.c[i] > ctx.o[i]
        if not ((green and ctx.c[i] > setup.level) if up else (not green and ctx.c[i] < setup.level)):
            self._reject("not confirmed")
            return None
        return self.make_signal(setup, i)

    def make_signal(self, setup, i):
        """Signal entering at candle i's close, after the stop/fee/height checks, or None."""
        ctx = self.ctx
        up = setup.direction == UP
        entry = ctx.c[i]
        risk = (entry - setup.stop) if up else (setup.stop - entry)
        if risk <= 0:
            self._reject("stop on wrong side")
            return None
        if risk / entry < self.min_risk_rate:
            self._reject("stop too close vs fees")
            return None
        reward = self.pred["reward_risk"] * risk
        if self.pred.get("target_mode") == "zone" and setup.pattern not in CHART_PATTERNS:
            # Exit E3: target at the next opposing zone, capped; no zone -> the cap.
            cap = self.pred["max_reward_risk"] * risk
            reward = cap if setup.zone_target is None else min(abs(setup.zone_target - entry), cap)
        if (self.pred["require_height_covers_target"] and setup.pattern in CHART_PATTERNS
                and setup.height < reward):
            self._reject("pattern height < target")
            return None
        target = entry + reward if up else entry - reward
        return Signal(i, int(ctx.t[i]), setup.direction, [setup.pattern], entry, setup.stop,
                      target, risk, setup.height, setup.start,
                      [EDGE_REASONS[setup.pattern]], [setup])

    def step(self, i):
        """Signal confirmed at candle i (or None)."""
        ctx = self.ctx
        b = i - 1
        if b < ctx.warmup or np.isnan(ctx.atr[b]):
            return None
        # Chart patterns: detected on candle i-1, confirmed by candle i.
        # Levels setups: detected on candle i itself, predicted at its close.
        found = [(s, True) for d in self.detectors for s in d(ctx, b)]
        found += [(s, False) for d in self.instant for s in d(ctx, i, self.pred["reward_risk"])]
        candidates = []
        for setup, needs_confirm in found:
            if setup.pattern not in self.only or setup.key in self.used_keys:
                continue
            last = self.last_signal.get((setup.pattern, setup.direction))
            if last is not None and i - last <= self.pred["horizon_candles"]:
                self._reject("cooldown")
                continue
            sig = self.confirm(setup, i) if needs_confirm else self.make_signal(setup, i)
            if sig:
                candidates.append(sig)
        if not candidates:
            return None
        directions = {s.direction for s in candidates}
        if len(directions) > 1:
            self._reject("conflicting directions")
            return None
        # Same direction: one prediction, all patterns credited, the widest stop is used.
        main = max(candidates, key=lambda s: s.risk)
        for s in candidates:
            self.used_keys.add(s.setups[0].key)
            self.last_signal[(s.patterns[0], s.direction)] = i
            if s is not main:
                main.patterns += s.patterns
                main.edge_reasons += s.edge_reasons
                main.setups += s.setups
        return main

    def scan(self, start=None, end=None):
        n = len(self.ctx.c)
        start = max(start or 0, self.ctx.warmup + 1)
        end = n if end is None else min(end, n)
        return [s for i in range(start, end) if (s := self.step(i))]
