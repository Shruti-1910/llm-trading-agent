# Levels Strategy — APPROVED 2026-10-25

Built from the concepts the user asked for: liquidity, support/resistance, trendlines, volume,
candle strength and momentum. Code: `trader/strategy.py`. Numbers: `[patterns.levels]` in `config.toml`.
Timeframe: 1h. Scoring, journal, keep rule, fees and shadow mode are shared with Module 3.

## Building blocks

| Concept | Rule | Default |
|---|---|---|
| Swing high / low | Extreme of the 3 candles on each side; known only 3 candles later | k = 3 |
| Support / resistance zone | ≥ 2 swing points (highs or lows) from the last 200 candles within 0.5 ATR | 200, 0.5, 2 |
| Trend | UP if the last 2 swing highs and lows both rise **and** close > 200-candle EMA; DOWN mirror; else range | 200 |
| Trendline | Line through the last 2 rising swing lows (UP) / falling swing highs (DOWN). The pullback that touches it (within 0.5 ATR) and closes beyond it is the 3rd touch | — |
| Liquidity | Equal highs / lows: ≥ 2 swing points within 0.25 ATR. Stops sit just beyond them | 0.25 |
| Sweep | Candle wick goes beyond the level, the close comes back inside, previous close was inside | — |
| Volume | Candle volume ÷ average of the previous 20 candles | 20 |
| Strong candle | Body ≥ 60% of range, close in the top (bottom) 25%, range ≥ 1 × ATR | 0.6, 0.25, 1.0 |
| Rejection candle | Wick pointing into the level ≥ 50% of the candle | 0.5 |

## Setups (prediction made at the close of the signal candle)

| Setup | Rule | Stop | Why |
|---|---|---|---|
| **A. sweep_buy / sweep_sell** | Sweep of a support zone or equal lows (resistance / equal highs for sells) + rejection candle + volume ≥ 1.5×. Not against a clear trend | 0.1 ATR beyond the sweep wick | Stops beyond obvious levels get triggered; the snap back shows the other side is exhausted |
| **B. pullback_buy / pullback_sell** | Trend UP (DOWN). Within the last 3 candles price dipped into a support zone (not more than 0.5 ATR through it) or onto the trendline, then a strong candle closes back beyond it with volume ≥ 1.2× | 0.1 ATR beyond the pullback extreme | Trend followers buy dips at value; the strong candle shows buyers are back |

Both: target 1.5 × risk; **room check** skips the trade if the next opposing zone is closer than
the target; fee filter, cooldown and conflict rules as in Module 2.

Implementation details chosen by Claude (not explicitly in the approved text): the pullback
window of 3 candles; the trendline's 3rd touch is the trade itself; when several levels are swept,
the one with the most swing points is recorded.

## 4-hour view (group `levels4h`, approved 2026-10-25)

Same setups, but zones, equal highs/lows and trend come from 4-hour candles built from the 1h
candles (a 4h candle is used only once all 4 hours have closed). Trend uses the 200-candle 4h EMA
(~33 days). Entries, stops, volume and candle checks stay on 1h. The 1h trendline is not used.
Room check and zone targets use 4h zones. Setups are named `*_4h`.

## Exit versions (approved 2026-10-25, `[exits]`, `--exit`)

| Exit | Target | Time limit (and cooldown) |
|---|---|---|
| E1 | 1.5 × risk | 6 candles |
| E2 | 1.5 × risk | 24 candles |
| E3 | Edge of the next opposing zone, capped at 3 × risk (no zone → 3 × risk) | 24 candles |

Keep rule: ≥ 30 decided, ≥ 50% accuracy and positive after fees. For E3 the accuracy bar is
dropped (targets vary in size): ≥ 30 decided and positive after fees.

## Testing

Train: up to 2025-12-31. Test: 2026 onwards, run **once**, only after training is final.

## Results (training period, 2024-01 → 2025-12)

| Timeframe | Predictions | Accuracy | After fees, per prediction |
|---|---|---|---|
| 1h | 143 | 40.7% | −0.08 R |
| 15m | 496 | 31.5% | −0.29 R |
| 5m | 1,063 | 39.5% | −0.27 R |

Exit and 4h comparison on 1h (training period, after fees per prediction, Unsure included):

| Group | E1 | E2 | E3 |
|---|---|---|---|
| levels (1h) | 40.7%, −0.08 R | 46.2%, −0.03 R | 32.6%, **+0.07 R** |
| levels4h | 34.5%, −0.29 R | 37.6%, −0.27 R | 24.6%, −0.30 R |

Random buy every hour (same stop size, E2): 40.3%, −0.19 R. levels E3 per setup: pullback_sell
+0.43 R (24), pullback_buy +0.19 R (33), sweep_buy +0.05 R (31), sweep_sell −0.20 R (43).
sweep_sell loses in every version. levels E2 keeps sweep_buy (30 decided, 50.0%, +0.04 R), but
a 15/30 result has an 18–36% chance of being luck.

### Candidate and the one-time 2026 test (2026-10-25)

Candidate (approved): 1h, exit E3, setups sweep_buy + pullback_buy + pullback_sell, judged as one
strategy (`--group candidate`, `[candidate]` in config).

| Period | Predictions | C / W / U | Accuracy | After fees per prediction | Verdict |
|---|---|---|---|---|---|
| Train 2024–2025 | 88 | 19 / 32 / 37 | 37.3% | +0.21 R | PASS |
| **Test 2026** | 47 | 2 / 22 / 23 | 8.3% | −0.23 R | **FAIL** |

Charts of the test losses follow the rules (no bug). BTC rose 43k → 123k in training and fell
88k → 58k (now 81k) in 2026; the training profit came mostly from buys in the uptrend
(Unsure predictions alone averaged +0.35 R). The 2026 data is now used and must not be used to
tune this strategy.

Only 5m sweep_buy passed the keep rule (39 decided, 51.3%) and it still loses −0.15 R per
prediction after fees, because Unsure predictions also pay fees. The test period has not been run.
