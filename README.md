# LLM Trading Agent

This is the initial paper-trading implementation of the build plan. Local paper mode is the default and does not connect to a broker or fetch live prices. Prices come from `paper_prices` in `config.toml`; edit those values to model scenarios. The local account persists in `paper_state.json` and the SQLite journal in `trader.sqlite3`. The agent has no per-position or total-investment caps and may use all available cash; the guard still prevents borrowing, shorting, non-whitelisted symbols, and orders beyond the configured price band. At the game-over line, the runner liquidates holdings and closes the run.

## Run

Python 3.9 or newer is supported. For an offline no-op cycle:

```sh
python main.py --no-agent
```

To request decisions from the configured model, install `requirements.txt`, configure the matching provider key in the environment (or install and run Ollama locally), then run:

```sh
python main.py
```

## Connect Delta Exchange India Demo

1. Create API credentials for a Delta Demo account. Use a key with only the required trading permission and keep the secret private.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Export credentials in your shell: `export DELTA_API_KEY='…'` and `export DELTA_API_SECRET='…'`.
4. In `config.toml`, set `broker = "delta"`, keep `delta_environment = "demo"`, and use INR spot symbols such as `BTC/INR`, `ETH/INR`, `SOL/INR`, or `XRP/INR`. The adapter checks these against Delta's live spot market list through CCXT.
5. Set `start_equity` to the Demo account's starting INR net equity, choose the same symbols in the watchlist, and run `python main.py --no-agent` first to check account/market access. Then run with the agent enabled.

The adapter submits IOC limit orders so unfilled quantity is not left open between cycles. Emergency game-over liquidation uses market sells for held spot balances. Production is blocked by default; to enable it, change `delta_environment = "live"` and `enable_live_trading = true` after Demo validation. Delta keys must match the selected environment. This integration only supports India INR spot markets; it does not support futures, options, or Global-region accounts.

For example, set `model = "openai/gpt-4o-mini"` and `OPENAI_API_KEY`, or use `anthropic/...`, `gemini/...`, `ollama/...`, or `custom:https://your-agent/decision`. The custom endpoint accepts `{"prompt":"..."}` and returns `{"output":"...json text..."}`. Keys are never loaded from TOML or sent in the prompt.

The local paper broker fills a buy when its limit is at or above the configured quote and a sell when its limit is at or below it. Unfilled orders remain journaled; they are not queued. The Delta adapter is testnet-first; do not enable production until Demo behavior has been validated.

The default game-over line is 30% of the starting amount (`game_over_pct`). The stop is checked at each cycle and immediately after simulated fills, but a real market gap or exchange/API outage can move equity below that line before liquidation. This is a practical stop, not a mathematical guarantee that account value can never reach zero.

Run the guard simulation with `python test_guard.py`. Review `LLM Trading Agent — Build Plan.md` for later phases such as market data, reflection, benchmark backtesting, tournaments, and alerting.
