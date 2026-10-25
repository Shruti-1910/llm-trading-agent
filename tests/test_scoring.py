from trader import scoring
from trader.journal import Journal
from trader.patterns import DOWN, UP, Signal
from trader.scoring import CORRECT, PENDING, UNSURE, WRONG, score

# UP trade: entry 100, stop 98 (risk 2), target 103 (1.5R)


def test_target_first_is_correct():
    s = score(UP, 100, 98, 103, highs=[101, 103.5], lows=[99, 100], closes=[100.5, 103], horizon=6)
    assert (s.result, s.candles_to_result, s.final_r) == (CORRECT, 2, 1.5)
    assert s.mfe_r == 1.75 and s.mae_r == 0.5


def test_stop_first_is_wrong():
    s = score(UP, 100, 98, 103, highs=[101, 100], lows=[99, 97.9], closes=[100, 98], horizon=6)
    assert (s.result, s.candles_to_result, s.final_r) == (WRONG, 2, -1.0)


def test_both_on_one_candle_is_wrong():
    s = score(UP, 100, 98, 103, highs=[104], lows=[97], closes=[100], horizon=6)
    assert s.result == WRONG


def test_neither_is_unsure_with_close_to_close_r():
    s = score(UP, 100, 98, 103, highs=[101] * 6, lows=[99] * 6, closes=[100.5] * 5 + [101],
              horizon=6)
    assert (s.result, s.final_r) == (UNSURE, 0.5)


def test_hit_after_horizon_is_ignored():
    s = score(UP, 100, 98, 103, highs=[101] * 6 + [110], lows=[99] * 7, closes=[100] * 7,
              horizon=6)
    assert s.result == UNSURE


def test_incomplete_window_is_pending():
    s = score(UP, 100, 98, 103, highs=[101, 101], lows=[99, 99], closes=[100, 100], horizon=6)
    assert s.result == PENDING


def test_down_trade_mirrors():
    # entry 100, stop 102, target 97
    assert score(DOWN, 100, 102, 97, [100.5], [96.9], [97], 6).result == CORRECT
    assert score(DOWN, 100, 102, 97, [102.1], [99], [101], 6).result == WRONG


def test_accuracy_excludes_unsure():
    t = scoring.tally([CORRECT, CORRECT, WRONG, UNSURE, UNSURE])
    assert t["accuracy"] == 2 / 3 and t["points"] == 2 and t["total"] == 5


def test_journal_roundtrip(tmp_path):
    jr = Journal(tmp_path / "j.sqlite3", "5m")
    sig = Signal(10, 3000, UP, ["bull_flag", "ascending_staircase"], 100, 98, 103, 2, 5, 0,
                 ["a", "b"], [])
    jr.add("backtest", "main", "BTCUSD", sig, score(UP, 100, 98, 103, [103.5], [99], [103], 6))
    jr.commit()
    [row] = jr.rows("backtest", "main", "BTCUSD")
    assert row["patterns"] == ["bull_flag", "ascending_staircase"] and row["result"] == CORRECT


def _rows(pattern, results, entry=100.0, stop=98.0):
    final = {CORRECT: 1.5, WRONG: -1.0, UNSURE: 0.0}
    return [{"patterns": [pattern], "result": r, "entry": entry, "stop": stop,
             "final_r": final[r]} for r in results]


def test_keep_list_rule():
    rows = (_rows("good", [CORRECT] * 16 + [WRONG] * 14)
            + _rows("weak", [CORRECT] * 10 + [WRONG] * 20)
            + _rows("rare", [CORRECT] * 10 + [WRONG] * 2 + [UNSURE] * 50))
    d = scoring.keep_list(rows, 0.00118, 30, 0.5)
    assert d["good"][0] == scoring.KEEP
    assert d["weak"][0] == scoring.SHADOW and d["rare"][0] == scoring.SHADOW


def test_keep_list_needs_positive_after_fees():
    # 15 C / 14 W / 30 Unsure with a tight stop: fees (0.118% of 100 / risk 0.2) = 0.59 R each
    rows = _rows("p", [CORRECT] * 15 + [WRONG] * 15 + [UNSURE] * 30, stop=99.8)
    assert scoring.keep_list(rows, 0.00118, 30, 0.5)["p"][0] == scoring.SHADOW
    assert scoring.keep_list(rows, 0.00118, 30, 0.5, require_net_positive=False)["p"][0] == scoring.KEEP
