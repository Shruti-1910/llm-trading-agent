"""Provider-swappable decision interface and strict JSON contract."""
import hashlib
import json
from typing import Any
from guard import Account, Limits, tier_name

SYSTEM = 'You are a portfolio manager. Aim to beat buy-and-hold over months, not days. You choose trades within the position, total investment, and daily loss limits shown in the SCOREBOARD. You may buy or sell assets already held. You propose orders only; code checks each order. No borrowing, leverage, or short selling. Limit orders must stay within 1% of last price. At or below the configured game-over line, the account is liquidated and this run halts. Reply only as JSON: {"thesis": string, "orders": [{"symbol": string, "side": "buy"|"sell", "qty": positive number, "limit_price": positive number, "reason": string, "exit_plan": string}]}. No extra keys.'
ORDER_KEYS = {"symbol", "side", "qty", "limit_price", "reason", "exit_plan"}

def build_prompt(cfg: dict[str, Any], acct: Account, prices: dict[str, float], recent: list[dict], lessons: list[str], run_stats: dict) -> str:
    limits: Limits = cfg["limits"]
    dist = max(0.0, acct.equity - limits.game_over)
    price_lines = "\n".join(f"{s}: {p:.2f}" for s, p in prices.items())
    benchmark_return=run_stats.get("buy_hold_return_pct")
    benchmark_line=(f"vs buy-and-hold: {run_stats.get('return_pct',0)-benchmark_return:+.1f}% over {run_stats.get('weeks',0)} weeks" if benchmark_return is not None else "vs buy-and-hold: unavailable (no recorded benchmark baseline)")
    review_line=(f"Replacement review in {max(0,cfg.get('replacement_weeks',8)-run_stats.get('weeks',0))} weeks if still behind." if benchmark_return is not None else "Replacement review waits for a valid benchmark baseline.")
    return (f"SCOREBOARD\nEquity: {acct.equity:.2f} of {limits.start:.2f} ({acct.equity/limits.start:.0%}); "
            f"Status: {tier_name(acct.equity, limits)}; max {limits.max_position_pct:.0%} per position, {limits.max_invested_pct:.0%} total invested; daily buy halt at {limits.max_daily_loss_pct:.0%} loss.\n"
            f"Distance to game-over {limits.game_over:.2f}: {dist:.2f}; {benchmark_line}\n"
            f"{review_line}\n"
            f"No borrowing, leverage, or shorting. At or below the game-over line, positions are liquidated and this run halts.\n\n"
            f"ACCOUNT\nCash: {acct.cash:.2f}; positions: {json.dumps(dict(acct.positions))}\nMARKET PRICES\n{price_lines}\n"
            f"RECENT DECISIONS\n{json.dumps(recent[-10:],ensure_ascii=False)}\nLESSONS\n"
            f"{chr(10).join('- '+x for x in lessons[:10]) or '(none yet)'}\nReturn JSON only. Empty orders are valid.")

def decide(prompt: str, model: str) -> str:
    if model.startswith("custom:"):
        from urllib.request import Request, urlopen
        req = Request(model.removeprefix("custom:"), data=json.dumps({"prompt":prompt}).encode(), headers={"Content-Type":"application/json"}, method="POST")
        with urlopen(req, timeout=60) as response: result = json.loads(response.read())
        if not isinstance(result, dict) or not isinstance(result.get("output"), str): raise ValueError("custom response needs string output")
        return result["output"]
    try: import litellm
    except ImportError as exc: raise RuntimeError("Install LiteLLM: pip install -r requirements.txt") from exc
    result = litellm.completion(model=model, messages=[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}], response_format={"type":"json_object"}, temperature=.2)
    return result.choices[0].message.content or ""

def parse_output(raw: str, symbols: set[str]) -> dict[str, Any]:
    try: obj = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc: raise ValueError("output is not valid JSON") from exc
    if not isinstance(obj,dict) or set(obj)!={"thesis","orders"} or not isinstance(obj["thesis"],str) or not isinstance(obj["orders"],list):
        raise ValueError("response must contain exactly string thesis and array orders")
    for order in obj["orders"]:
        if not isinstance(order,dict) or set(order)!=ORDER_KEYS: raise ValueError("order keys do not match contract")
        if not isinstance(order["symbol"],str) or order["symbol"] not in symbols: raise ValueError("symbol is not whitelisted")
        if order["side"] not in ("buy","sell") or isinstance(order["qty"],bool) or not isinstance(order["qty"],(int,float)) or order["qty"]<=0: raise ValueError("invalid side or quantity")
        if isinstance(order["limit_price"],bool) or not isinstance(order["limit_price"],(int,float)) or order["limit_price"]<=0: raise ValueError("invalid limit price")
        if not isinstance(order["reason"],str) or not isinstance(order["exit_plan"],str): raise ValueError("reason and exit_plan must be strings")
    return obj

def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()
