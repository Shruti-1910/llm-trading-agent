"""Offline paper broker and exchange adapters sharing the same order interface."""
from dataclasses import dataclass
from typing import Optional, Protocol
import os
import random

@dataclass(frozen=True)
class Execution:
    filled_qty: float
    average_price: Optional[float]
    status: str = "closed"

@dataclass
class Snapshot:
    cash: float
    positions: dict[str,float]
    prices: dict[str,float]
    unpriced_assets: tuple[str, ...] = ()
    @property
    def positions_value(self) -> float: return sum(q*self.prices.get(s,0) for s,q in self.positions.items())
    @property
    def equity(self) -> float: return self.cash+self.positions_value

class Broker(Protocol):
    def snapshot(self, symbols: list[str]) -> Snapshot: ...
    def submit_limit(self, symbol: str, side: str, qty: float, price: float) -> Execution: ...

class LocalPaperBroker:
    def __init__(self, initial_cash: float, prices: dict[str,float], state: Optional[dict]=None, fee_rate: float=0.001):
        self.cash=float(initial_cash if state is None else state["cash"])
        self.positions=dict(state.get("positions",{}) if state else {})
        self.prices=dict(state.get("prices", prices) if state else prices)
        self.fee_rate=float(fee_rate)
        self.market_step=int(state.get("market_step", 0) if state else 0)
        self.seed=int(state.get("seed", 17) if state else 17)
    def advance_market(self, volatility_pct: float) -> None:
        rng=random.Random(self.seed+self.market_step)
        for symbol,price in self.prices.items():
            self.prices[symbol]=max(0.000001,price*(1+rng.gauss(0,volatility_pct/100)))
        self.market_step+=1
    def snapshot(self, symbols: list[str]) -> Snapshot:
        return Snapshot(self.cash,dict(self.positions),dict(self.prices))
    def submit_limit(self,symbol: str,side: str,qty: float,price: float) -> Optional[float]:
        market=self.prices[symbol]
        if not ((side=="buy" and price>=market) or (side=="sell" and price<=market)): return None
        if side=="buy":
            cost=qty*market*(1+self.fee_rate)
            if cost>self.cash: return None
            self.cash-=cost; self.positions[symbol]=self.positions.get(symbol,0)+qty
        else:
            if qty>self.positions.get(symbol,0): return None
            self.cash+=qty*market*(1-self.fee_rate); self.positions[symbol]-=qty
            if self.positions[symbol]==0: del self.positions[symbol]
        return Execution(qty,market)
    def save_state(self) -> dict: return {"cash":self.cash,"positions":self.positions,"prices":self.prices,"market_step":self.market_step,"seed":self.seed}


class DeltaBroker:
    """Delta India spot adapter. Defaults to the Demo/testnet endpoint."""
    INDIA_URLS = {
        "demo": "https://cdn-ind.testnet.deltaex.org",
        "live": "https://api.india.delta.exchange",
    }

    def __init__(self, cfg: dict):
        try:
            import ccxt
        except ImportError as exc:
            raise RuntimeError("Install CCXT with: pip install -r requirements.txt") from exc
        environment = cfg.get("delta_environment", "demo")
        if environment not in self.INDIA_URLS:
            raise ValueError("delta_environment must be demo or live")
        if environment == "live" and not cfg.get("enable_live_trading", False):
            raise RuntimeError("Live orders are disabled. Set enable_live_trading=true only after Demo validation.")
        api_key = os.getenv("DELTA_API_KEY")
        api_secret = os.getenv("DELTA_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError("Set DELTA_API_KEY and DELTA_API_SECRET in the environment")
        base_url = self.INDIA_URLS[environment]
        self.exchange = ccxt.delta({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 20000,
            "urls": {"api": {"public": base_url, "private": base_url}},
        })
        self.symbols = list(cfg["symbols"])
        self.markets = self.exchange.load_markets()
        for symbol in self.symbols:
            market = self.markets.get(symbol)
            if not market or not market.get("spot") or market.get("quote") != "INR":
                raise ValueError(f"{symbol} is not an available INR spot market on this Delta environment")
        self.environment = environment

    def snapshot(self, symbols: list[str]) -> Snapshot:
        balance = self.exchange.fetch_balance()
        totals = balance.get("total", {})
        cash = float(totals.get("INR") or 0.0)
        prices = {}
        positions = {}
        requested_symbols = list(symbols)
        supported_assets = {self.markets[s]["base"] for s in requested_symbols}
        unpriced = []
        for asset, amount in totals.items():
            if asset == "INR" or not amount:
                continue
            if asset not in supported_assets:
                pair=f"{asset}/INR"
                market=self.markets.get(pair)
                if market and market.get("spot") and market.get("quote")=="INR":
                    requested_symbols.append(pair)
                else:
                    unpriced.append(asset)
        for symbol in dict.fromkeys(requested_symbols):
            market = self.markets.get(symbol)
            if not market or not market.get("spot") or market.get("quote")!="INR":
                continue
            amount = float(totals.get(market["base"]) or 0.0)
            ticker = self.exchange.fetch_ticker(symbol)
            last = ticker.get("last")
            if last is None or float(last) <= 0:
                raise RuntimeError(f"Delta returned no usable last price for {symbol}")
            prices[symbol] = float(last)
            positions[symbol] = amount
        return Snapshot(cash, positions, prices, tuple(unpriced))

    def submit_limit(self, symbol: str, side: str, qty: float, price: float) -> Optional[float]:
        if symbol not in self.symbols:
            raise ValueError("symbol is outside configured Delta spot markets")
        amount = float(self.exchange.amount_to_precision(symbol, qty))
        limit_price = float(self.exchange.price_to_precision(symbol, price))
        if amount <= 0 or limit_price <= 0:
            raise ValueError("order rounded to zero at Delta market precision")
        # IOC avoids leaving an untracked GTC order open between agent cycles.
        response = self.exchange.create_order(symbol, "limit", side, amount, limit_price, {"time_in_force": "ioc"})
        filled = float(response.get("filled") or 0.0)
        average = response.get("average")
        if not average and filled > 0 and response.get("cost"):
            average=float(response["cost"])/filled
        return Execution(filled,float(average) if average else None,str(response.get("status") or "unknown"))

    def liquidate(self, symbol: str, qty: float) -> Optional[float]:
        """Emergency spot exit used only after the game-over floor is crossed."""
        if symbol not in self.markets or not self.markets[symbol].get("spot") or self.markets[symbol].get("quote")!="INR":
            raise ValueError(f"No supported INR spot market to liquidate {symbol}")
        amount = float(self.exchange.amount_to_precision(symbol, qty))
        if amount <= 0:
            return None
        response = self.exchange.create_order(symbol, "market", "sell", amount)
        filled = float(response.get("filled") or 0.0)
        average = response.get("average")
        if not average and filled > 0 and response.get("cost"):
            average=float(response["cost"])/filled
        return Execution(filled,float(average) if average else None,str(response.get("status") or "unknown"))
