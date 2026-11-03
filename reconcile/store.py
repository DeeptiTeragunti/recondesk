"""SQLite persistence for runs, original input bytes, system results and review events."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists runs (
    id integer primary key autoincrement,
    created_at text not null,
    source_label text not null,
    bank_filename text not null,
    ledger_filename text not null,
    bank_sha256 text not null,
    ledger_sha256 text not null,
    bank_csv blob not null,
    ledger_csv blob not null,
    rules_version text not null,
    window_days integer not null,
    system_results_json text not null
);
create table if not exists review_events (
    id integer primary key autoincrement,
    run_id integer not null references runs(id),
    created_at text not null,
    actor text not null,
    event_type text not null,
    source text,
    transaction_id text,
    bank_id text,
    ledger_id text,
    note text
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _display_name(name: str) -> str:
    # Display text only. The name is never used as a filesystem path.
    return os.path.basename(name.replace("\\", "/"))[:200] or "unnamed.csv"


def create_run(conn, *, source_label, bank_filename, ledger_filename, bank_bytes, ledger_bytes,
               bank_sha256, ledger_sha256, rules_version, window_days, system_results) -> int:
    cursor = conn.execute(
        """insert into runs (created_at, source_label, bank_filename, ledger_filename, bank_sha256, ledger_sha256,
           bank_csv, ledger_csv, rules_version, window_days, system_results_json)
           values (?,?,?,?,?,?,?,?,?,?,?)""",
        (_now(), source_label, _display_name(bank_filename), _display_name(ledger_filename), bank_sha256,
         ledger_sha256, sqlite3.Binary(bank_bytes), sqlite3.Binary(ledger_bytes), rules_version, window_days,
         json.dumps(system_results, sort_keys=True)),
    )
    conn.commit()
    return cursor.lastrowid


def load_run(conn, run_id: int) -> dict:
    row = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"no run {run_id}")
    return dict(row)


def list_runs(conn) -> list[dict]:
    rows = conn.execute(
        "select id, created_at, source_label, bank_filename, ledger_filename, window_days, rules_version from runs order by id desc"
    ).fetchall()
    return [dict(r) for r in rows]


def add_event(conn, run_id: int, event: dict) -> int:
    cursor = conn.execute(
        """insert into review_events (run_id, created_at, actor, event_type, source, transaction_id, bank_id, ledger_id, note)
           values (?,?,?,?,?,?,?,?,?)""",
        (run_id, _now(), event["actor"], event["event_type"], event.get("source"), event.get("transaction_id"),
         event.get("bank_id"), event.get("ledger_id"), event.get("note")),
    )
    conn.commit()
    return cursor.lastrowid


def list_events(conn, run_id: int) -> list[dict]:
    rows = conn.execute("select * from review_events where run_id = ? order by id", (run_id,)).fetchall()
    return [dict(r) for r in rows]

