"""Run one scheduled decision cycle: python main.py [--no-agent]."""
import argparse, json
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from agent import build_prompt, decide, parse_output, prompt_hash
from broker import DeltaBroker, LocalPaperBroker
from guard import Account, Limits, Order, check, is_game_over, tier_name
from journal import Journal

def run_cycle(call_agent: bool=True) -> None:
    cfg=tomllib.loads(Path("config.toml").read_text())
    symbols=cfg["symbols"]
    limits=Limits(cfg["start_equity"],cfg["start_equity"]*cfg["game_over_pct"],frozenset(symbols),cfg["max_daily_loss_pct"],cfg["max_price_dev"],cfg["fee_rate"])
    cfg["limits"]=limits
    state_path=Path("paper_state.json")
    if cfg.get("broker", "local_paper") == "local_paper":
        state=json.loads(state_path.read_text()) if state_path.exists() else None
        broker=LocalPaperBroker(cfg["start_equity"],cfg["paper_prices"],state)
    elif cfg["broker"] == "delta":
        broker=DeltaBroker(cfg)
    else:
        raise ValueError("broker must be local_paper or delta")
    journal=Journal(cfg["database"])
    snapshot=broker.snapshot(symbols)
    active=journal.active_run(cfg["model"])
    run_id=active["id"] if active else journal.create_run(cfg["model"],snapshot.equity)
    acct=Account(snapshot.cash,snapshot.positions,snapshot.prices,snapshot.equity,start_of_day_equity=snapshot.equity)
    journal.record_equity(run_id,snapshot.cash,snapshot.positions_value,snapshot.equity,tier_name(snapshot.equity,limits))
    if is_game_over(acct,limits):
        did=journal.add_decision(run_id,"game-over","Game-over liquidation",'{"reason":"game over"}')
        for sym,qty in list(snapshot.positions.items()):
            if qty <= 0: continue
            order={"symbol":sym,"side":"sell","qty":qty,"limit_price":snapshot.prices[sym],"reason":"game over liquidation","exit_plan":"halt"}
            fill=(broker.liquidate(sym,qty) if hasattr(broker,"liquidate") else broker.submit_limit(sym,"sell",qty,snapshot.prices[sym]))
            journal.add_order(did,order,"approved: game-over liquidation",fill)
        journal.close_run(run_id,snapshot.equity,"game_over")
        if hasattr(broker,"save_state"): state_path.write_text(json.dumps(broker.save_state(),indent=2))
        print("GAME OVER: run halted; positions liquidated."); journal.close(); return
    if not call_agent:
        journal.add_decision(run_id,"manual","No-agent paper cycle",'{"thesis":"manual no-op","orders":[]}')
        print(f"Paper cycle initialized: equity {snapshot.equity:.2f}; no orders submitted.")
    else:
        prompt=build_prompt(cfg,acct,snapshot.prices,journal.recent(run_id),journal.lessons(),{"return_pct":(acct.equity/limits.start-1)*100})
        raw=""
        try:
            raw=decide(prompt,cfg["model"]); output=parse_output(raw,set(symbols)); thesis=output["thesis"]
        except Exception as exc:
            did=journal.add_decision(run_id,prompt_hash(prompt),None,raw or f"ERROR: {type(exc).__name__}: {exc}")
            print(f"No orders: model or response error ({exc}).")
        else:
            did=journal.add_decision(run_id,prompt_hash(prompt),thesis,raw)
            # Track pending orders within the cycle to prevent aggregate-limit bypass.
            for item in output["orders"]:
                order=Order(item["symbol"],item["side"],item["qty"],float(item["limit_price"]))
                current=broker.snapshot(symbols)
                live=Account(current.cash,current.positions,current.prices,current.equity,acct.day_pnl,acct.start_of_day_equity)
                reason=check(order,live,current.prices.get(order.symbol,0),limits)
                if reason:
                    journal.add_order(did,item,"rejected: "+reason); continue
                fill=broker.submit_limit(order.symbol,order.side,order.qty,order.limit_price)
                verdict="approved: filled" if fill is not None else "approved: limit not filled"
                journal.add_order(did,item,verdict,fill)
            print(f"Decision: {thesis}\nOrders proposed: {len(output['orders'])}")
    snapshot=broker.snapshot(symbols)
    if snapshot.equity<=limits.game_over:
        did=journal.add_decision(run_id,"game-over","Game-over liquidation",'{"reason":"equity reached game-over line"}')
        for sym,qty in list(snapshot.positions.items()):
            if qty <= 0: continue
            order={"symbol":sym,"side":"sell","qty":qty,"limit_price":snapshot.prices[sym],"reason":"game over liquidation","exit_plan":"halt"}
            fill=(broker.liquidate(sym,qty) if hasattr(broker,"liquidate") else broker.submit_limit(sym,"sell",qty,snapshot.prices[sym]))
            journal.add_order(did,order,"approved: game-over liquidation",fill)
        snapshot=broker.snapshot(symbols)
        journal.close_run(run_id,snapshot.equity,"game_over")
        print("GAME OVER: equity reached the configured floor; trading halted.")
    journal.record_equity(run_id,snapshot.cash,snapshot.positions_value,snapshot.equity,tier_name(snapshot.equity,limits))
    if hasattr(broker,"save_state"): state_path.write_text(json.dumps(broker.save_state(),indent=2))
    journal.close()

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--no-agent",action="store_true",help="record a paper no-op without calling the model"); args=parser.parse_args(); run_cycle(not args.no_agent)
