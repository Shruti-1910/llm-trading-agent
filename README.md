# LLM Trading Agent

This is the paper-trading implementation of the build plan. Local paper mode is the default and does not connect to a broker. It moves configured prices with a seeded random walk each cycle and persists prices/account state in `paper_state.json`; the SQLite journal is `trader.sqlite3`. The guard caps each position at 40% of equity and total invested value at 80%, preserving a cash reserve. It also blocks borrowing, shorting, non-whitelisted assets, buys after the daily loss limit, and orders beyond the configured price band. At the game-over line, the runner attempts to liquidate supported holdings, closes the run, and stays halted for human review.

## Run

Python 3.9 or newer is supported. For an offline no-op cycle:

```sh
python main.py --no-agent
```

To request decisions from the configured model, install `requirements.txt`, configure the matching provider key in the environment (or install and run Ollama locally), then run:

```sh
python main.py
```

Run hourly as configured with `python main.py --loop` (or schedule `python main.py` hourly). A run that hits game over will stay halted on later invocations; inspect the account and journal and explicitly reset the state/database to start another run.

## Connect Delta Exchange India Demo

1. Create API credentials for a Delta Demo account. Use a key with only the required trading permission and keep the secret private.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Export credentials in your shell: `export DELTA_API_KEY='…'` and `export DELTA_API_SECRET='…'`.
4. In `config.toml`, set `broker = "delta"`, keep `delta_environment = "demo"`, and use INR spot symbols such as `BTC/INR`, `ETH/INR`, `SOL/INR`, or `XRP/INR`. The adapter checks these against Delta's live spot market list through CCXT.
5. Set `start_equity` to the Demo account's starting INR net equity, choose the same symbols in the watchlist, and run `python main.py --no-agent` first to check account/market access. Then run with the agent enabled.

The adapter submits IOC limit orders so unfilled quantity is not left open between cycles. Emergency game-over liquidation uses market sells for held spot balances. Production is blocked by default; to enable it, change `delta_environment = "live"` and `enable_live_trading = true` after Demo validation. Delta keys must match the selected environment. This integration only supports India INR spot markets; it does not support futures, options, or Global-region accounts.

The default model is `ollama/qwen3.5`; install it with `ollama run qwen3.5`. The replacement pool also includes `openai/gpt-5-mini` and `anthropic/claude-sonnet-4-6` (requiring their provider keys). Other supported choices include `gemini/...`, `ollama/...`, or `custom:https://your-agent/decision`. The custom endpoint accepts `{"prompt":"..."}` and returns `{"output":"...json text..."}`. Keys are never loaded from TOML or sent in the prompt.

The local paper broker fills a buy when its limit is at or above the current quote and a sell when its limit is at or below it. Its fee comes from `fee_rate`. Unfilled and partial orders are journaled with actual filled quantity. Delta wallet assets with available INR spot markets can be valued and included in emergency exits even when they are outside the agent watchlist; assets with no usable INR quote pause new buys and are called out in the snapshot. The Delta adapter is testnet-first; do not enable production until Demo behavior has been validated.

The default game-over line is 30% of the starting amount (`game_over_pct`). The stop is checked each cycle and again after orders. Hourly checks reduce the delay, but a market gap or exchange/API outage can move equity below that line before liquidation. This is a practical stop, not a mathematical guarantee that account value can never reach zero. The 40% per-position and 80% total-investment limits deliberately restore bounded trading freedom and a 20% cash reserve.

Run the safety simulation with `python test_guard.py`. The scoreboard compares account return with buy-and-hold in the configured benchmark, and the replacement review rotates through `models` after the configured number of weeks if the current model trails the benchmark. Price history, reflection/lesson generation, tournaments, and alerting remain later phases. The repository plan describes tiered caps; the current implementation uses fixed 40%/80% caps instead.
