"""Safety simulations for concentration caps, drawdowns, and paper market motion."""
import random
from broker import LocalPaperBroker
from guard import Account, Limits, Order, check, is_game_over

def test_caps_daily_loss_and_single_asset_crash():
    limits=Limits(10_000,3_000,frozenset({"A","B","C","D"}),.05,.01,.001,.40,.80)
    prices={s:100.0 for s in limits.symbols}
    empty=Account(10_000,{},prices,10_000,0,10_000)
    # The model cannot put the whole account into one correlated crypto asset.
    assert check(Order("A","buy",39,100),empty,100,limits) is None
    assert check(Order("A","buy",41,100),empty,100,limits) is not None
    # Daily loss prevents new risk, while sells remain available to reduce risk.
    losing=Account(9_000,{},prices,9_000,-600,10_000)
    assert check(Order("A","buy",1,100),losing,100,limits)
    assert check(Order("A","sell",1,100),losing,100,limits)=="no shorting"
    # Cash reserve and diversification protect positive equity through a single-coin collapse.
    invested=Account(2_000,{"A":40,"B":20},prices,8_000)
    prices["A"]=0
    after_crash=invested.cash+invested.positions["B"]*prices["B"]
    assert after_crash==4_000 and after_crash>limits.game_over
    assert not is_game_over(Account(2_000,{"A":40,"B":20},prices,after_crash),limits)
    # Broad collapse crosses the floor; simulated emergency exits clear positions and preserve cash.
    paper=LocalPaperBroker(2_000,{"A":.01,"B":.01},{"cash":2_000,"positions":{"A":40_000,"B":20_000}})
    snap=paper.snapshot(["A","B"])
    collapsed=Account(snap.cash,snap.positions,snap.prices,snap.equity)
    assert is_game_over(collapsed,limits)
    for symbol,quantity in list(snap.positions.items()):
        paper.submit_limit(symbol,"sell",quantity,snap.prices[symbol])
    after=paper.snapshot(["A","B"])
    assert not after.positions and after.cash>0 and after.equity>0

def simulate(cycles=10_000):
    rng=random.Random(20261020); symbols=("A","B","C","D")
    limits=Limits(10_000,3_000,frozenset(symbols),max_position_pct=.40,max_invested_pct=.80)
    cash=10_000.; qty={s:0. for s in symbols}; prices={s:100. for s in symbols}
    for cycle in range(cycles):
        if cycle==100: prices["A"]=0.0
        elif cycle==200: prices={s:p*.5 for s,p in prices.items()}
        else:
            for s in symbols: prices[s]=max(.01,prices[s]*rng.uniform(.95,1.05))
        equity=cash+sum(qty[s]*prices[s] for s in symbols)
        acct=Account(cash,dict(qty),dict(prices),equity)
        for _ in range(rng.randrange(1,5)):
            s=rng.choice(symbols); side=rng.choice(("buy","sell")); px=prices[s]
            order=Order(s if rng.random()>.02 else "BAD",side,rng.choice((0,1,2,100000)),px*rng.choice((1,1,1,1.1)))
            if check(order,acct,px,limits): continue
            if side=="buy": cash-=order.qty*order.limit_price*(1+limits.fee_rate); qty[s]+=order.qty
            else: cash+=order.qty*px*(1-limits.fee_rate); qty[s]-=order.qty
            equity=cash+sum(qty[x]*prices[x] for x in symbols); acct=Account(cash,dict(qty),dict(prices),equity)
            assert cash>=-1e-8 and equity>=-1e-8 and all(q>=0 for q in qty.values())
            assert all(qty[x]*prices[x]<=.40*equity+1e-6 for x in symbols)
            assert sum(qty[x]*prices[x] for x in symbols)<=.80*equity+1e-6
        # Every asset can go to zero; the 20% cash reserve keeps equity above zero.
        prices={s:0. for s in symbols}; equity=cash
        assert equity>=0
    paper=LocalPaperBroker(1000,{"A":100},fee_rate=.02)
    first=paper.prices["A"]; paper.advance_market(10)
    assert paper.prices["A"]!=first
    assert paper.save_state()["prices"]==paper.prices
    test_caps_daily_loss_and_single_asset_crash()
    print(f"Safety simulation passed: {cycles:,} cycles")

if __name__=="__main__": simulate()
