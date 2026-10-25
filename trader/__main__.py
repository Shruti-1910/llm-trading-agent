"""Command line entry point: python -m trader <command>."""
import argparse
import collections
import random
from datetime import datetime, timezone

from trader import charts, config, data, journal, patterns, scoring


def fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "-"


def cmd_data(args, cfg):
    m = cfg["market"]
    db_path = config.resolve(cfg["storage"]["market_db"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = data.CandleStore(db_path)
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


def main():
    parser = argparse.ArgumentParser(prog="trader")
    parser.add_argument("--resolution", help="candle size to use instead of config.toml (e.g. 15m, 1h)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("data", help="download or inspect market candles")
    p.add_argument("action", choices=["update", "status"])
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


if __name__ == "__main__":
    main()
