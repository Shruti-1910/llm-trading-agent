# New Edge Research — APPROVED 2026-10-25

Code: `trader/edges.py`. Numbers: `[edges.trend]`, `[edges.overshoot]`, `[research]` in `config.toml`.
Run: `python3 -m trader data research-history` (once), then `python3 -m trader edges backtest`.
Trade lists: `reports/edges/*.csv`.

## Data and split
- Delta global BTCUSD 1h from 2019-06-01, then Delta India from **2024-03-01** (India's first
  weeks, Jan–Feb 2024, have prices frozen for hours or days; the feeds match from March on).
- Train 2019-07 → 2023-12 · Check 2024–2025 · 2026 "already seen" (second look only).
- Pass rule: positive after fees on train **and** in ≥ 4 of the 5 training years **and** on the check.
- Costs on every trade: round-trip taker fees + GST (0.118%) and funding 0.01% per 8 h held.

## A. Trend following
Daily close above the highest daily high of the previous 20 days and above the 50-day average
→ BUY (mirror → SELL). Initial stop 2 × daily ATR(14); trailing stop 3 × daily ATR behind the
best price, never moved away; exit after 30 days at the latest; one trade at a time. Correct =
profit after fees and funding. *Why:* slow reaction then herding makes big moves persist.

## C. Liquidation overshoot
1h candle with range ≥ 3 × ATR and volume ≥ 3 × the 20-candle average; if the next candle
doesn't extend the move, fade it at that candle's close. Stop at the extreme candle's low/high;
target its midpoint (skipped if < 1.5 × risk); judged within 24 candles. *Why:* forced
liquidations overshoot fair value.

## Results (2026-10-25)

| | Trend: trades | net R | R/trade | Overshoot: trades | net R | R/trade |
|---|---|---|---|---|---|---|
| Train 2019–23 | 56 | +30.5 | +0.54 | 28 | −19.8 | −0.71 |
| Check 2024–25 | 28 | 0.0 | 0.00 | 12 | −2.6 | −0.22 |
| Seen 2026 | 8 | +5.4 | +0.68 | 2 | 0.0 | +0.01 |
| Verdict | **FAIL** (3/5 training years positive; check flat) | | | **FAIL** | | |

Trend by year (net R): 2019 −3.7 (half year) · 2020 +15.9 · 2021 −0.7 · 2022 +1.5 · 2023 +17.4 ·
2024 +1.1 · 2025 −1.1 · 2026 +5.4. All 92 trades: +35.9 R (+0.39 R/trade), t-stat 1.7, worst
drawdown −5.5 R, longest losing streak 6. The total depends on the 5 biggest trades
(+13.6, +7.6, +7.0, +3.8, +3.6 R); without them it is ~0.

Overshoot: the extreme candle's low/high is usually retested within 1–3 hours, so the stop is hit
almost immediately (2019: 0 of 11 correct).

## Robustness check (training years only, 2026-10-25)

| Breakout | Trail 2.5 | Trail 3.0 | Trail 3.5 |
|---|---|---|---|
| 15 days | +27.1 R (4/5 yrs) | +34.2 R (4/5) | +37.0 R (4/5) |
| 20 days | +23.1 R (3/5) | **+30.5 R (3/5, approved)** | +32.6 R (4/5) |
| 30 days | +14.7 R (3/5) | +17.3 R (3/5) | +18.6 R (2/5) |

All nine are positive with the same shape (big 2020 and 2023), so the result isn't one lucky
setting. The approved setting is kept.

## Paper trading (live since 2026-10-25 17:36 UTC)

`python3 -m trader paper update` (daily at 06:00 IST via launchd), `paper status`, `paper resume`.
$1,000 simulated account; 1% risk per trade capped at 10% of capital per position. Graduation:
≥ 12 closed trades, positive after fees and funding, drawdown never ≤ −8 R (which also pauses
new entries). A replay from 2026-01-01 reproduces the 8 backtest trades: +5.41 R, +$25.75.
