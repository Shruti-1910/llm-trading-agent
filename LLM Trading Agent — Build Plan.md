# LLM Trading Agent — Build Plan

*how-to · personal project · last updated 2026-10-20*

## Purpose

Plan for building a trading bot where an AI model (Claude, GPT, Gemini, a local model, or any other agent)
gets the **whole amount X**, makes its own decisions, and learns from its mistakes. Its losses have real
consequences, and the account value must never reach 0.

## TL;DR

-**The model gets all of X. Nothing is held back.** No cash is reserved. How much the model can risk depends only on how it trades.
-**Code protects the money, not the AI.** A small checker written in plain code (the **Guard**) approves or
 rejects every order. The AI only *proposes* orders and never holds the broker login.
-**The account can't go negative, and reaching 0 is practically impossible.** It uses a cash account, buys only
 (no shorting), and never borrows, so no holding can be worth less than 0. Each holding is capped at a share of
 the account, so 0 would need every holding to go bust at once. A **game-over line G** (for example 30% of X)
 sells everything and stops the run.
-**The pressure comes from rules in code, not from words in the prompt.** Losses automatically shrink the model's
 limits, gains restore them, hitting G ends its run, and lagging buy-and-hold gets it replaced by another model.
 It sees all of this in every prompt.
-**Swapping the model is a config change.** One function, `decide(prompt) -> json`, sits behind
 [LiteLLM](GitHub - BerriAI/litellm: The fastest, litest AI Gateway. Rust core with Python SDK. Call 100+ LLM A…), so `anthropic/...`, `openai/...`, `gemini/...` and
 `ollama/...` all work.
-**"Learning" means memory, not retraining.** Every decision and its result go into a journal, and a reflection
 step writes short lessons that go into the next prompt.

---

## 1. Being honest about what this can and cannot do

| Claim | Reality |
|---|---|
| "X never hits 0" | **Practically yes, but not mathematically.** With buy-only, no borrowing, and capped position sizes, the value can never go negative. Reaching 0 would need every holding to collapse at the same time. The only way to make it *mathematically* certain is to keep reserved cash, which you chose not to do. |
| "The model has pressure to improve" | **The pressure comes from consequences enforced in code.** An LLM does not feel fear. Telling it "you'll be fired" does not make it smarter, and it can push it to gamble to win back losses. What works is showing it the facts (equity, distance to game-over, rank) and having code enforce the consequences (§5). |
| "It learns from mistakes" | **Partly.** It keeps lessons in its prompt, and its weights never change. Lessons can be wrong. |
| "The AI will make money" | **Not guaranteed.** Most LLM traders do worse than buy-and-hold once fees are counted. The scorecard in §8 tells you the truth. |
| "I can lose at most X − G" | **Usually.** An overnight crash can drop prices past G before the bot can sell. That is why position caps and liquid assets matter. |

Not financial advice. Check your jurisdiction's rules on retail algorithmic trading before going live
(for example SEBI in India or FINRA/SEC in the US). Run it on a personal machine with personal API keys, never on
employer hardware or keys.

---

## 2. Architecture

```mermaid
flowchart LR
 M[Market data<br/>broker API] --> C[Context builder]
 J[(Journal<br/>SQLite)] --> C
 L[Lessons] --> C
 S[Scoreboard<br/>tier, distance to G, rank] --> C
 C -->|prompt| A["Agent: decide()<br/>Claude / GPT / Gemini / any"]
 A -->|proposed orders JSON| G{"Guard<br/>(plain code)"}
 G -->|rejected + reason| J
 G -->|approved| B[Broker<br/>paper → live]
 B -->|fills, equity| J
 J --> R[Reflection<br/>same decide()]
 R --> L
 J --> S
```

One cycle (run by cron, for example once a day or once an hour, never on every price tick):

1. Pull account state (cash, positions, equity) and recent prices for a fixed watchlist.
2. Work out the current **tier** from equity vs. X (§3), and check for game-over.
3. Build the prompt: state, scoreboard, current limits, the last N decisions and their outcomes, top lessons.
4. `decide(prompt)` returns proposed orders as JSON.
5. Parse and validate the JSON. If it is invalid, **place no orders** and log it.
6. The Guard checks each order against the current tier. Rejected orders are logged with a reason, and that reason
 goes into the next prompt.
7. Approved orders go to the broker as **limit orders**.
8. Log everything. Once a trade closes, or once a day, run the reflection step.

### Files (keep it this small)

```
trader/
 config.toml model, symbols, X, game-over line, tiers, cadence
 agent.py decide() + prompt building + JSON parsing
 guard.py tiers + order checks + game-over (§3)
 broker.py thin wrapper on the broker SDK (paper/live is one flag)
 journal.py SQLite: decisions, orders, equity, lessons, runs
 main.py one cycle; called by cron
 test_guard.py simulation test: random orders and crashes never push equity to 0 or negative
```

---

## 3. Keeping X above 0 with all of X in play

The model gets all of X. Safety comes from **what it is allowed to hold** and a **game-over line**, not from
reserved cash.

### Why the value stays above 0

1.**It can't go negative.** In a cash account with buy-only trading and no margin, shorting, or options, every
 holding is worth at least 0 and you can never owe the broker money.
2.**Reaching exactly 0 would need every holding to collapse at once.** No single holding may exceed the tier's
 size cap (25% at most), and the whitelist holds only large, liquid stocks or broad ETFs. A broad index ETF
 going to 0 is not a realistic event.
3.**The game-over line G is a stop, not a reserve.** When `equity ≤ G` (for example 30% of X), the bot sells
 everything, halts, and logs the run as a loss for that model. In normal markets you lose at most about X − G.

### Losses shrink the limits, gains restore them

How much risk the model may take depends on how it has done. Nothing is held back at the start: at full
equity it may invest 100%.

| Equity vs. X | Max per position | Max invested | Meaning for the model |
|---|---|---|---|
| ≥ 90% | 25% | 100% | Full freedom |
| 70–90% | 15% | 80% | Warning: you are losing |
| 50–70% | 10% | 60% | Probation |
| G–50% | 5% | 40% | Last chance |
| ≤ G | — | 0% | **Game over**: sell everything, halt, swap the model |

This also blocks "revenge trading". After a loss the model *cannot* bet bigger to win it back, because its
limits have just shrunk.

### Defense layers

| Layer | What | Why |
|---|---|---|
| **L1. Broker settings** | Set the account to a **cash account** (margin off) and disable options and shorting at the broker. | The broker itself rejects leverage, even if your code has a bug. This layer is what keeps the value from going negative. |
| **L2. Guard (code)** | Whitelist, tier limits, price sanity check, daily loss cap. | Catches every order the AI proposes. |
| **L3. Game-over** | If `equity ≤ G`, sell everything, halt, and wait for a human. | Caps how far losses can go. |
| **L4. AI isolation** | The AI never sees API keys and its output is only parsed as data. Anything malformed becomes "no orders". | A prompt injection or a hallucination cannot place an order directly. |

### Guard sketch

```python
from dataclasses import dataclass

FEE = 0.001 # per-side fee rate; set it from your broker
# (equity/X lower bound, max position pct, max invested pct), best tier first
TIERS = [(0.90, 0.25, 1.00), (0.70, 0.15, 0.80), (0.50, 0.10, 0.60), (0.0, 0.05, 0.40)]

@dataclass(frozen=True)
class Limits:
 start: float # X
 game_over: float # G, e.g. 0.30 * X
 symbols: frozenset[str]
 max_daily_loss_pct: float = 0.05
 max_price_dev: float = 0.01

def tier(equity: float, limits: Limits) -> tuple[float, float]:
 ratio = equity / limits.start
 return next((pos, inv) for floor, pos, inv in TIERS if ratio >= floor)

def is_game_over(acct, limits) -> bool:
 return acct.equity <= limits.game_over

def check(order, acct, last_price, limits) -> str | None:
 """Returns the rejection reason, or None if the order is allowed."""
 if is_game_over(acct, limits):
 return "game over: trading halted"
 if order.symbol not in limits.symbols:
 return "symbol not whitelisted"
 if order.qty <= 0 or order.side not in ("buy", "sell"):
 return "malformed order"
 if abs(order.limit_price / last_price - 1) > limits.max_price_dev:
 return "limit price too far from market"
 if order.side == "sell":
 return "no shorting" if order.qty > acct.qty(order.symbol) else None # selling is always allowed
 if acct.day_pnl <= -limits.max_daily_loss_pct * acct.start_of_day_equity:
 return "daily loss limit hit: no new buys today"
 max_pos, max_inv = tier(acct.equity, limits)
 cost = order.qty * order.limit_price * (1 + FEE)
 if cost > acct.cash:
 return "not enough cash (no borrowing)"
 if acct.position_value(order.symbol) + cost > max_pos * acct.equity:
 return f"position too large for current tier (max {max_pos:.0%})"
 if acct.positions_value + cost > max_inv * acct.equity:
 return f"over invested limit for current tier (max {max_inv:.0%})"
 return None
```

`test_guard.py`: simulate 10,000 random cycles. Each cycle has random orders (including deliberately bad ones)
and random price shocks, including one holding falling 100% and the whole market falling 50% overnight.
Assert that equity is never negative, cash never goes below 0, and no single crash short of *every* holding
going to 0 brings equity to 0. Use only the standard library `random`, no test framework.

---

## 4. Swappable models

Everything goes through one function:

```python
import litellm, tomllib

CFG = tomllib.load(open("config.toml", "rb"))

def decide(prompt: str) -> str:
 r = litellm.completion(
model=CFG["model"], # the only thing you change
messages=[{"role": "system", "content": SYSTEM},
 {"role": "user", "content": prompt}],
response_format={"type": "json_object"},
temperature=0.2,
 )
 return r.choices[0].message.content
```

```toml
# config.toml — pick one
model = "anthropic/claude-sonnet-5-5"
# model = "openai/<gpt-model>"
# model = "gemini/<gemini-model>"
# model = "ollama/llama3.1" # local, free
```

API keys come from environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), never
from config files or the prompt.

**Any other agent** (a CLI agent, an HTTP agent service, your own multi-step agent) fits by exposing the
same shape: text in, JSON out. Add a `model = "custom:http://..."` branch in `decide()` that POSTs the
prompt to it. One `if`, no plugin framework.

**Why no tool-calling:** the prompt already contains everything the agent needs. That works the same on every
provider and keeps the agent away from the broker. Add read-only tools such as `get_news(symbol)` only once
you can show that missing information is hurting decisions.

### Output contract

```json
{
 "thesis": "one paragraph: what you see and why",
 "orders": [
 {"symbol": "AAPL", "side": "buy", "qty": 2, "limit_price": 187.40,
 "reason": "pullback to 50-day avg, earnings beat", "exit_plan": "sell if closes below 182 or +8%"}
 ]
}
```

Validate it strictly: right types, every required key present, nothing extra. On any failure, place **no orders**
and log the raw text. An empty `orders` list ("do nothing") is a valid and often good answer.

---

## 5. Pressure and learning

### Pressure: consequences enforced by code

| Mechanism | How it works |
|---|---|
| **The model sees the stakes** | Every prompt opens with a scoreboard: equity vs. X, current tier and its limits, distance to game-over, return vs. buy-and-hold, and win rate. |
| **Losses take away freedom** | The tier table (§3). The model is told its limits just shrank and why. |
| **Gains give it back** | Recovering past a tier boundary restores the larger limits, so improving pays off. |
| **Game-over ends the run** | Hitting G sells everything and halts. The run is recorded as a failure for that model, and the next run starts with its lessons. |
| **Replacement** | If a model trails buy-and-hold for N weeks (for example 8), it is swapped for the next model in `config.toml`. Because models are swappable, this is a real rule, not a threat. |

The scoreboard block added to the prompt:

```
SCOREBOARD
Equity: $8,420 of $10,000 start (84%) Tier: WARNING (max 15%/position, 80% invested)
Distance to game-over ($3,000): $5,420 | vs buy-and-hold: -6.1% over 5 weeks
Replacement review in 3 weeks if still behind buy-and-hold.
```

Keep the tone factual. "You will be destroyed if you lose" makes models gamble more, not trade better.

### Learning: memory, not retraining

**Journal (SQLite, 5 tables):**

| Table | Columns |
|---|---|
| `runs` | model, started, ended, start_equity, end_equity, outcome (`active` / `game_over` / `replaced`) |
| `decisions` | run_id, ts, prompt_hash, thesis, raw_output |
| `orders` | decision_id, symbol, side, qty, limit_price, reason, guard_verdict, fill_price, fill_ts |
| `equity` | ts, cash, positions_value, equity, tier |
| `lessons` | ts, text, source_trade_ids, score, active |

**Reflection step** (runs when a position closes, plus a short daily recap):

> Here is the trade you proposed, your stated reason and exit plan, and what actually happened (P&L,
> price path, whether you followed the plan). In at most 2 sentences, write a concrete, testable lesson.
> If the outcome was luck rather than skill, say so.

When a tier drops, also run a **post-mortem**: "Your equity fell from 92% to 84% of X. Which decisions
caused it? What will you do differently?" Its answer becomes a lesson with a high starting score.

**Feedback into the next prompt:** the last 10 decisions with their outcomes and Guard rejections, plus the top 10
lessons ranked by `score`. Raise a lesson's score when trades that cite it make money, lower it when they lose,
and turn it off below 0. Lessons belong to the *account*, not the model, so a replacement model inherits what the
previous one learned.

**Guardrail on learning:** lessons can change *strategy*, never *limits*. The Guard does not read lessons.

---

## 6. Prompt sketch (system)

```
You are a portfolio manager trading an account that started at ${X}. Your goal is to beat buy-and-hold
of {benchmark} over months, not days. Doing nothing is often correct.

Your performance has consequences, enforced outside you:
- Losses lower your tier and shrink your position and investment limits. Gains restore them.
- If equity reaches ${G}, all positions are sold and your run ends.
- If you trail buy-and-hold for {N} weeks, you are replaced by another model.

Hard rules (violating orders are rejected and logged): buy-only, no borrowing, whitelist {symbols},
limit orders within 1% of last price, current tier limits shown in the SCOREBOARD.

You will see: the scoreboard, account state, positions, recent prices/indicators, your recent decisions
and their outcomes, and lessons learned so far. Respond ONLY with JSON matching the schema.
```

---

## 7. Build phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Choose market and broker** | Pick one market. Options: **Alpaca** (US stocks and crypto, free paper trading), **ccxt** with a crypto exchange testnet, **Interactive Brokers** (global, paper account). Prefer stocks/ETFs over crypto: crypto moves faster and makes "never 0" harder. | Paper account works and you can read balance and positions by API |
| **1. Skeleton** | `broker.py`, `journal.py`, `main.py` running one cycle with a *hard-coded* "buy 1 share" decision | One cycle runs end to end on paper and the journal has rows |
| **2. Guard** | `guard.py` with tiers and game-over, plus `test_guard.py`; cash-account broker settings applied | The simulation test passes 10k cycles, and a deliberately bad order is rejected on paper |
| **3. Agent** | `decide()` + prompt + scoreboard + JSON validation, defaulting to Claude | Runs on a cron schedule and malformed output leads to no orders |
| **4. Learning and pressure** | Reflection, post-mortems on tier drops, lesson scoring, runs table, replacement rule | Lessons show up in prompts, and a tier drop triggers a post-mortem |
| **5. Backtest tournament** | Replay the last 1–2 years of daily bars through the same `main.py` with a simulated broker. Each model gets its own X on **identical** data. | A scorecard per model (§8). Watch for look-ahead bias: give the model only data up to the simulated date |
| **6. Paper tournament** | 4–8 weeks on paper. Run 2–3 models side by side, each with its own paper account. | The winner beats or roughly matches buy-and-hold after fees and AI cost, and none hit game-over |
| **7. Live, small** | Give real X to the winning model, starting small | Two weeks with no surprises; scale X only after months |

---

## 8. Scorecard (per model, per run)

| Metric | Why |
|---|---|
| Total return vs. **buy-and-hold** of the same watchlist | If it does not beat this, the AI adds nothing |
| Max drawdown and lowest tier reached | How close it came to game-over |
| Time spent at full freedom (top tier) | Whether it keeps its freedom |
| Sharpe ratio | Return per unit of risk |
| Win rate, average win / average loss | Whether lessons are improving trade quality |
| Guard rejection rate | Whether it understands its limits; this should fall over time |
| AI cost per month | Must be well below the profit, or the bot loses money overall |

---

## 9. Running cost and operations

-**Cadence:** daily or hourly. A daily cycle with a mid-tier model costs a few dollars a month.
-**AI spend cap:** set a monthly budget in LiteLLM or at the provider. AI costs come out of your pocket, not X.
-**Alerting:** send a message (Telegram, email) on a tier drop, game-over, daily-loss halt, or three malformed
 outputs in a row.
-**Restarts:** each cycle reads the current state from the broker, not from memory, so a crash never leaves
 stale positions behind.

---

## 10. Deliberately skipped

| Skipped | Add when |
|---|---|
| Multi-agent setups (analyst, risk, and trader agents) | A single agent's scorecard plateaus and you can measure the gain |
| Vector DB / RAG memory | You have more than about 200 lessons and the top-10 ranking stops being enough |
| Fine-tuning / reinforcement learning | Never for a personal bot; too costly for the gain |
| Web dashboard | The SQLite journal plus one notebook stops being enough |
| News and sentiment tools | The backtest shows decisions are missing information, not reasoning |
| Intraday or high-frequency trading | Never. LLM latency and cost make it a losing game |
| Shorting, margin, options | Never; they allow losses beyond X, which breaks "never 0" |
