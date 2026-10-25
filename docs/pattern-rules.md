# Pattern Rules (Module 2) — APPROVED 2026-10-25

Source: "11 chart patterns you should know" (forex.com), from `trading analysis Forex.docx`.
Market: BTCUSD perpetual, 5-minute candles. All numbers below are configurable in `config.toml`.

## A. Shared building blocks

| Term | Exact rule | Default |
|---|---|---|
| **ATR** | Average True Range over the last 14 candles. Used as the "unit of volatility" so rules adapt to quiet and wild markets. | 14 |
| **Swing high** | A candle whose high is the highest of the `k` candles before and `k` after it. It is only *known* `k` candles later — the agent never uses a swing it couldn't have seen yet. | k = 3 (15 min) |
| **Swing low** | Same, with the lowest low. | k = 3 |
| **"Equal" levels** | Two swing prices within `0.5 × ATR` of each other. | 0.5 ATR |
| **"Rising / falling" levels** | Each swing differs from the previous by more than `0.5 × ATR`. | 0.5 ATR |
| **Uptrend before pattern** | The 2 swing highs and 2 swing lows before the pattern are each higher than the one before (ascending staircase). Downtrend = mirror. | — |
| **Breakout** | A candle **closes** beyond the pattern's line by at least `0.1 × ATR` (a wick through doesn't count). | 0.1 ATR |
| **Volume check** (triangles, as the article says) | Breakout candle volume ≥ `1.5 ×` the 20-candle average. | 1.5× |
| **Confirmation** (article: "wait one or two candles … if they're green") | After the breakout candle, the next `1` candle must be green (UP) / red (DOWN) and still close beyond the breakout level. The prediction is made at that candle's close. | 1 candle |
| **Max pattern length** | Patterns longer than this are ignored (avoids "seeing" patterns in random noise over days). | 144 candles (12 h) |

### Every prediction records
- **Direction** (UP / DOWN), **entry** = close of the confirmation candle
- **Stop** = the price where the pattern has clearly failed (per pattern below, per the article: "beyond the previous high" for bearish, "below the previous significant low" for bullish)
- **Target** = entry ± **1.5 × risk** (your plan: 1.5–2× risk)
- **Pattern height** (article's measured move) — the setup is **skipped** if the height is smaller than the target distance, i.e. the pattern isn't big enough to pay for itself
- **Skipped** if stop distance < `2 ×` round-trip fees (≈ 0.24%) — otherwise fees eat most of the profit
- **Edge reason** (below), which goes into the journal

## B. The 11 patterns

| # | Pattern | Direction | Detection rule | Stop | Edge reason (why it should work) |
|---|---|---|---|---|---|
| 1a | **Ascending staircase** (buy the dip) | UP | Uptrend (2 higher highs + 2 higher lows). Price pulls back, forms a new swing low **above** the previous swing low, then a candle closes above the prior candle's high. | Below the new higher low | Trend followers buy pullbacks; momentum persists |
| 1b | **Descending staircase** (sell the rally) | DOWN | Mirror of 1a. | Above the new lower high | Same, short side |
| 2 | **Ascending triangle** | UP | Prior uptrend. ≥2 swing highs "equal" (flat resistance) + ≥2 swing lows "rising". Breakout close above resistance with volume check. | Below last swing low | Sellers defend a fixed level while buyers step in higher each time; stop-loss orders cluster above resistance |
| 3 | **Descending triangle** | DOWN | Prior downtrend. ≥2 swing lows "equal" (flat support) + ≥2 swing highs "falling". Breakout close below support with volume check. | Above last swing high | Mirror: buyers at a fixed level get exhausted; stops cluster below support |
| 4 | **Symmetrical triangle** | Breakout side | ≥2 swing highs "falling" + ≥2 swing lows "rising" (lines converge). Breakout close through either line with volume check. Direction = breakout side. | Last swing on the opposite side | Volatility compression is followed by expansion |
| 5 | **Flag** (bull / bear) | With the pole | **Pole:** a move of ≥ 3 × ATR within ≤ 12 candles. **Flag:** next 5–30 candles form a channel sloping *against* the pole (best-fit lines through highs and lows, slopes same sign and within 30% of each other), giving back ≤ 50% of the pole. Breakout close out of the channel in the pole's direction. | Bull: flag low. Bear: flag high | Short pause / profit-taking after a strong impulse, then the trend resumes |
| 6 | **Wedge** (rising / falling) | Rising → DOWN, Falling → UP | ≥2 swing highs and ≥2 swing lows, both lines sloping the **same** way and **converging**. Rising wedge: breakout close below the lower line. Falling: above the upper line. | Rising: last swing high. Falling: last swing low | Momentum fading inside the trend (each push smaller) |
| 7 | **Double top** | DOWN | Prior uptrend. Two swing highs "equal", ≥ 10 candles apart, second not higher than first + tolerance. Swing low between them (the **neckline**) at least 1.5 × ATR below the tops. Trigger: close below the neckline. | Above the higher top | Buyers fail twice at the same level; sellers take control |
| 8 | **Double bottom** | UP | Mirror of 7 (W shape, prior downtrend, close above the neckline). | Below the lower bottom | Sellers fail twice at the same level |
| 9 | **Head and shoulders** | DOWN | Prior uptrend. Three swing highs: middle (head) higher than both shoulders by > 0.5 × ATR; shoulders within 1 × ATR of each other. Neckline = line through the two swing lows between them. Trigger: close below the neckline. | Above the right shoulder | Each rally weaker; failed new high traps late buyers |
| 10a | **Rounded top** | DOWN | Over 40–144 candles, closes fit an upside-down U (curve fit quality R² ≥ 0.7, peak in the middle 30–70% of the window, depth ≥ 3 × ATR). Trigger: close below the level where the curve started. | Above last swing high | Gradual shift from buyers to sellers |
| 10b | **Rounded bottom** | UP | Mirror (U shape). Trigger: close above the level where the curve started. | Below last swing low | Gradual shift from sellers to buyers |
| 11 | **Cup and handle** | UP | A rounded bottom (10b) whose right side returns to within 1 × ATR of the left rim = **cup**. Then a **handle**: a pullback of ≤ 50% of cup depth, lasting ≤ 1/3 of the cup's length. Trigger: close above the handle's high. | Below handle low | Second shakeout before the uptrend; sellers exhausted |

**Not included:** inverse head and shoulders (not in the article; user: not needed now).

## C. Rules for one detection → one prediction
- The same pattern instance can only produce one prediction (no repeats on the next candles).
- If two patterns trigger on the same candle in **opposite** directions → no prediction (conflict = no edge).
- If two trigger in the **same** direction → one prediction, both patterns credited.

## D. Decisions (2026-10-25)
- Prediction horizon **N = 6** candles (30 min). Backtests may compare 4 / 6 / 8.
- Outcome: **Correct** = target hit before stop within N; **Wrong** = stop hit first; **Unsure** = neither within N (kept separate from accuracy).
- Target fixed at **1.5 × risk**.
- Rule details are taken as given from the forex.com article.
- **Height rule OFF**: the "skip if pattern height < target" filter rejected every flag and double top/bottom (their stop is beyond the pattern, so 1.5 × risk always exceeds the height). Pattern quality is judged by measured accuracy instead.
- **Cooldown**: the same pattern can't predict the same direction again within N (6) candles.
- **Agreeing patterns**: same direction on the same candle → one prediction, all patterns credited, the widest stop used. Opposite directions → no prediction.
