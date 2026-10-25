"""Module 3: score predictions (rules approved by the user, 2026-10-25).

A prediction is made at the close of its confirmation candle i. We watch the next
N candles (i+1 .. i+N):
  - target reached first             -> CORRECT (+1 point)
  - stop reached first               -> WRONG
  - one candle touches both          -> WRONG (cautious: we can't tell which came first)
  - neither within N candles         -> UNSURE (shown, excluded from accuracy)
  - fewer than N candles closed yet  -> PENDING (paper/live only)
Accuracy = Correct / (Correct + Wrong). Price only: fees belong to trade accuracy.
"""
from dataclasses import dataclass

from trader.patterns import UP

CORRECT, WRONG, UNSURE, PENDING = "CORRECT", "WRONG", "UNSURE", "PENDING"


@dataclass
class Score:
    result: str
    candles_to_result: int   # candles after entry until hit (N for Unsure)
    mfe_r: float             # best move in our favour, in R (risk units)
    mae_r: float             # worst move against us, in R (positive number)
    final_r: float           # result in R: +reward_risk, -1, or close-to-close for Unsure


def score(direction, entry, stop, target, highs, lows, closes, horizon):
    """Score one prediction from the candles that FOLLOW the entry candle.

    highs/lows/closes: sequences starting at candle i+1 (may be shorter than horizon).
    """
    up = direction == UP
    risk = (entry - stop) if up else (stop - entry)
    mfe = mae = 0.0
    n = min(horizon, len(highs))
    for k in range(n):
        h, l = highs[k], lows[k]
        fav = (h - entry) if up else (entry - l)
        adv = (entry - l) if up else (h - entry)
        mfe, mae = max(mfe, fav / risk), max(mae, adv / risk)
        hit_stop = l <= stop if up else h >= stop
        hit_target = h >= target if up else l <= target
        if hit_stop:  # includes the case where both are touched on one candle
            return Score(WRONG, k + 1, mfe, mae, -1.0)
        if hit_target:
            return Score(CORRECT, k + 1, mfe, mae, abs(target - entry) / risk)
    if n < horizon:
        return Score(PENDING, n, mfe, mae, 0.0)
    last = closes[horizon - 1]
    final = ((last - entry) if up else (entry - last)) / risk
    return Score(UNSURE, horizon, mfe, mae, final)


def score_signal(signal, ctx, horizon):
    i = signal.index
    return score(signal.direction, signal.entry, signal.stop, signal.target,
                 ctx.h[i + 1:i + 1 + horizon], ctx.l[i + 1:i + 1 + horizon],
                 ctx.c[i + 1:i + 1 + horizon], horizon)


def tally(rows):
    """rows: iterable of result strings -> dict with counts, accuracy and points."""
    out = {CORRECT: 0, WRONG: 0, UNSURE: 0, PENDING: 0}
    for r in rows:
        out[r] += 1
    decided = out[CORRECT] + out[WRONG]
    out["total"] = sum(out[k] for k in (CORRECT, WRONG, UNSURE, PENDING))
    out["accuracy"] = out[CORRECT] / decided if decided else None
    out["points"] = out[CORRECT]
    return out


KEEP, SHADOW = "KEEP", "SHADOW"


def net_r(row, fee_rate):
    """A journal row's result in R after round-trip fees, as if it had been traded
    (+target R / -1 R / close-out R for Unsure). None while Pending."""
    if row["result"] == PENDING:
        return None
    return row["final_r"] - fee_rate * row["entry"] / abs(row["entry"] - row["stop"])


def keep_list(rows, fee_rate, min_decided, min_accuracy, require_net_positive=True):
    """Journal rows -> {pattern: (KEEP|SHADOW, reason)}. A prediction backed by several
    patterns counts for each of them."""
    by = {}
    for r in rows:
        for p in r["patterns"]:
            by.setdefault(p, []).append(r)
    out = {}
    for p, rs in by.items():
        t = tally(r["result"] for r in rs)
        decided = t[CORRECT] + t[WRONG]
        nets = [n for n in (net_r(r, fee_rate) for r in rs) if n is not None]
        net = sum(nets) / len(nets) if nets else 0.0
        if decided < min_decided:
            out[p] = (SHADOW, f"only {decided} decided (< {min_decided})")
        elif t["accuracy"] < min_accuracy:
            out[p] = (SHADOW, f"accuracy {t['accuracy']:.1%} < {min_accuracy:.0%}")
        elif require_net_positive and net <= 0:
            out[p] = (SHADOW, f"accuracy {t['accuracy']:.1%} but after fees {net:+.3f} R "
                              f"per prediction (needs > 0)")
        else:
            out[p] = (KEEP, f"{decided} decided, accuracy {t['accuracy']:.1%}, "
                            f"after fees {net:+.3f} R per prediction")
    return out
