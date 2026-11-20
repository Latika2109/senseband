"""Local SQLite storage + outbox for cloud upload."""
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS vitals(ts REAL, hr_ecg REAL, hr_ppg REAL, spo2 REAL, temp REAL,
                                  irregularity REAL, activity REAL);
CREATE TABLE IF NOT EXISTS alerts(ts REAL, level TEXT, kind TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS rom_sessions(ts REAL, joint TEXT, min_angle REAL, max_angle REAL,
                                        rom REAL, reps INTEGER, peak_speed REAL);
CREATE TABLE IF NOT EXISTS grip_tests(ts REAL, hand TEXT, peak REAL, mean REAL, unit TEXT);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
                                  payload TEXT, sent INTEGER DEFAULT 0);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def _exec(self, sql, args=()):
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    def add(self, table, row, upload=True):
        cols = ",".join(row)
        qs = ",".join("?" * len(row))
        self._exec(f"INSERT INTO {table}({cols}) VALUES({qs})", tuple(row.values()))
        if upload:
            self._exec("INSERT INTO outbox(ts,kind,payload) VALUES(?,?,?)",
                       (time.time(), table, json.dumps(row)))

    def query(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def pending(self, limit=200):
        return self.query("SELECT id,kind,payload FROM outbox WHERE sent=0 ORDER BY id LIMIT ?",
                          (limit,))

    def mark_sent(self, ids):
        if ids:
            self._exec(f"UPDATE outbox SET sent=1 WHERE id IN ({','.join('?' * len(ids))})",
                       tuple(ids))
