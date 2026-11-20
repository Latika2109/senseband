"""SQLite (WAL) store with migrations and an outbox. Safe to call from several threads."""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from .schema import MIGRATIONS, TABLES


def _now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.migrate()

    # ---- migrations ---------------------------------------------------------------------
    @property
    def version(self) -> int:
        return int(self.db.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        with self.lock:
            while self.version < len(MIGRATIONS):
                v = self.version
                self.db.execute("BEGIN")
                try:
                    for stmt in _split(MIGRATIONS[v]):
                        self.db.execute(stmt)
                    self.db.execute(f"PRAGMA user_version={v + 1}")
                    self.db.execute("COMMIT")
                except Exception:
                    self.db.execute("ROLLBACK")
                    raise
            return self.version

    # ---- generic helpers ----------------------------------------------------------------
    def query(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    @staticmethod
    def _encode(table: str, row: dict[str, Any]) -> dict[str, Any]:
        jcols = TABLES[table][1]
        return {k: (json.dumps(v) if k in jcols and not isinstance(v, str | type(None)) else v)
                for k, v in row.items()}

    @staticmethod
    def _decode(table: str, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        for k in TABLES[table][1]:
            if isinstance(row.get(k), str):
                try:
                    row[k] = json.loads(row[k])
                except ValueError:
                    pass
        return row

    def _row_id(self, table: str, row: dict[str, Any]) -> str:
        return ":".join(str(row[k]) for k in TABLES[table][0])

    def _outbox(self, table: str, row: dict[str, Any], op: str = "upsert") -> None:
        full = self._decode(table, dict(row))
        for k in [k for k, v in full.items() if isinstance(v, bytes | bytearray)]:
            full[f"{k}_b64"] = base64.b64encode(full.pop(k)).decode()   # blobs travel as base64
        self.db.execute(
            'INSERT INTO outbox("table", row_id, op, payload, created_ts) VALUES(?,?,?,?,?)',
            (table, self._row_id(table, row), op, json.dumps(full), _now_ms()))

    # ---- writes (every write is mirrored into the outbox for later sync) ----------------
    def upsert(self, table: str, row: dict[str, Any], outbox: bool = True) -> dict[str, Any]:
        """Insert or replace by primary key. Creates a UUID `id` when the table has one."""
        pk = TABLES[table][0]
        row = dict(row)
        if pk == ("id",) and not row.get("id"):
            row["id"] = str(uuid.uuid4())
        enc = self._encode(table, row)
        cols = ",".join(enc)
        qs = ",".join("?" * len(enc))
        upd = ",".join(f"{c}=excluded.{c}" for c in enc if c not in pk)
        sql = (f"INSERT INTO {table}({cols}) VALUES({qs}) "
               f"ON CONFLICT({','.join(pk)}) DO UPDATE SET {upd}")
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.execute(sql, tuple(enc.values()))
                if outbox:
                    stored = self.get_row(table, {k: row[k] for k in pk})
                    self._outbox(table, self._encode(table, stored or enc))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return row

    def update(self, table: str, row_id: str, fields: dict[str, Any]) -> None:
        """Merge fields into an existing id-keyed row (and re-queue it for sync)."""
        cur = self.get_row(table, {"id": row_id})
        if cur is None:
            raise KeyError(f"{table}/{row_id}")
        cur.update(fields)
        self.upsert(table, cur)

    def get_row(self, table: str, key: dict[str, Any]) -> dict[str, Any] | None:
        where = " AND ".join(f"{k}=?" for k in key)
        return self.one(f"SELECT * FROM {table} WHERE {where}", tuple(key.values()))

    def get(self, table: str, row_id: str) -> dict[str, Any] | None:
        return self._decode(table, self.get_row(table, {"id": row_id}))

    def list(self, table: str, where: str = "1=1", args: tuple = (), order: str = "",
             limit: int = 1000) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {table} WHERE {where}" + (f" ORDER BY {order}" if order else "")
        rows = self.query(sql + " LIMIT ?", (*args, limit))
        return [self._decode(table, r) for r in rows]  # type: ignore[misc]

    # ---- outbox -------------------------------------------------------------------------
    def pending(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.query(
            'SELECT id, "table", row_id, op, payload, created_ts FROM outbox '
            "WHERE sent_ts IS NULL ORDER BY id LIMIT ?", (limit,))

    def backlog(self) -> int:
        return int(self.one("SELECT COUNT(*) AS n FROM outbox WHERE sent_ts IS NULL")["n"])  # type: ignore[index]

    def mark_sent(self, ids: list[int]) -> None:
        if ids:
            with self.lock:
                self.db.execute(
                    f"UPDATE outbox SET sent_ts=? WHERE id IN ({','.join('?' * len(ids))})",
                    (_now_ms(), *ids))

    def bump_attempts(self, ids: list[int], error: str) -> dict[int, int]:
        """Record a failed delivery; returns the new attempt count per outbox id."""
        out: dict[int, int] = {}
        with self.lock:
            for i in ids:
                self.db.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                                (error[:300], i))
                row = self.db.execute("SELECT attempts FROM outbox WHERE id = ?", (i,)).fetchone()
                out[i] = int(row[0]) if row else 0
        return out

    def mark_dead(self, ids: list[int], error: str) -> None:
        """Give up on rows the cloud permanently rejects (kept for inspection, never re-sent)."""
        if ids:
            with self.lock:
                self.db.execute(
                    f"UPDATE outbox SET sent_ts = ?, last_error = ? WHERE id IN ({','.join('?' * len(ids))})",
                    (_now_ms(), "dead: " + error[:280], *ids))

    def dead_count(self) -> int:
        return int(self.one("SELECT COUNT(*) AS n FROM outbox WHERE last_error LIKE 'dead:%'")["n"])  # type: ignore[index]

    def state_get(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM sync_state WHERE key = ?", (key,))
        return r["value"] if r else default

    def state_set(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO sync_state(key, value) VALUES(?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.query(f"PRAGMA table_info({table})")}

    def close(self) -> None:
        with self.lock:
            self.db.close()


def _split(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]
