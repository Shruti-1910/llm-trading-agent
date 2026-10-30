"""Command line entry point: python -m trader <command>."""
import argparse
import collections
import random

import numpy as np
from datetime import datetime, timezone

from trader import charts, config, data, edges, journal, paper, patterns, scoring


def fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "-"


def cmd_data(args, cfg):
    m = cfg["market"]
    db_path = config.resolve(cfg["storage"]["market_db"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = data.CandleStore(db_path)
    if args.action == "research-history":
        r = cfg["research"]
        start = int(datetime.strptime(r["global_start"], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
        end = int(datetime.strptime(r["splice"], "%Y-%m-%d")
                  .replace(tzinfo=timezone.utc).timestamp()) + 7 * 86400
        client = data.DeltaCandleClient(r["global_api_base"])
        n = data.download_range(store, client, m["symbol"], r["global_store_symbol"],
                                r["resolution"], start, end)
        count, first, last = store.summary(r["global_store_symbol"], r["resolution"])
        print(f"Global {m['symbol']} {r['resolution']}: fetched {n}; stored {count} candles, "
              f"{fmt(first)} -> {fmt(last)}; gaps: {len(store.gaps(r['global_store_symbol'], r['resolution']))}")
        return
    if args.action == "update":
        client = data.DeltaCandleClient(m["api_base"])
        n = data.update(store, client, m["symbol"], m["resolution"], m["history_days"])
        print(f"Fetched {n} new {m['resolution']} candles for {m['symbol']}.")
    count, first, last = store.summary(m["symbol"], m["resolution"])
    gaps = store.gaps(m["symbol"], m["resolution"])
    print(f"{m['symbol']} {m['resolution']}: {count} candles, {fmt(first)} -> {fmt(last)}")
    missing = sum(g[1] for g in gaps)
    print(f"Gaps: {len(gaps)} ({missing} missing candles)")
    for after, n in gaps[:10]:
        print(f"  after {fmt(after)}: {n} missing")


def cmd_patterns(args, cfg):
    m = cfg["market"]
    store = data.CandleStore(config.resolve(cfg["storage"]["market_db"]))
    candles = store.load(m["symbol"], m["resolution"])
    scanner = patterns.Scanner(candles, cfg["patterns"], cfg["prediction"], cfg["fees"])
    signals = scanner.scan()
    print(f"{len(signals)} predictions from {len(candles)} candles "
          f"({fmt(candles[0].time)} -> {fmt(candles[-1].time)})")
    counts = collections.Counter(p for s in signals for p in s.patterns)
    for p in patterns.PATTERNS:
        print(f"  {p:22s}{counts[p]:6d}")
    print("Setups rejected:")
    for reason, n in sorted(scanner.rejected.items(), key=lambda r: -r[1]):
        print(f"  {reason:28s}{n:6d}")
    if args.snapshots:
        out = config.resolve("reports/patterns")
        out.mkdir(parents=True, exist_ok=True)
        rng = random.Random(0)
        for p in patterns.PATTERNS:
            mine = [s for s in signals if s.patterns[0] == p]
            for n, s in enumerate(rng.sample(mine, min(args.snapshots, len(mine)))):
                charts.snapshot(scanner.ctx, s, out / f"{p}_{n}.png")
        print(f"Snapshots written to {out}")


def print_table(title, groups):
    print(f"\n{title}")
    print(f"  {'':22s}{'total':>6s}{'correct':>8s}{'wrong':>7s}{'unsure':>7s}{'pending':>8s}"
          f"{'accuracy':>10s}{'points':>7s}")
    for name, results in groups:
        t = scoring.tally(results)
        acc = f"{t['accuracy']:.1%}" if t["accuracy"] is not None else "-"
        print(f"  {name:22s}{t['total']:6d}{t[scoring.CORRECT]:8d}{t[scoring.WRONG]:7d}"
              f"{t[scoring.UNSURE]:7d}{t[scoring.PENDING]:8d}{acc:>10s}{t['points']:7d}")


def report(rows, fee_rate=None, last_n=20):
    if not rows:
        print("No predictions in the journal.")
        return
    print(f"{len(rows)} predictions, {fmt(rows[0]['time'])} -> {fmt(rows[-1]['time'])}")
    print_table("Overall", [("all", [r["result"] for r in rows])]
                + [(d, [r["result"] for r in rows if r["direction"] == d])
                   for d in (patterns.UP, patterns.DOWN)])
    by_pattern = results_by_pattern(rows)
    print_table("Per pattern (combined predictions count for each pattern)",
                [(p, by_pattern[p]) for p in patterns.PATTERNS if by_pattern[p]])
    decided = [r for r in rows if r["result"] in (scoring.CORRECT, scoring.WRONG)]
    recent = scoring.tally(r["result"] for r in decided[-last_n:])
    if recent["accuracy"] is not None:
        print(f"\nLast {min(last_n, len(decided))} decided predictions: {recent['accuracy']:.1%}")
    if fee_rate is not None:
        # As if every prediction were a trade: +1.5R / -1R / close-out R, minus round-trip fees.
        net = [n for n in (scoring.net_r(r, fee_rate) for r in rows) if n is not None]
        if net:
            print(f"Result after fees, every prediction incl. Unsure: {sum(net):+.1f} R total, "
                  f"{sum(net) / len(net):+.3f} R per prediction")
    unsure = [r["final_r"] for r in rows if r["result"] == scoring.UNSURE]
    if unsure:
        print(f"Unsure predictions ended on average at {sum(unsure) / len(unsure):+.2f} R")
    for res in (scoring.CORRECT, scoring.WRONG):
        sub = [r for r in rows if r["result"] == res]
        if sub:
            print(f"{res.title():8s} avg candles to result {sum(r['candles_to_result'] for r in sub) / len(sub):.1f}, "
                  f"avg best {sum(r['mfe_r'] for r in sub) / len(sub):.2f} R, "
                  f"avg worst {sum(r['mae_r'] for r in sub) / len(sub):.2f} R")


def results_by_pattern(rows):
    by = collections.defaultdict(list)
    for r in rows:
        for p in r["patterns"]:   # a prediction backed by 2 patterns counts for both
            by[p].append(r["result"])
    return by


def write_snapshots(ctx, signals, scores, per_group, resolution, horizon):
    out = config.resolve(f"reports/predictions/{resolution}")
    out.mkdir(parents=True, exist_ok=True)
    rng, snap = random.Random(0), {}
    for p in patterns.PATTERNS:
        for res in (scoring.CORRECT, scoring.WRONG):
            mine = [n for n, s in enumerate(signals)
                    if s.patterns[0] == p and scores[n].result == res]
            for k, n in enumerate(rng.sample(mine, min(per_group, len(mine)))):
                path = out / f"{p}_{res.lower()}_{k}.png"
                charts.snapshot(ctx, signals[n], path, horizon=horizon)
                snap[n] = str(path)
    return snap


def run_stream(jr, mode, symbol, stream, candles, cfg, only, ctx, snapshots=0, window=(None, None)):
    scanner = patterns.Scanner(candles, cfg["patterns"], cfg["prediction"], cfg["fees"],
                               only=only, ctx=ctx)
    horizon = cfg["prediction"]["horizon_candles"]
    signals = scanner.scan() if only else []
    lo, hi = window
    signals = [s for s in signals if (lo is None or s.time >= lo) and (hi is None or s.time < hi)]
    scores = [scoring.score_signal(s, ctx, horizon) for s in signals]
    snap = write_snapshots(ctx, signals, scores, snapshots,
                                                       cfg["market"]["resolution"], horizon) if snapshots else {}
    for n, (s, sc) in enumerate(zip(signals, scores)):
        jr.add(mode, stream, symbol, s, sc, snap.get(n))
    jr.commit()
    return len(snap)


def min_accuracy(cfg):
    """The fixed accuracy bar doesn't apply to zone targets (they vary in size)."""
    zone = cfg["prediction"].get("target_mode") == "zone"
    return 0.0 if zone else cfg["selection"]["keep_min_accuracy"]


def decide_keep(rows, cfg, fee_rate):
    sel = cfg["selection"]
    return scoring.keep_list(rows, fee_rate, sel["keep_min_decided"], min_accuracy(cfg),
                             sel["keep_require_net_positive"])


def print_keep_list(rows, cfg, fee_rate):
    sel = cfg["selection"]
    decisions = decide_keep(rows, cfg, fee_rate)
    extra = " and positive after fees" if sel["keep_require_net_positive"] else ""
    acc = f", >= {min_accuracy(cfg):.0%} accuracy" if min_accuracy(cfg) else ""
    print(f"\nKeep list (needs >= {sel['keep_min_decided']} decided{acc}{extra})")
    for p in patterns.PATTERNS:
        if p in decisions:
            status, why = decisions[p]
            print(f"  {p:22s}{status:8s}{why}")
    return decisions


def period_window(cfg, period):
    """(start, end) unix times for train / test / all. Train ends at the end of train_end."""
    end_of_train = int(datetime.strptime(cfg["testing"]["train_end"], "%Y-%m-%d")
                       .replace(tzinfo=timezone.utc).timestamp()) + 86400
    return {"train": (None, end_of_train), "test": (end_of_train, None),
            "all": (None, None)}[period]


def cmd_predict(args, cfg):
    m = cfg["market"]
    jr = journal.Journal(config.resolve(cfg["storage"]["journal_db"]), m["resolution"])
    mode = f"backtest-{args.group}-{args.period}-{args.exit}"
    group = (set(cfg["candidate"]["setups"]) if args.group == "candidate"
             else patterns.GROUPS[args.group])
    window = period_window(cfg, args.period)
    fee_rate = patterns.round_trip_fee_rate(cfg["fees"])
    pred = cfg["prediction"]
    target = (f"next zone, max {pred['max_reward_risk']}R" if pred["target_mode"] == "zone"
              else f"{pred['reward_risk']}R")
    print(f"Group: {args.group}   period: {args.period}   timeframe: {m['resolution']}   "
          f"exit: {args.exit} (target {target}, within {pred['horizon_candles']} candles)")
    if args.action == "backtest":
        store = data.CandleStore(config.resolve(cfg["storage"]["market_db"]))
        candles = store.load(m["symbol"], m["resolution"])
        ctx = patterns.Context(candles, cfg["patterns"])
        jr.clear(mode, m["symbol"])
        # 1. Evaluate every pattern together, as approved, to decide the keep list.
        n_snap = run_stream(jr, mode, m["symbol"], "all", candles, cfg, group, ctx,
                            args.snapshots, window)
        decisions = decide_keep(jr.rows(mode, "all", m["symbol"]), cfg, fee_rate)
        keep = {p for p, (st, _) in decisions.items() if st == scoring.KEEP}
        # 2. Kept patterns alone (the predictions that count) and 3. shadow patterns alone.
        run_stream(jr, mode, m["symbol"], "main", candles, cfg, keep, ctx, window=window)
        run_stream(jr, mode, m["symbol"], "shadow", candles, cfg, group - keep, ctx, window=window)
        if n_snap:
            print(f"{n_snap} snapshots written to reports/predictions/{m['resolution']}/")
    all_rows = jr.rows(mode, "all", m["symbol"])
    print("=== ALL PATTERNS (evaluation) ===")
    report(all_rows, fee_rate)
    print_keep_list(all_rows, cfg, fee_rate)
    print("\n=== KEPT PATTERNS: these predictions count toward accuracy and the unlock ===")
    report(jr.rows(mode, "main", m["symbol"]), fee_rate)
    print("\n=== SHADOW PATTERNS: tracked, never counted ===")
    report(jr.rows(mode, "shadow", m["symbol"]), fee_rate)
    if args.group == "candidate":
        # Judged as one strategy: every prediction counts toward a single verdict.
        whole = [dict(r, patterns=["candidate"]) for r in all_rows]
        status, why = decide_keep(whole, cfg, fee_rate).get("candidate", ("SHADOW", "no predictions"))
        print(f"\n*** CANDIDATE VERDICT ({args.period}): {'PASS' if status == scoring.KEEP else 'FAIL'}"
              f" - {why}")


def cmd_edges(args, cfg):
    import csv
    r = cfg["research"]
    store = data.CandleStore(config.resolve(cfg["storage"]["market_db"]))
    candles = data.load_research(store, cfg)
    ctx = patterns.Context(candles, cfg["patterns"])
    ts = lambda d: int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    periods = {name: (ts(r[name][0]), ts(r[name][1]) + 86400) for name in ("train", "check", "seen")}
    train_years = list(range(int(r["train"][0][:4]), int(r["train"][1][:4]) + 1))
    min_risk = cfg["prediction"]["min_risk_fee_mult"] * patterns.round_trip_fee_rate(cfg["fees"])
    runs = {"trend": edges.trend_trades(ctx, cfg["edges"]["trend"], cfg["fees"]),
            "overshoot": edges.overshoot_trades(ctx, cfg["edges"]["overshoot"], cfg["fees"], min_risk)}
    print(f"Research data: {len(candles)} 1h candles, {fmt(candles[0].time)} -> {fmt(candles[-1].time)}")
    out = config.resolve("reports/edges")
    out.mkdir(parents=True, exist_ok=True)
    for name, trades in runs.items():
        trades = [tr for tr in trades if tr.exit_reason != "open"]   # judge closed trades only
        year = lambda tr: datetime.fromtimestamp(tr.entry_time, timezone.utc).year
        within = lambda p: [tr for tr in trades if periods[p][0] <= tr.entry_time < periods[p][1]]
        by_year = {y: edges.summarize([tr for tr in trades if year(tr) == y])
                   for y in sorted({year(tr) for tr in trades})}
        print(f"\n=== {name.upper()} ===")
        print(f"  {'':16s}{'trades':>7s}{'correct':>8s}{'wrong':>7s}{'unsure':>7s}{'accuracy':>10s}"
              f"{'net R':>9s}{'R/trade':>9s}")
        def row(label, t):
            acc = f"{t['accuracy']:.1%}" if t["accuracy"] is not None else "-"
            print(f"  {label:16s}{t['total']:7d}{t[scoring.CORRECT]:8d}{t[scoring.WRONG]:7d}"
                  f"{t[scoring.UNSURE]:7d}{acc:>10s}{t['net_total']:+9.1f}{t['net_avg']:+9.3f}")
        for y, t in by_year.items():
            tag = " (train)" if y in train_years else ""
            row(f"{y}{tag}", t)
        train, check, seen = (edges.summarize(within(p)) for p in ("train", "check", "seen"))
        print("  " + "-" * 71)
        row("TRAIN 2019-23", train)
        row("CHECK 2024-25", check)
        row("SEEN 2026", seen)
        if trades:
            wins = [tr.net_r for tr in trades if tr.net_r > 0]
            losses = [tr.net_r for tr in trades if tr.net_r <= 0]
            print(f"  avg winner {np.mean(wins) if wins else 0:+.2f} R, avg loser "
                  f"{np.mean(losses) if losses else 0:+.2f} R, avg hold {np.mean([tr.hours for tr in trades]) / 24:.1f} days; "
                  f"buys {sum(tr.direction == patterns.UP for tr in trades)}, sells "
                  f"{sum(tr.direction == patterns.DOWN for tr in trades)}")
        ok, why = edges.verdict(by_year, train_years, train, check)
        print(f"  *** VERDICT: {'PASS' if ok else 'FAIL'} - {why}")
        with open(out / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["entry_time", "direction", "entry", "stop", "target", "exit_time", "exit_price",
                        "exit_reason", "result", "gross_r", "net_r", "hours"])
            for tr in trades:
                w.writerow([fmt(tr.entry_time), tr.direction, tr.entry, tr.stop, tr.target,
                            fmt(int(ctx.t[tr.exit_index])), tr.exit_price, tr.exit_reason,
                            tr.result, round(tr.gross_r, 3), round(tr.net_r, 3), tr.hours])
    print(f"\nTrade lists written to {out}")


def notify(lines):
    """macOS notification (best effort)."""
    import subprocess
    import sys
    if sys.platform != "darwin" or not lines:
        return
    text = "; ".join(lines).replace('"', "'")[:240]
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "Paper trading"'],
                   check=False)


def paper_status(book, cfg, state):
    p = cfg["paper"]
    start = state.get("start_time")
    print(f"Paper trading since {fmt(start)}  |  account ${book.equity:,.2f} "
          f"(start ${p['start_capital']:,.0f})  |  closed trades net {book.net_r:+.2f} R  |  "
          f"worst drawdown {book.max_drawdown_r:.2f} R")
    if book.paused_since:
        print(f"!! PAUSED since {fmt(book.paused_since)}: drawdown reached -{p['pause_drawdown_r']} R. "
              f"Review, then `python3 -m trader paper resume`.")
    opened = [r for r in book.rows if r["status"] == "open"]
    for r in opened:
        side = "BUY" if r["direction"] == "UP" else "SELL"
        print(f"OPEN {side} since {fmt(r['entry_time'])} @ {r['entry']:,.0f}  stop now {r['current_stop']:,.0f}  "
              f"last {r['_mark']:,.0f}  ({r['net_r']:+.2f} R so far)  size {r['size_btc']:.5f} BTC "
              f"(${r['notional']:,.0f}, risking ${r['risk_usd']:,.2f})")
    if not opened:
        print("No open trade.")
    closed = [r for r in book.rows if r["status"] == "closed"]
    if closed:
        print(f"\n  {'entry':18s}{'side':6s}{'entry':>10s}{'exit':>10s}  {'reason':7s}{'R':>7s}{'USD':>10s}")
        for r in closed:
            print(f"  {fmt(r['entry_time'])[:16]:18s}{'BUY' if r['direction'] == 'UP' else 'SELL':6s}"
                  f"{r['entry']:10,.0f}{r['exit_price']:10,.0f}  {r['exit_reason']:7s}"
                  f"{r['net_r']:+7.2f}{r['pnl_usd']:+10.2f}")
    ok, checks = paper.graduation(book, cfg)
    print("\nReady for real money? " + ("YES - all checks met (decide with the user)" if ok else "not yet"))
    for label, passed, value in checks:
        print(f"  [{'x' if passed else ' '}] {label}: {value}")


def cmd_paper(args, cfg):
    m, r = cfg["market"], cfg["research"]
    state = paper.State(config.resolve(cfg["storage"]["journal_db"]))
    if args.action == "start":
        if state.get("start_time"):
            print(f"Paper trading already started at {fmt(state.get('start_time'))}.")
            return
        state.set("start_time", paper.now())
        state.set("resumes", [])
        print(f"Paper trading started at {fmt(state.get('start_time'))}. "
              f"Only daily closes after this can open trades.")
        return
    if not state.get("start_time"):
        print("Paper trading not started. Run: python3 -m trader paper start")
        return
    if args.action == "resume":
        state.set("resumes", state.get("resumes", []) + [paper.now()])
        print("Resumed: new paper trades may open again.")
    store = data.CandleStore(config.resolve(cfg["storage"]["market_db"]))
    if args.action == "update":
        client = data.DeltaCandleClient(m["api_base"])
        n = data.update(store, client, m["symbol"], "1h", m["history_days"])
        print(f"Fetched {n} new 1h candles.")
    splice = int(datetime.strptime(r["splice"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    candles = store.load(m["symbol"], "1h", start=splice)
    ctx = patterns.Context(candles, cfg["patterns"])
    print(f"Latest closed candle: {fmt(candles[-1].time)}")
    book = paper.replay(ctx, cfg, state.get("start_time"), state.get("resumes", []))
    old = state.stored_trades()
    out = config.resolve("reports/paper")
    out.mkdir(parents=True, exist_ok=True)
    for row in book.rows:
        path = out / f"trade_{datetime.fromtimestamp(row['entry_time'], timezone.utc):%Y%m%d}.png"
        prev = old.get(row["entry_time"])
        if row["status"] == "open" or not prev or prev["status"] == "open" or not path.exists():
            charts.trend_snapshot(ctx, row, path)
        row["snapshot"] = str(path)
    changes = paper.events(old, book.rows)
    was_paused = state.get("paused_since")
    if book.paused_since and book.paused_since != was_paused:
        changes.append(f"PAUSED: drawdown reached -{cfg['paper']['pause_drawdown_r']} R")
    state.set("paused_since", book.paused_since)
    state.save_trades([{k: v for k, v in row.items() if not k.startswith("_")} for row in book.rows])
    for c in changes:
        print(">> " + c)
    paper_status(book, cfg, state)
    if args.notify:
        notify(changes)


def cmd_markets(args, cfg):
    from trader import markets
    names = list(markets.MARKETS) if args.market == ["all"] else args.market
    cache = config.resolve("data/markets")
    out = config.resolve("reports/markets")
    out.mkdir(parents=True, exist_ok=True)
    e = cfg["edges"]["trend"]
    results = {}
    for name in names:
        d = markets.load(name, cache, refresh=args.refresh)
        trades = markets.trend_trades(d, e, markets.fees_for(name))
        ev = markets.evaluate(trades)
        results[name] = trades
        print(f"\n=== {name} ({markets.MARKETS[name][1]}, {markets.MARKETS[name][2]}) ===  "
              f"{len(d['c'])} daily candles, {fmt(d['t'][0])[:10]} -> {fmt(d['t'][-1])[:10]}")
        print(f"  {'':12s}{'trades':>7s}{'win':>5s}{'loss':>5s}{'net R':>8s}{'R/trade':>9s}")
        row = lambda label, t: print(f"  {label:12s}{t['total']:7d}{t[scoring.CORRECT]:5d}"
                                     f"{t[scoring.WRONG]:5d}{t['net_total']:+8.1f}{t['net_avg']:+9.2f}")
        for y, t in ev["by_year"].items():
            row(f"{y}{' (train)' if y in markets.TRAIN_YEARS else ''}", t)
        print("  " + "-" * 46)
        if ev["early"]["total"]:
            row("pre-2019", ev["early"])
        row("TRAIN 19-23", ev["train"])
        row("CHECK 24-25", ev["check"])
        row("SEEN 2026", ev["seen"])
        st = ev["stats"]
        if st:
            top = f"{st['top5_share']:.0%}" if st["top5_share"] is not None else "-"
            print(f"  all: {st['trades']} trades, {st['net_total']:+.1f} R, {st['net_avg']:+.2f} R/trade, "
                  f"t-stat {st['t_stat']:.1f}, win rate {st['win_rate']:.0%}, max drawdown {st['max_dd']:.1f} R, "
                  f"top-5 trades = {top} of profit")
        for tr in ev["open"]:
            print(f"  open {tr.direction} since {fmt(tr.entry_time)[:10]} @ {tr.entry:,.2f} ({tr.net_r:+.2f} R so far)")
        print(f"  *** VERDICT: {'PASS' if ev['pass'] else 'FAIL'} - {ev['why']}")
        with open(out / f"{name}.csv", "w", newline="") as f:
            import csv
            w = csv.writer(f)
            w.writerow(["entry_date", "direction", "entry", "stop", "exit_date", "exit_price",
                        "exit_reason", "result", "gross_r", "net_r", "days"])
            for tr in trades:
                w.writerow([fmt(tr.entry_time)[:10], tr.direction, tr.entry, round(tr.stop, 6),
                            fmt(int(d["t"][tr.exit_index]))[:10], tr.exit_price, tr.exit_reason,
                            tr.result, round(tr.gross_r, 3), round(tr.net_r, 3), round(tr.hours / 24, 1)])
    if len(results) > 1:
        pf = markets.portfolio(results)
        print(f"\n=== PORTFOLIO ({', '.join(results)}; 1R per trade, by exit year) ===")
        for y, r in sorted(pf["by_year"].items()):
            print(f"  {y}: {r:+.1f} R")
        print(f"  all: {pf['trades']} trades, {pf['net_total']:+.1f} R, {pf['net_avg']:+.2f} R/trade, "
              f"max drawdown {pf['max_dd']:.1f} R")
    print(f"\nTrade lists written to {out}")


def main():
    parser = argparse.ArgumentParser(prog="trader")
    parser.add_argument("--resolution", help="candle size to use instead of config.toml (e.g. 15m, 1h)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("data", help="download or inspect market candles")
    p.add_argument("action", choices=["update", "status", "research-history"])
    p = sub.add_parser("patterns", help="scan stored candles for chart patterns")
    p.add_argument("action", choices=["scan"])
    p.add_argument("--snapshots", type=int, default=0, help="chart images per pattern")
    p = sub.add_parser("predict", help="score predictions and report accuracy")
    p.add_argument("action", choices=["backtest", "report"])
    p.add_argument("--group", choices=sorted(patterns.GROUPS) + ["candidate"], default="levels",
                   help="chart = the 11 chart patterns, levels = the levels strategy")
    p.add_argument("--exit", choices=["E1", "E2", "E3"], default=None,
                   help="exit version from [exits] in config.toml")
    p.add_argument("--period", choices=["train", "test", "all"], default="train",
                   help="train = up to [testing] train_end, test = after it")
    p.add_argument("--snapshots", type=int, default=0,
                   help="chart images per pattern for correct and for wrong predictions")
    p = sub.add_parser("edges", help="backtest the new edge ideas (trend, overshoot)")
    p.add_argument("action", choices=["backtest"])
    p = sub.add_parser("markets", help="test the trend rules on other markets (daily candles)")
    p.add_argument("action", choices=["backtest"])
    p.add_argument("--market", nargs="+", default=["all"], help="names from trader/markets.py, or all")
    p.add_argument("--refresh", action="store_true", help="re-download daily candles")
    p = sub.add_parser("paper", help="paper-trade the trend-following edge on live candles")
    p.add_argument("action", choices=["start", "update", "status", "resume"])
    p.add_argument("--notify", action="store_true", help="macOS notification on new/closed trades")
    args = parser.parse_args()
    cfg = config.load()
    if getattr(args, "exit", "x") is None:
        args.exit = (cfg["candidate"]["exit"] if getattr(args, "group", None) == "candidate"
                     else cfg["exits"]["default"])
    if getattr(args, "exit", None):
        ex = cfg["exits"][args.exit]
        cfg["prediction"]["horizon_candles"] = ex["horizon"]
        cfg["prediction"]["target_mode"] = ex["target"]
        cfg["prediction"]["max_reward_risk"] = ex.get("max_reward_risk", cfg["prediction"]["reward_risk"])
    if args.resolution:
        if args.resolution not in data.RESOLUTION_SECONDS:
            parser.error(f"unknown resolution {args.resolution}")
        cfg["market"]["resolution"] = args.resolution
    if args.command == "data":
        cmd_data(args, cfg)
    elif args.command == "patterns":
        cmd_patterns(args, cfg)
    elif args.command == "predict":
        cmd_predict(args, cfg)
    elif args.command == "edges":
        cmd_edges(args, cfg)
    elif args.command == "markets":
        cmd_markets(args, cfg)
    elif args.command == "paper":
        cmd_paper(args, cfg)


if __name__ == "__main__":
    main()
