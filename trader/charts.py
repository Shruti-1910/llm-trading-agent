"""Chart snapshots of a signal: candles, pattern points, entry/stop/target."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


def snapshot(ctx, signal, path, after=8, before=10, horizon=6):
    s0 = max(0, signal.start - before)
    s1 = min(len(ctx.c) - 1, signal.index + max(after, horizon + 2))
    xs = range(s0, s1 + 1)
    fig, ax = plt.subplots(figsize=(10, 5))
    for x in xs:
        up = ctx.c[x] >= ctx.o[x]
        color = "#26a69a" if up else "#ef5350"
        ax.vlines(x, ctx.l[x], ctx.h[x], color=color, linewidth=0.8)
        ax.add_patch(plt.Rectangle((x - 0.35, min(ctx.o[x], ctx.c[x])), 0.7,
                                   max(abs(ctx.c[x] - ctx.o[x]), 1e-9), color=color))
    for setup in signal.setups:
        pts = setup.points
        for name in ("highs", "lows", "peaks", "pole", "upper", "lower", "neck"):
            seq = pts.get(name)
            if isinstance(seq, list) and seq and isinstance(seq[0], tuple):
                ax.plot([p[0] for p in seq], [p[1] for p in seq], "o-", color="#5c6bc0",
                        linewidth=1.2, markersize=4)
            elif isinstance(seq, float):
                ax.axhline(seq, color="#5c6bc0", linestyle=":", linewidth=1)
        if "rim" in pts:
            rims = pts["rim"] if isinstance(pts["rim"], tuple) else (pts["rim"],)
            for r in rims:
                ax.axhline(r, color="#5c6bc0", linestyle=":", linewidth=1)
        ax.axvline(setup.b, color="#999", linestyle="--", linewidth=0.8)
    i = signal.index
    ax.hlines([signal.entry], i, s1, colors="#333", linewidth=1, label="entry")
    ax.hlines([signal.stop], i, s1, colors="#ef5350", linewidth=1, label="stop")
    ax.hlines([signal.target], i, s1, colors="#26a69a", linewidth=1, label="target")
    ax.axvspan(i, i + horizon, color="#ffeb3b", alpha=0.15)
    when = datetime.fromtimestamp(signal.time, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ax.set_title(f"{' + '.join(signal.patterns)}  {signal.direction}  @ {when}")
    ax.legend(loc="best", fontsize=8)
    ax.set_xlim(s0 - 1, s1 + 1)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
