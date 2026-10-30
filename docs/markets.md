# Multi-market research (approved 2026-10-30)

The BTC trend rules, unchanged, tested on daily candles of 10 other markets (`python3 -m trader markets backtest`).
Pass rule = same as BTC: train 2019-23 positive, >= 4/5 training years positive, check 2024-25 positive.
Detailed agent reports: reports/markets/crypto.md, india.md, commodities.md (git-ignored).

| Market | Train 19-23 | Years + | Check 24-25 | 2026 | Verdict | Note |
|---|---|---|---|---|---|---|
| BTC (Binance daily) | +33.8 | 4/5 | +1.3 | +5.5 | PASS (narrow) | Delta 1h version: 3/5 years, check 0.0 -> FAIL |
| ETH | +27.5 | 5/5 | +9.2 | +4.0 | PASS | check +0.6 without best trade |
| BNB | +42.2 | 4/5 | +8.7 | -0.6 | PASS (narrow) | ~0 R without top 5 trades |
| SOL | +50.3 | 4/5* | -5.1 | +0.7 | FAIL | *no 2019 data |
| XRP | +18.6 | 3/5 | +24.0 | -0.1 | FAIL | one trade = +34.7 R |
| NIFTY | +17.3 | 3/5 | -12.9 | -4.2 | FAIL | flat over 19 years |
| BANKNIFTY | +13.5 | 3/5 | -4.4 | +0.3 | FAIL | good before 2019 |
| USDINR | -7.2 | 2/5 | -0.8 | -4.8 | FAIL | bad Yahoo data; RBI limits speculation |
| GOLD | +5.8 | 2/5 | +10.3 | +4.7 | FAIL | Yahoo high/low broken, rolls not adjusted |
| SILVER | -7.8 | 2/5 | +9.5 | +0.8 | FAIL | same data problems |
| CRUDE | +16.5 | 5/5 | -2.4 | +3.8 | FAIL | monthly roll jumps |

Portfolio BTC+ETH+BNB (1R per trade): +143 R over 326 trades, max drawdown -10.7 R, every year since 2018 positive
(2025 only +2.8). Daily returns correlate 0.7-0.83, so it is one crypto-trend bet, not three independent ones.
No code bugs found (crypto agent replayed all 388 trades independently: 0 mismatches).
