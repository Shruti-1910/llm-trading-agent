"""Run a scheduled decision cycle: python main.py [--no-agent]."""
import argparse, json, time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from agent import build_prompt, decide, parse_output, prompt_hash
from broker import DeltaBroker, LocalPaperBroker
from guard import Account, Limits, Order, check, is_game_over, tier_name
from journal import Journal

def execution_fields(result):
    if result is None: return None, 0.0
    if hasattr(result, "filled_qty"):
        return result.average_price, result.filled_qty
    return float(result), 0.0

def run_cycle(call_agent: bool=True) -> bool:
    cfg=tomllib.loads(Path("config.toml").read_text())
    symbols=cfg["symbols"]
    limits=Limits(cfg["start_equity"],cfg["start_equity"]*cfg["game_over_pct"],frozenset(symbols),cfg["max_daily_loss_pct"],cfg["max_price_dev"],cfg["fee_rate"],cfg.get("max_position_pct",.40),cfg.get("max_invested_pct",.80))
    cfg["limits"]=limits
    state_path=Path("paper_state.json")
    journal=Journal(cfg["database"])
    try:
        latest=journal.latest_run()
        if latest and latest["outcome"]=="game_over":
            print("HALTED: the previous run reached game over. Human review and an explicit reset are required before restarting.")
            return False
        if cfg.get("broker", "local_paper") == "local_paper":
            state=json.loads(state_path.read_text()) if state_path.exists() else None
            broker=LocalPaperBroker(cfg["start_equity"],cfg["paper_prices"],state,cfg["fee_rate"])
            broker.advance_market(cfg.get("paper_volatility_pct",2.0))
        elif cfg["broker"] == "delta": broker=DeltaBroker(cfg)
        else: raise ValueError("broker must be local_paper or delta")
        snapshot=broker.snapshot(symbols)
        if latest and latest["outcome"]=="active": cfg["model"]=latest["model"]
        elif latest and latest["outcome"]=="replaced":
            models=cfg.get("models",[cfg["model"]]); prior=latest["model"]
            cfg["model"]=models[(models.index(prior)+1)%len(models)] if prior in models else cfg["model"]
        active=journal.active_run(cfg["model"])
        benchmark=cfg.get("benchmark",symbols[0])
        run_id=active["id"] if active else journal.create_run(cfg["model"],snapshot.equity,benchmark,snapshot.prices.get(benchmark,0))
        day_start=journal.day_start_equity(run_id,snapshot.equity)
        acct=Account(snapshot.cash,snapshot.positions,snapshot.prices,snapshot.equity,snapshot.equity-day_start,day_start)
        stats=journal.run_stats(run_id,snapshot.equity,snapshot.prices.get(benchmark,0))
        if stats["weeks"]>=cfg.get("replacement_weeks",8) and stats["buy_hold_return_pct"] is not None and stats["return_pct"]<stats["buy_hold_return_pct"]:
            journal.close_run(run_id,snapshot.equity,"replaced")
            models=cfg.get("models",[cfg["model"]]); idx=models.index(cfg["model"]) if cfg["model"] in models else -1
            cfg["model"]=models[(idx+1)%len(models)]
            run_id=journal.create_run(cfg["model"],snapshot.equity,benchmark,snapshot.prices.get(benchmark,0))
            day_start=journal.day_start_equity(run_id,snapshot.equity)
            acct=Account(snapshot.cash,snapshot.positions,snapshot.prices,snapshot.equity,0,day_start)
            stats=journal.run_stats(run_id,snapshot.equity,snapshot.prices.get(benchmark,0))
            print(f"Replacement review: starting {cfg['model']}.")

        def liquidate(current):
            did=journal.add_decision(run_id,"game-over","Game-over liquidation",'{"reason":"equity reached game-over line"}')
            for sym,qty in list(current.positions.items()):
                if qty<=0 or sym not in current.prices: continue
                order={"symbol":sym,"side":"sell","qty":qty,"limit_price":current.prices[sym],"reason":"game over liquidation","exit_plan":"halt"}
                try:
                    result=broker.liquidate(sym,qty) if hasattr(broker,"liquidate") else broker.submit_limit(sym,"sell",qty,current.prices[sym])
                    px,filled=execution_fields(result)
                    journal.add_order(did,order,"game-over liquidation",px,filled)
                except Exception as exc:
                    journal.add_order(did,order,f"liquidation error: {type(exc).__name__}: {exc}")

        journal.record_equity(run_id,snapshot.cash,snapshot.positions_value,snapshot.equity,tier_name(snapshot.equity,limits),snapshot.prices.get(benchmark))
        if is_game_over(acct,limits):
            liquidate(snapshot); snapshot=broker.snapshot(symbols); journal.close_run(run_id,snapshot.equity,"game_over")
            journal.record_equity(run_id,snapshot.cash,snapshot.positions_value,snapshot.equity,tier_name(snapshot.equity,limits),snapshot.prices.get(benchmark))
            print("GAME OVER: run halted; liquidation attempted for all supported holdings.")
            if hasattr(broker,"save_state"): state_path.write_text(json.dumps(broker.save_state(),indent=2))
            return False
        if not call_agent:
            journal.add_decision(run_id,"manual","No-agent paper cycle",'{"thesis":"manual no-op","orders":[]}')
            print(f"Paper cycle initialized: equity {snapshot.equity:.2f}; no orders submitted.")
        else:
            prompt=build_prompt(cfg,acct,snapshot.prices,journal.recent(run_id),journal.lessons(),stats)
            raw=""
            try:
                raw=decide(prompt,cfg["model"]); output=parse_output(raw,set(symbols)); thesis=output["thesis"]
            except Exception as exc:
                did=journal.add_decision(run_id,prompt_hash(prompt),None,raw or f"ERROR: {type(exc).__name__}: {exc}")
                print(f"No orders: model or response error ({exc}).")
            else:
                did=journal.add_decision(run_id,prompt_hash(prompt),thesis,raw)
                for item in output["orders"]:
                    try:
                        order=Order(item["symbol"],item["side"],item["qty"],float(item["limit_price"]))
                        current=broker.snapshot(symbols)
                        day_start=journal.day_start_equity(run_id,current.equity)
                        live=Account(current.cash,current.positions,current.prices,current.equity,current.equity-day_start,day_start)
                        reason="wallet has unpriced assets; buys paused" if current.unpriced_assets and order.side=="buy" else check(order,live,current.prices.get(order.symbol,0),limits)
                        if reason:
                            journal.add_order(did,item,"rejected: "+reason); continue
                        result=broker.submit_limit(order.symbol,order.side,order.qty,order.limit_price)
                        px,filled=execution_fields(result)
                        verdict=(f"{result.status}; filled {filled:g}" if filled>0 else "limit not filled")
                        journal.add_order(did,item,"approved: "+verdict,px,filled)
                    except Exception as exc:
                        journal.add_order(did,item,f"order error: {type(exc).__name__}: {exc}")
                print(f"Decision: {thesis}\nOrders proposed: {len(output['orders'])}")
        # Always take a fresh snapshot and evaluate the hard floor, even after individual order failures.
        snapshot=broker.snapshot(symbols)
        acct=Account(snapshot.cash,snapshot.positions,snapshot.prices,snapshot.equity)
        if is_game_over(acct,limits):
            liquidate(snapshot); snapshot=broker.snapshot(symbols); journal.close_run(run_id,snapshot.equity,"game_over")
            print("GAME OVER: equity reached the configured floor; trading halted.")
        journal.record_equity(run_id,snapshot.cash,snapshot.positions_value,snapshot.equity,tier_name(snapshot.equity,limits),snapshot.prices.get(benchmark))
        if hasattr(broker,"save_state"): state_path.write_text(json.dumps(broker.save_state(),indent=2))
        if journal.latest_run()["outcome"]=="game_over": return False
        return True
    finally:
        journal.close()

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--no-agent",action="store_true",help="record a paper no-op without calling the model"); parser.add_argument("--loop",action="store_true",help="run continuously at the configured cadence"); args=parser.parse_args()
    while True:
        keep_running=run_cycle(not args.no_agent)
        if not args.loop or not keep_running: break
        try:
            cfg=tomllib.loads(Path("config.toml").read_text()); time.sleep(max(60,int(cfg.get("interval_seconds",3600))))
        except KeyboardInterrupt: break
