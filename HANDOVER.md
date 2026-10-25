# Handover: Pattern-Learning Trading Agent (v2)

You are continuing a build with the user in `/Users/admin/Desktop/trading`. Read this whole file before doing anything.

## 1. How to work with this user (most important)

- **Confirm every piece of business logic before implementing it.** The user said: *"For each business logic first confirm with me then implement. I should be aware what is being implemented."* Propose exact rules and numbers in plain language, wait for a yes or corrections, then code. Never bundle unconfirmed logic into a module.
- The user isn't a trading-systems engineer. Explain in plain terms, give a recommendation with each question, and keep questions few and concrete.
- If a rule you proposed turns out to be flawed, say so plainly and show the data (this already happened with the height filter, see §4).
- After building a module: run it on real data, show the results (counts or accuracy tables), and offer chart snapshots.
- Only commit when the user asks. Nothing since the "Clear v1" commit is committed yet (see §5).

## 2. The user's plan (source of truth)

**Goal:** an AI agent that reads charts, finds a repeatable edge, predicts the next move, and trades small amounts with strict risk rules, learning from its results (especially failed analyses) over time.

1. **Setup.** One market, one timeframe, a fixed list of patterns, and a clear prediction: "price goes UP or DOWN within the next N candles".
2. **Edge.** A pattern that wins more than it loses, after fees, over many trades, with a written "why" (human behaviour, market structure, time-based). No clear why → treat it as luck and don't trade it. Recheck edges regularly and pause one when its win rate drops.
3. **Analyse.** Latest candles, support/resistance, trend, and whether a listed pattern is forming.
4. **Predict.** Match → UP/DOWN. No match → do nothing.
5. **Score.** Mark correct or wrong after N candles. Accuracy = correct ÷ total, tracked per pattern as well as overall.
6. **Confidence.** Accuracy, counted only after ≥30 predictions. Below that a pattern is "untested" and can't trade real money.
7. **Daily discipline.** Up to 5 predicted setups a day (all scored), at most 3 real-money trades a day, no revenge trading. Stop for the day once a limit is hit.
8. **When to trade.** Confidence above the threshold, at most 10% of capital per trade, at most 2–3 open trades.
9. **Risk.** A stop loss on every trade, set before entry: below support for buys, above resistance for sells, capped at 5–10% of the trade amount. Never move a stop further away. Risk ≈1% of capital per trade. Stop for the day after a 3% daily loss. Kill switch at 10–15% total drawdown, then the bot waits for the human. Fees and slippage are in every P&L.
10. **Take profit.** Target 1.5–2× risk. Move the stop to breakeven once in profit, then trail it. Take moderate profits.
11. **Journal.** Every prediction and trade: pattern, edge reason, confidence, entry, exit, stop, P&L, chart snapshot.
12. **Learn.** Every 20 trades, review the losses for common mistakes. Change rules only when a mistake repeats. Drop patterns whose accuracy stays low or whose reason no longer holds.
13. **Rollout.** Backtest → paper trade for a few weeks → live with a small amount, then scale slowly.

## 3. Decisions already made with the user

| Topic | Decision |
|---|---|
| Old project | Archived on git branch `archive/v1` (includes previously uncommitted files and data). `main` was cleared. |
| Market | **Delta Exchange India, BTCUSD perpetual futures** (long and short). Settles in USD. |
| Timeframe | **5-minute candles.** Prediction horizon **N = 6 candles** (30 min); backtests may compare 4/6/8. |
| AI role | **Code detects patterns and computes stats deterministically.** An LLM reviews losses every 20 trades and *suggests* rule changes, which the **user approves**. |
| Patterns | The 11 from forex.com's "11 chart patterns you should know" (user's copy: `~/Downloads/trading analysis Forex.docx`; the website returns 403 to automated fetches). No inverse head and shoulders ("not needed now"). The user said to take the article's rules as correct. |
| Prediction outcome | **Correct** = target hit before stop within N. **Wrong** = stop first. **Unsure** = neither within N (kept separate). |
| Target | Fixed **1.5 × risk**. |
| Height filter | **OFF.** It rejected every flag and double top/bottom. |
| Cooldown | Same pattern + direction can't predict again within N candles. **Approved.** |
| Agreeing patterns | Same direction on the same candle → one prediction, all patterns credited, widest stop. Opposite directions → no prediction. **Approved.** |
| Trading unlock (Module 4) | Prediction-only mode first, with a point for each correct prediction. Unlock trading when **overall** prediction accuracy is **80–85%**, after a **minimum of 30 predictions**, measured over the **last 20 predictions**. After unlocking, track a **separate trade accuracy** (real buy/sell wins and losses after fees). If trade accuracy later drops, go **back to prediction-only mode** until it re-qualifies. |
| Module 3 scoring | Approved as proposed: Correct/Wrong/Unsure over N; accuracy = C ÷ (C+W); price only; one candle touching both = **Wrong**; 5/day cap only in paper/live; per-pattern shown, overall controls unlock. |
| History | Backfilled to Delta's first BTCUSD 5m candle (2024-01-16): 291,647 candles, one 188-candle exchange gap (2024-01-28). |
| Keep list | A pattern is KEPT if backtest has **≥30 decided and ≥50% accuracy** (≈ break-even after fees: fees ≈0.27R at the median 0.44% stop). Others run in **shadow mode** (scored separately, never counted, can't block kept patterns). `[selection]` in config. |
| Fees | From Delta's API: maker 0.02%, taker 0.05%, plus 18% GST on the fee (secondary sources). Round trip with taker orders ≈ 0.118%. The candle API is free and public: no keys, no money involved. Fees only matter for real or paper trades. |

**Open, not yet asked in detail (raise when you reach the module):** how 80–85% is chosen (one number), whether the unlock is a hard gate in backtest or only in paper/live, position sizing under futures leverage, breakeven trigger (the plan says +5%, but on 5m a +1R trigger was recommended, see the plan critique), and the LLM provider for the review loop.

## 4. What's built (Modules 1–2 done)

```
config.toml              all business parameters ([market] [fees] [storage] [patterns] [prediction])
trader/config.py         loads TOML (tomli on Python 3.9)
trader/data.py           Module 1: Delta public candle API → SQLite, incremental, gap check
trader/indicators.py     ATR, swing highs/lows (confirmed k candles later = no lookahead), avg volume
trader/patterns.py       Module 2: 9 detector functions covering all 11 patterns, Scanner (confirmation, filters, cooldown, conflicts)
trader/charts.py         PNG snapshot of a signal (pattern points, entry/stop/target, N-candle window)
trader/__main__.py       CLI
docs/pattern-rules.md    APPROVED exact rules for every pattern + decisions log
tests/                   8 tests, all passing (incl. a no-lookahead test)
data/market.sqlite3      105,120 BTCUSD 5m candles, 2025-10-25 → 2026-10-25, 0 gaps (git-ignored)
```

Commands:
```sh
python3 -m trader data update                       # fetch new candles
python3 -m trader data status
python3 -m trader patterns scan --snapshots 3       # counts + PNGs into reports/patterns/
python3 -m pytest -q tests
```

Environment: Python 3.9.6 (no `tomllib`, so `tomli` from requirements.txt), numpy 2.0.2, matplotlib 3.9.4. Delta's history API returns at most 4,000 candles per call, newest first; `DeltaCandleClient.fetch` pages through.

Latest scan (height filter off): **2,255 predictions in 12 months (~6/day)**. Staircases ~1,190, wedges ~540, flags 169, double top/bottom 164, rounded 138, symmetrical triangle 44, cup and handle 45, head and shoulders 16, ascending/descending triangles 12. Accuracy has **not** been measured yet; that's Module 3.

Snapshot review: flag, cup and handle, double top, head and shoulders and staircase looked correct. Wedges built from swings only a few candles apart look weak but follow the rules; let accuracy judge them.

## 5. Git state

- `v2-research`: commit `fe1af62`, all v2 work (Modules 1–3, levels strategy, tests, docs). Created from `main` at the user's request to commit (2026-10-25); merge into `main` when the user wants.
- `main`: commit `f84fdd8` ("Clear v1 project…").
- `archive/v1`: the full old project.
- `data/` and `reports/` are git-ignored (rebuild with `python3 -m trader data update`).

## 6. Module 3 DONE (2026-10-25), next decision pending

Built: `trader/scoring.py` (score, tally, keep_list), `trader/journal.py` (data/journal.sqlite3, streams all/main/shadow), `python3 -m trader predict backtest|report [--snapshots N]`, `Scanner(only=...)`, history backfill in `data.update`. 20 tests pass. Uncommitted.

Results on 2.8 years, N=6: 6,475 predictions, **32.2% accuracy** (396 C / 834 W / 5,245 Unsure, 81% Unsure). **Keep list is empty**: frequent patterns are 23–37%; rare ones lack 30 decided. With N=12/24 still nothing qualifies (best: bull flag 41%, H&S 42.5%, symmetrical triangle falls to 41%). Filters tested on staircases/wedges (swing staleness, line overlap, stop size, chase, 200-EMA trend, session) add at most ~5 points. Conclusion shown to user: these patterns show no edge on 5m BTC as coded.

Timeframe test (user chose option 1, 2026-10-25): same rules and candle counts on 15m and 1h (`--resolution` flag; config still 5m; journal has a resolution column). 2.8 years each:
- 5m: 6,475 predictions, 32.2%. Break-even after fees ~51%.
- 15m: 2,591 predictions, 36.3%. Break-even ~47%. Keep list empty (best: descending staircase 42%).
- 1h: 715 predictions, 42.2%. Break-even ~44%. Keep list = **descending staircase only** (31 C / 25 W, 55.4%).
  Caveats shown to user: unstable by half-year (33%…75%); total after fees incl. Unsure trades ≈ −1.2 R over 169 predictions (break-even); ~48% chance one of 45 pattern×timeframe tests passes by luck.
Conclusion: 1h is the best timeframe, but there is still no proven edge.

**Levels strategy (user request, 2026-10-25):** user asked for a strategy built on liquidity, S/R, trendlines, volume, candle strength. Proposal approved as written: setups A (liquidity sweep) + B (trend pullback), **1h timeframe** (config now `resolution = "1h"`), **train ≤ 2025-12-31 / test 2026 once**. Rules: `docs/levels-strategy.md`, code `trader/strategy.py`, config `[patterns.levels]` and `[testing]`. CLI: `predict backtest --group levels|chart --period train|test|all`. Report now shows the after-fees result incl. Unsure. 27 tests pass.
Training results: 1h 40.7% (143 predictions, −0.08 R each after fees); 15m 31.5%; 5m 39.5%. Nothing profitable after fees. **Test period NOT run yet** (don't run it until training is final).
Keep rule now also needs a positive after-fees result (approved; `keep_require_net_positive`).

**4h view + exit versions (approved 2026-10-25):** group `levels4h` (zones/liquidity/trend from 4h, entries on 1h) and `--exit E1|E2|E3` (`[exits]` in config; E3 = next-zone target capped at 3R, 24 candles; for E3 the keep rule drops the 50% bar). 31 tests pass. Training results (after fees per prediction): levels E1 −0.08 R, E2 −0.03 R, **E3 +0.07 R** (131 predictions); levels4h is worse in every version (−0.27 to −0.30 R). Random hourly buy = −0.19 R. sweep_sell loses in every version (−0.20 R). Details in docs/levels-strategy.md.
**One-time 2026 test DONE (user chose: 1h levels + E3, sweep_sell dropped, judged as one strategy; `--group candidate`):** train PASS (88 predictions, +0.21 R each) → **test FAIL** (47 predictions, 2 C / 22 W / 23 U, −0.23 R each). No bug found in charts. Cause: training was a bull market (43k→123k), 2026 fell (88k→58k); the edge was trend drift. **2026 is now spent as a test set**; don't tune on it.
Status: after ~25 rule/timeframe/exit variants, no edge has survived. Don't build Modules 4–7 (real-money trading) on this. Options offered to the user: commit the work; build paper mode to collect fresh live data for shadow strategies (new hold-out); research a different kind of edge; or pause.

## 7. Remaining roadmap (confirm each before building)

| # | Module | Plan steps |
|---|---|---|
| 4 | Confidence and unlock (prediction accuracy → trade accuracy, fallback) | 6, 8 |
| 5 | Risk and sizing (stop rules, 1% risk, 10% cap, max open trades, futures leverage) | 8, 9 |
| 6 | Trade management (1.5R target, breakeven, trailing) | 10 |
| 7 | Discipline (5 predictions/3 trades a day, 3% daily loss, kill switch, no revenge) | 7, 9 |
| 8 | Journal (full: predictions + trades + snapshots) | 11 |
| 9 | Learning loop (every 20 trades: group losses by pattern/MFE/MAE/time, LLM suggests, user approves) | 12 |
| 10 | Rollout: backtest runner → paper trading on live candles → Delta futures (demo first) | 13 |

Expect the 80–85% gate to be hard to reach. A 1.5R target needs ~40% before fees but **~50% after fees** at our typical stop size. If Module 3 shows real accuracies far below 80%, present that honestly and let the user decide; don't change the gate yourself.
