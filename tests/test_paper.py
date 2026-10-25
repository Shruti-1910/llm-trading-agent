import numpy as np

from trader import config, edges, paper, scoring
from trader.edges import Trade
from tests.test_edges import ctx_from

CFG = config.load()


def trend_ctx():
    flat = [100 + (i % 2) for i in range(60 * 24)]
    up = list(np.linspace(102, 160, 15 * 24))
    down = list(np.linspace(160, 120, 10 * 24))
    return ctx_from(flat + up + down, wick=0.5)


def test_sizing_uses_10pct_cap_and_books_pnl():
    book = paper.replay(trend_ctx(), CFG, start_time=0, resumes=[])
    r = book.rows[0]
    assert r["status"] == "closed" and r["net_r"] > 0
    assert abs(r["notional"] - 100.0) < 1e-6                 # capped at 10% of $1,000
    assert r["risk_usd"] < 10.0                               # so less than 1% is at risk
    assert abs(book.equity - (1000 + r["pnl_usd"])) < 1e-9
    assert abs(r["pnl_usd"] - r["net_r"] * r["risk_usd"]) < 1e-9


def test_start_time_blocks_earlier_signals():
    ctx = trend_ctx()
    assert paper.replay(ctx, CFG, start_time=int(ctx.t[-1]) + 1, resumes=[]).rows == []


def test_open_trade_at_data_end():
    flat = [100 + (i % 2) for i in range(60 * 24)]
    ctx = ctx_from(flat + list(np.linspace(102, 130, 5 * 24)), wick=0.5)
    book = paper.replay(ctx, CFG, start_time=0, resumes=[])
    assert book.rows[-1]["status"] == "open" and book.rows[-1]["current_stop"] is not None


def fake_trades(net_rs, gap=100):
    """Replacement for edges.trend_trades: one -R trade every `gap` candles while allowed."""
    def run(ctx, cfg, fees, start_time=None, can_open=None):
        out, m = [], 0
        for net in net_rs:
            while can_open and not can_open(out, int(ctx.t[m])):
                m += gap
                if m >= len(ctx.t) - gap:
                    return out
            out.append(Trade("trend", "UP", m, int(ctx.t[m]), 100.0, 98.0, None, m + gap - 1,
                             100.0 + 2 * net, "stop", scoring.WRONG, net, net, gap - 1, 98.0))
            m += gap
        return out
    return run


def test_pause_at_minus_8R_and_resume(monkeypatch):
    ctx = ctx_from([100.0] * 3000)
    monkeypatch.setattr(edges, "trend_trades", fake_trades([-1.0] * 12))
    book = paper.replay(ctx, CFG, 0, [])
    assert len(book.rows) == 8 and book.paused_since == int(ctx.t[799])   # paused after -8 R
    resume = int(ctx.t[1000])
    book = paper.replay(ctx, CFG, 0, [resume])
    assert len(book.rows) == 12 and book.paused_since is None


def test_graduation_checks():
    book = paper.Book(rows=[{"status": "closed"}] * 12, equity=1100, net_r=3.0, max_drawdown_r=-4.0)
    assert paper.graduation(book, CFG)[0]
    book.max_drawdown_r = -8.0
    assert not paper.graduation(book, CFG)[0]
