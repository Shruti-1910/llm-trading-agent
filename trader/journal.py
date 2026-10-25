"""Prediction journal (first version of Module 8): every prediction and its score."""
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    mode              TEXT    NOT NULL,   -- backtest | paper | live
    stream            TEXT    NOT NULL,   -- main (kept patterns) | shadow | all (evaluation)
    symbol            TEXT    NOT NULL,
    resolution        TEXT    NOT NULL,   -- candle size, e.g. 5m
    time              INTEGER NOT NULL,   -- open time of the confirmation candle (unix UTC)
    patterns          TEXT    NOT NULL,   -- JSON list; all patterns credited
    direction         TEXT    NOT NULL,   -- UP | DOWN
    entry             REAL    NOT NULL,
    stop              REAL    NOT NULL,
    target            REAL    NOT NULL,
    edge_reasons      TEXT    NOT NULL,   -- JSON list
    snapshot          TEXT,               -- PNG path, if one was made
    result            TEXT    NOT NULL,   -- CORRECT | WRONG | UNSURE | PENDING
    candles_to_result INTEGER,
    mfe_r             REAL,
    mae_r             REAL,
    final_r           REAL,
    UNIQUE (mode, stream, symbol, resolution, time)
)
"""

COLUMNS = ["id", "mode", "stream", "symbol", "resolution", "time", "patterns", "direction", "entry", "stop", "target",
           "edge_reasons", "snapshot", "result", "candles_to_result", "mfe_r", "mae_r", "final_r"]


class Journal:
    """One journal object reads and writes one candle size (resolution)."""

    def __init__(self, path, resolution):
        self.resolution = resolution
        self.db = sqlite3.connect(str(path))
        self.db.execute(SCHEMA)

    def clear(self, mode, symbol):
        self.db.execute("DELETE FROM predictions WHERE mode=? AND symbol=? AND resolution=?",
                        (mode, symbol, self.resolution))
        self.db.commit()

    def add(self, mode, stream, symbol, signal, score, snapshot=None):
        self.db.execute(
            "INSERT OR REPLACE INTO predictions (mode, stream, symbol, resolution, time, patterns, direction, entry, "
            "stop, target, edge_reasons, snapshot, result, candles_to_result, mfe_r, mae_r, "
            "final_r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mode, stream, symbol, self.resolution, signal.time, json.dumps(signal.patterns), signal.direction,
             signal.entry, signal.stop, signal.target, json.dumps(signal.edge_reasons),
             snapshot, score.result, score.candles_to_result, score.mfe_r, score.mae_r,
             score.final_r))

    def commit(self):
        self.db.commit()

    def rows(self, mode, stream, symbol, result=None):
        sql = "SELECT * FROM predictions WHERE mode=? AND stream=? AND symbol=? AND resolution=?"
        args = [mode, stream, symbol, self.resolution]
        if result:
            sql += " AND result=?"
            args.append(result)
        out = []
        for r in self.db.execute(sql + " ORDER BY time", args):
            d = dict(zip(COLUMNS, r))
            d["patterns"] = json.loads(d["patterns"])
            d["edge_reasons"] = json.loads(d["edge_reasons"])
            out.append(d)
        return out

    def update_score(self, row_id, score):
        self.db.execute(
            "UPDATE predictions SET result=?, candles_to_result=?, mfe_r=?, mae_r=?, final_r=? "
            "WHERE id=?",
            (score.result, score.candles_to_result, score.mfe_r, score.mae_r, score.final_r,
             row_id))
