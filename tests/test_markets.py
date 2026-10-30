import unittest

import numpy as np

from trader import markets, scoring
from trader.setups import DOWN, UP

E = {"breakout_days": 20, "average_days": 50, "atr_days": 14,
     "initial_stop_atr": 2.0, "trail_atr": 3.0, "max_days": 30}
NO_FEES = {"taker_rate": 0.0, "gst_rate": 0.0, "funding_rate_8h": 0.0}


def candles(closes):
    c = np.array(closes, float)
    return {"t": np.arange(len(c), dtype=np.int64) * 86400, "o": c.copy(),
            "h": c + 1, "l": c - 1, "c": c}


class TrendDailyTest(unittest.TestCase):
    def test_breakout_up_then_stopped(self):
        closes = [100.0] * 60 + [105.0] + [106.0] * 5 + [90.0] * 5
        tr = markets.trend_trades(candles(closes), E, NO_FEES)[0]
        self.assertEqual(tr.direction, UP)
        self.assertEqual(tr.entry_index, 60)
        self.assertEqual(tr.entry, 105.0)
        self.assertEqual(tr.exit_reason, "stop")    # trail never got above the initial stop
        self.assertEqual(tr.exit_price, 90.0)       # gapped through: exits at the open

    def test_breakout_down_time_exit_and_open(self):
        closes = [100.0] * 60 + list(np.linspace(95, 60, 40))
        trades = markets.trend_trades(candles(closes), E, NO_FEES)
        self.assertEqual(trades[0].direction, DOWN)
        self.assertEqual(trades[0].exit_reason, "time")
        self.assertEqual(trades[0].exit_index, 60 + 30)
        self.assertGreater(trades[0].net_r, 0)

    def test_last_trade_still_open(self):
        tr = markets.trend_trades(candles([100.0] * 60 + [95.0] + [94.0] * 5), E, NO_FEES)[-1]
        self.assertEqual((tr.direction, tr.exit_reason, tr.result), (DOWN, "open", scoring.PENDING))

    def test_no_signal_in_flat_market(self):
        self.assertEqual(markets.trend_trades(candles([100.0] * 120), E, NO_FEES), [])


if __name__ == "__main__":
    unittest.main()
