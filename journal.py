"""SQLite journal for runs, decisions, orders, equity, and shared lessons."""
import json, sqlite3
from datetime import datetime, timezone
from typing import List, Optional

def now() -> str: return datetime.now(timezone.utc).isoformat()

class Journal:
    def __init__(self,path: str):
        self.db=sqlite3.connect(path); self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,model TEXT NOT NULL,started TEXT NOT NULL,ended TEXT,start_equity REAL NOT NULL,end_equity REAL,outcome TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES runs(id),ts TEXT NOT NULL,prompt_hash TEXT NOT NULL,thesis TEXT,raw_output TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,decision_id INTEGER NOT NULL REFERENCES decisions(id),symbol TEXT NOT NULL,side TEXT NOT NULL,qty REAL NOT NULL,limit_price REAL NOT NULL,reason TEXT NOT NULL,guard_verdict TEXT NOT NULL,fill_price REAL,fill_ts TEXT);
        CREATE TABLE IF NOT EXISTS equity(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES runs(id),ts TEXT NOT NULL,cash REAL NOT NULL,positions_value REAL NOT NULL,equity REAL NOT NULL,tier TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY,ts TEXT NOT NULL,text TEXT NOT NULL,source_trade_ids TEXT NOT NULL DEFAULT '[]',score REAL NOT NULL DEFAULT 1.0,active INTEGER NOT NULL DEFAULT 1);'''); self.db.commit()
    def create_run(self,model: str,equity: float)->int:
        c=self.db.execute("INSERT INTO runs(model,started,start_equity) VALUES(?,?,?)",(model,now(),equity)); self.db.commit(); return int(c.lastrowid)
    def active_run(self,model: str)->Optional[dict]:
        row=self.db.execute("SELECT id,start_equity FROM runs WHERE model=? AND outcome='active' ORDER BY id DESC LIMIT 1",(model,)).fetchone()
        return dict(row) if row else None
    def close_run(self,run_id: int,equity: float,outcome: str)->None:
        self.db.execute("UPDATE runs SET ended=?,end_equity=?,outcome=? WHERE id=?",(now(),equity,outcome,run_id)); self.db.commit()
    def add_decision(self,run_id: int,digest: str,thesis: Optional[str],raw: str)->int:
        c=self.db.execute("INSERT INTO decisions(run_id,ts,prompt_hash,thesis,raw_output) VALUES(?,?,?,?,?)",(run_id,now(),digest,thesis,raw)); self.db.commit(); return int(c.lastrowid)
    def add_order(self,decision_id: int,order: dict,verdict: str,fill_price: Optional[float]=None)->None:
        self.db.execute("INSERT INTO orders(decision_id,symbol,side,qty,limit_price,reason,guard_verdict,fill_price,fill_ts) VALUES(?,?,?,?,?,?,?,?,?)",(decision_id,order["symbol"],order["side"],order["qty"],order["limit_price"],order.get("reason",""),verdict,fill_price,now() if fill_price is not None else None)); self.db.commit()
    def record_equity(self,run_id: int,cash: float,positions_value: float,equity: float,tier: str)->None:
        self.db.execute("INSERT INTO equity(run_id,ts,cash,positions_value,equity,tier) VALUES(?,?,?,?,?,?)",(run_id,now(),cash,positions_value,equity,tier)); self.db.commit()
    def recent(self,run_id: int,limit: int=10)->list[dict]:
        return [dict(r) for r in self.db.execute("SELECT d.ts,d.thesis,o.symbol,o.side,o.qty,o.guard_verdict,o.fill_price FROM decisions d LEFT JOIN orders o ON o.decision_id=d.id WHERE d.run_id=? ORDER BY d.id DESC LIMIT ?",(run_id,limit))]
    def lessons(self,limit: int=10)->list[str]:
        return [r[0] for r in self.db.execute("SELECT text FROM lessons WHERE active=1 ORDER BY score DESC,id DESC LIMIT ?",(limit,))]
    def add_lesson(self,text: str,source_trade_ids: Optional[List[int]]=None,score: float=1.0)->None:
        if text.strip(): self.db.execute("INSERT INTO lessons(ts,text,source_trade_ids,score) VALUES(?,?,?,?)",(now(),text.strip(),json.dumps(source_trade_ids or []),score)); self.db.commit()
    def close(self)->None: self.db.close()
