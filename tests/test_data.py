from urllib.parse import parse_qs, urlparse

from trader import data

STEP = 300


def fake_api(available_times, calls):
    """Mimics Delta: newest first, at most 4000 candles per response."""
    def get_json(url):
        q = {k: int(v[0]) if k in ("start", "end") else v[0]
             for k, v in parse_qs(urlparse(url).query).items()}
        calls.append(q)
        ts = [t for t in available_times if q["start"] <= t <= q["end"]]
        ts = sorted(ts, reverse=True)[:data.MAX_CANDLES_PER_REQUEST]
        return {"success": True, "result": [
            {"time": t, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10} for t in ts]}
    return get_json


def test_fetch_pages_past_cap_without_duplicates():
    times = list(range(0, STEP * 10000, STEP))
    calls = []
    client = data.DeltaCandleClient("https://x", fake_api(times, calls))
    got = client.fetch("BTCUSD", "5m", 0, times[-1])
    assert [c.time for c in got] == times
    assert len(calls) == 3


def test_update_is_incremental_and_skips_forming_candle(tmp_path):
    now = STEP * 1000 + 120  # mid-way through candle starting at 1000*STEP
    times = list(range(0, STEP * 1001, STEP))
    store = data.CandleStore(tmp_path / "m.sqlite3")
    calls = []
    client = data.DeltaCandleClient("https://x", fake_api(times, calls))

    n = data.update(store, client, "BTCUSD", "5m", history_days=1, now=now)
    count, first, last = store.summary("BTCUSD", "5m")
    assert last == STEP * 999  # forming candle at 1000*STEP excluded
    assert n == count == 288   # one day of 5m candles

    calls.clear()
    assert data.update(store, client, "BTCUSD", "5m", history_days=1, now=now) == 0
    assert calls == []
    assert data.update(store, client, "BTCUSD", "5m", history_days=1, now=now + STEP) == 1


def test_gaps_reported(tmp_path):
    store = data.CandleStore(tmp_path / "m.sqlite3")
    c = lambda t: data.Candle(t, 1, 1, 1, 1, 1)
    store.insert("BTCUSD", "5m", [c(0), c(STEP), c(STEP * 4)])
    assert store.gaps("BTCUSD", "5m") == [(STEP, 2)]


def test_update_backfills_older_history(tmp_path):
    now = STEP * 1000 + 120
    times = list(range(0, STEP * 1001, STEP))
    store = data.CandleStore(tmp_path / "m.sqlite3")
    client = data.DeltaCandleClient("https://x", fake_api(times, []))
    data.update(store, client, "BTCUSD", "5m", history_days=1, now=now)
    n = data.update(store, client, "BTCUSD", "5m", history_days=2, now=now)
    count, first, last = store.summary("BTCUSD", "5m")
    assert n == 288 and count == 576 and last == STEP * 999
    assert store.gaps("BTCUSD", "5m") == []
