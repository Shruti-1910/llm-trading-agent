"""Deterministic order checks. The model never gets broker credentials."""
from dataclasses import dataclass
import math
from typing import Mapping, Optional

FEE = 0.001

@dataclass(frozen=True)
class Limits:
    start: float
    game_over: float
    symbols: frozenset[str]
    max_daily_loss_pct: float = 0.05
    max_price_dev: float = 0.01
    fee_rate: float = FEE
    max_position_pct: float = 0.40
    max_invested_pct: float = 0.80

@dataclass(frozen=True)
class Account:
    cash: float
    positions: Mapping[str, float]
    prices: Mapping[str, float]
    equity: float
    day_pnl: float = 0.0
    start_of_day_equity: float = 0.0

    def qty(self, symbol: str) -> float:
        return float(self.positions.get(symbol, 0))

    def position_value(self, symbol: str) -> float:
        return self.qty(symbol) * float(self.prices.get(symbol, 0.0))

    @property
    def positions_value(self) -> float:
        return sum(self.position_value(s) for s in self.positions)

@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    qty: float
    limit_price: float

def tier(equity: float, limits: Limits) -> tuple[float, float]:
    if limits.start <= 0:
        raise ValueError("starting equity must be positive")
    return (0.0, 0.0) if equity <= limits.game_over else (limits.max_position_pct, limits.max_invested_pct)

def tier_name(equity: float, limits: Limits) -> str:
    return "GAME OVER" if equity <= limits.game_over else "ACTIVE"

def is_game_over(acct: Account, limits: Limits) -> bool:
    return acct.equity <= limits.game_over

def check(order: Order, acct: Account, last_price: float, limits: Limits) -> Optional[str]:
    if is_game_over(acct, limits): return "game over: trading halted"
    if order.symbol not in limits.symbols: return "symbol not whitelisted"
    if order.side not in ("buy", "sell") or isinstance(order.qty, bool) or not isinstance(order.qty, (int, float)) or not math.isfinite(order.qty) or order.qty <= 0:
        return "malformed order"
    if not isinstance(order.limit_price, (int, float)) or not math.isfinite(order.limit_price) or order.limit_price <= 0: return "malformed order"
    if not math.isfinite(last_price) or last_price <= 0 or abs(order.limit_price / last_price - 1) > limits.max_price_dev:
        return "limit price too far from market"
    if order.side == "sell": return None if order.qty <= acct.qty(order.symbol) else "no shorting"
    if acct.start_of_day_equity > 0 and acct.day_pnl <= -limits.max_daily_loss_pct * acct.start_of_day_equity:
        return "daily loss limit hit: no new buys today"
    cost = order.qty * order.limit_price * (1 + limits.fee_rate)
    if cost > acct.cash + 1e-9: return "not enough cash (no borrowing)"
    max_position, max_invested = tier(acct.equity, limits)
    if acct.position_value(order.symbol) + cost > max_position * acct.equity + 1e-9:
        return f"position exceeds {max_position:.0%} cap"
    if acct.positions_value + cost > max_invested * acct.equity + 1e-9:
        return f"investment exceeds {max_invested:.0%} cap"
    return None
