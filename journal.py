"""SQLite journal for runs, decisions, orders, equity, and shared lessons."""
import json, sqlite3
from datetime import datetime, timezone
from typing import List, Optional

def now() -> str: return datetime.now(timezone.utc).isoformat()

class Journal:
    def __init__(self,path: str):
        self.db=sqlite3.connect(path); self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,model TEXT NOT NULL,started TEXT NOT NULL,ended TEXT,start_equity REAL NOT NULL,end_equity REAL,outcome TEXT NOT NULL DEFAULT 'active',benchmark_symbol TEXT,benchmark_start_price REAL);
        CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES runs(id),ts TEXT NOT NULL,prompt_hash TEXT NOT NULL,thesis TEXT,raw_output TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,decision_id INTEGER NOT NULL REFERENCES decisions(id),symbol TEXT NOT NULL,side TEXT NOT NULL,qty REAL NOT NULL,limit_price REAL NOT NULL,reason TEXT NOT NULL,guard_verdict TEXT NOT NULL,fill_price REAL,fill_ts TEXT,filled_qty REAL);
        CREATE TABLE IF NOT EXISTS equity(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES runs(id),ts TEXT NOT NULL,cash REAL NOT NULL,positions_value REAL NOT NULL,equity REAL NOT NULL,tier TEXT NOT NULL,benchmark_price REAL);
        CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY,ts TEXT NOT NULL,text TEXT NOT NULL,source_trade_ids TEXT NOT NULL DEFAULT '[]',score REAL NOT NULL DEFAULT 1.0,active INTEGER NOT NULL DEFAULT 1);'''); self.db.commit()
        self._migrate()
    def _migrate(self)->None:
        for table,column,definition in (("runs","benchmark_symbol","TEXT"),("runs","benchmark_start_price","REAL"),("orders","filled_qty","REAL"),("equity","benchmark_price","REAL")):
            columns={r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if column not in columns: self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.db.commit()
    def create_run(self,model: str,equity: float,benchmark_symbol: str="",benchmark_price: float=0.0)->int:
        c=self.db.execute("INSERT INTO runs(model,started,start_equity,benchmark_symbol,benchmark_start_price) VALUES(?,?,?,?,?)",(model,now(),equity,benchmark_symbol,benchmark_price)); self.db.commit(); return int(c.lastrowid)
    def active_run(self,model: str)->Optional[dict]:
        row=self.db.execute("SELECT * FROM runs WHERE model=? AND outcome='active' ORDER BY id DESC LIMIT 1",(model,)).fetchone()
        return dict(row) if row else None
    def latest_run(self)->Optional[dict]:
        row=self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    def day_start_equity(self,run_id: int,current_equity: float)->float:
        row=self.db.execute("SELECT equity FROM equity WHERE run_id=? AND date(ts)=date('now') ORDER BY id LIMIT 1",(run_id,)).fetchone()
        return float(row[0]) if row else current_equity
    def run_stats(self,run_id: int,current_equity: float,current_benchmark_price: float)->dict:
        row=self.db.execute("SELECT start_equity,started,benchmark_start_price FROM runs WHERE id=?",(run_id,)).fetchone()
        start_equity=float(row[0]); started=datetime.fromisoformat(row[1]); bench_start=float(row[2] or 0)
        weeks=max(0,int((datetime.now(timezone.utc)-started).total_seconds()//604800))
        return {"return_pct":(current_equity/start_equity-1)*100 if start_equity else 0.0,
                "buy_hold_return_pct":(current_benchmark_price/bench_start-1)*100 if bench_start>0 and current_benchmark_price>0 else None,
                "weeks":weeks}
    def close_run(self,run_id: int,equity: float,outcome: str)->None:
        self.db.execute("UPDATE runs SET ended=?,end_equity=?,outcome=? WHERE id=?",(now(),equity,outcome,run_id)); self.db.commit()
    def add_decision(self,run_id: int,digest: str,thesis: Optional[str],raw: str)->int:
        c=self.db.execute("INSERT INTO decisions(run_id,ts,prompt_hash,thesis,raw_output) VALUES(?,?,?,?,?)",(run_id,now(),digest,thesis,raw)); self.db.commit(); return int(c.lastrowid)
    def add_order(self,decision_id: int,order: dict,verdict: str,fill_price: Optional[float]=None,filled_qty: Optional[float]=None)->None:
        self.db.execute("INSERT INTO orders(decision_id,symbol,side,qty,limit_price,reason,guard_verdict,fill_price,fill_ts,filled_qty) VALUES(?,?,?,?,?,?,?,?,?,?)",(decision_id,order["symbol"],order["side"],order["qty"],order["limit_price"],order.get("reason",""),verdict,fill_price,now() if filled_qty else None,filled_qty)); self.db.commit()
    def record_equity(self,run_id: int,cash: float,positions_value: float,equity: float,tier: str,benchmark_price: Optional[float]=None)->None:
        self.db.execute("INSERT INTO equity(run_id,ts,cash,positions_value,equity,tier,benchmark_price) VALUES(?,?,?,?,?,?,?)",(run_id,now(),cash,positions_value,equity,tier,benchmark_price)); self.db.commit()
    def recent(self,run_id: int,limit: int=10)->list[dict]:
        return [dict(r) for r in self.db.execute("SELECT d.ts,d.thesis,o.symbol,o.side,o.qty,o.filled_qty,o.guard_verdict,o.fill_price FROM decisions d LEFT JOIN orders o ON o.decision_id=d.id WHERE d.run_id=? ORDER BY d.id DESC LIMIT ?",(run_id,limit))]
    def lessons(self,limit: int=10)->list[str]:
        return [r[0] for r in self.db.execute("SELECT text FROM lessons WHERE active=1 ORDER BY score DESC,id DESC LIMIT ?",(limit,))]
    def add_lesson(self,text: str,source_trade_ids: Optional[List[int]]=None,score: float=1.0)->None:
        if text.strip(): self.db.execute("INSERT INTO lessons(ts,text,source_trade_ids,score) VALUES(?,?,?,?)",(now(),text.strip(),json.dumps(source_trade_ids or []),score)); self.db.commit()
    def close(self)->None: self.db.close()
