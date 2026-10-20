"""Standard-library randomized simulation for Guard invariants; run directly."""
import random
from guard import Account, Limits, Order, check

def simulate(cycles: int = 10_000) -> None:
    rng=random.Random(20261020)
    symbols=("A","B","C","D")
    limits=Limits(10_000,3_000,frozenset(symbols))
    cash=10_000.0; qty={s:0 for s in symbols}; prices={s:100.0 for s in symbols}
    for cycle in range(cycles):
        # Simulate shocks, including a single holding going to zero and broad drawdowns.
        if cycle==100: prices["A"]=0.0
        elif cycle==200: prices={s:p*.5 for s,p in prices.items()}
        else:
            for s in symbols: prices[s]=max(0.01,prices[s]*rng.uniform(.95,1.05))
        equity=cash+sum(qty[s]*prices[s] for s in symbols)
        acct=Account(cash,dict(qty),dict(prices),equity)
        for _ in range(rng.randrange(1,5)):
            symbol=rng.choice(symbols); side=rng.choice(("buy","sell")); price=prices[symbol]
            # Include malformed, over-sized, off-market, and short-sale attempts.
            proposed=Order(symbol if rng.random()>.02 else "BAD",side,rng.choice((0,1,2,100000)),price*rng.choice((1,1,1,1.1)))
            reason=check(proposed,acct,price,limits)
            if reason is not None: continue
            if side=="buy":
                cost=proposed.qty*proposed.limit_price*(1+limits.fee_rate)
                cash-=cost; qty[symbol]+=proposed.qty
            else:
                cash+=proposed.qty*price*(1-limits.fee_rate); qty[symbol]-=proposed.qty
            equity=cash+sum(qty[s]*prices[s] for s in symbols)
            acct=Account(cash,dict(qty),dict(prices),equity)
            assert cash >= -1e-8, (cycle,cash)
            assert all(q>=0 for q in qty.values()), (cycle,qty)
            assert equity >= -1e-8, (cycle,equity)
        equity=cash+sum(qty[s]*prices[s] for s in symbols)
        assert cash >= -1e-8 and equity >= -1e-8
        # One asset at zero cannot erase other holdings or cash.
        if prices["A"]==0 and any(qty[s]>0 for s in symbols if s!="A"):
            assert equity>0
    print(f"Guard simulation passed: {cycles:,} cycles")

if __name__=="__main__": simulate()
