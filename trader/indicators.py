"""Shared building blocks: ATR, swing points, average volume."""
from bisect import bisect_right

import numpy as np


def atr(high, low, close, period):
    """Simple average of True Range over `period` candles (NaN until enough data)."""
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= period:
        c = np.cumsum(np.concatenate([[0.0], tr]))
        out[period - 1:] = (c[period:] - c[:-period]) / period
    return out


def rolling_mean_prior(values, period):
    """Mean of the `period` values *before* each index (excludes the current one)."""
    out = np.full(len(values), np.nan)
    c = np.cumsum(np.concatenate([[0.0], values]))
    out[period:] = (c[period:-1] - c[:-period - 1]) / period
    return out


def find_swings(values, k, kind):
    """Indices j where values[j] is the strict extreme vs the k before and >= the k after.

    A swing at j is only knowable at candle j + k.
    """
    out = []
    for j in range(k, len(values) - k):
        left = values[j - k:j]
        right = values[j + 1:j + k + 1]
        v = values[j]
        if kind == "high" and v > left.max() and v >= right.max():
            out.append(j)
        elif kind == "low" and v < left.min() and v <= right.min():
            out.append(j)
    return out


class Swings:
    """Swing points, queried as 'what was confirmed by candle b'."""

    def __init__(self, indices, prices, k):
        self.idx = indices
        self.prices = prices
        self.k = k

    def confirmed(self, b, since=None):
        """List of (index, price) confirmed by candle b, optionally with index >= since."""
        end = bisect_right(self.idx, b - self.k)
        start = 0 if since is None else bisect_right(self.idx, since - 1)
        return [(self.idx[n], self.prices[self.idx[n]]) for n in range(start, end)]

    def last(self, b, n, before=None):
        """Last n confirmed swings, optionally only those with index < before."""
        limit = b - self.k if before is None else min(b - self.k, before - 1)
        end = bisect_right(self.idx, limit)
        return [(self.idx[m], self.prices[self.idx[m]]) for m in range(max(0, end - n), end)]


def ema(values, period):
    """Exponential moving average (uses only past and current values)."""
    out = np.empty(len(values))
    a = 2 / (period + 1)
    acc = values[0]
    for j, v in enumerate(values):
        acc = a * v + (1 - a) * acc
        out[j] = acc
    return out
