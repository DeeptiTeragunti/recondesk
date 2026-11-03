"""Orchestrates ingest, reconciliation, persistence and reopen with integrity checks."""
from __future__ import annotations

import json

from reconcile import store
from reconcile.ingest import IngestResult, ingest_csv
from reconcile.matcher import WINDOW_DEFAULT, Outcome, reconcile, summarize
from reconcile.review import ReviewState, assert_allowed, build_state


class RulesChanged(RuntimeError):
    """The stored run was reconciled under different rules; its snapshot no longer reproduces."""

    def __init__(self, stored_version: str, current_version: str):
        super().__init__(f"run was reconciled with rules {stored_version}; the current rules ({current_version}) "
                         "give different results. Re-run its stored inputs as a new run.")
        self.stored_version, self.current_version = stored_version, current_version


class InputRejected(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def ingest_pair(bank_bytes: bytes, ledger_bytes: bytes) -> tuple[IngestResult, IngestResult]:
    bank = ingest_csv("bank", bank_bytes)
    ledger = ingest_csv("ledger", ledger_bytes)
    errors = [f"bank file: {e}" for e in bank.file_errors] + [f"ledger file: {e}" for e in ledger.file_errors]
    if errors:
        raise InputRejected(errors)
    return bank, ledger


def _system_snapshot(outcome: Outcome) -> dict:
    return {
        "matches": [list(m) for m in outcome.matches],
        "statuses": {f"{s}:{t}": r.status for (s, t), r in sorted(outcome.results.items())},
        "proposals": [list(p) for p in outcome.proposals],
    }


def start_run(conn, *, source_label, bank_name, ledger_name, bank_bytes, ledger_bytes, window_days=WINDOW_DEFAULT):
    bank, ledger = ingest_pair(bank_bytes, ledger_bytes)
    outcome = reconcile(bank.rows, ledger.rows, window_days)
    run_id = store.create_run(
        conn, source_label=source_label, bank_filename=bank_name, ledger_filename=ledger_name,
        bank_bytes=bank_bytes, ledger_bytes=ledger_bytes, bank_sha256=bank.sha256, ledger_sha256=ledger.sha256,
        rules_version=outcome.rules_version, window_days=window_days, system_results=_system_snapshot(outcome),
    )
    return run_id


def open_run(conn, run_id: int) -> dict:
    """Recompute results from the stored bytes and verify them against the stored snapshot."""
    meta = store.load_run(conn, run_id)
    bank, ledger = ingest_pair(bytes(meta["bank_csv"]), bytes(meta["ledger_csv"]))
    if bank.sha256 != meta["bank_sha256"] or ledger.sha256 != meta["ledger_sha256"]:
        raise RuntimeError("stored input bytes do not match their recorded hashes")
    outcome = reconcile(bank.rows, ledger.rows, meta["window_days"])
    stored, current = json.loads(meta["system_results_json"]), _system_snapshot(outcome)
    if "proposals" not in stored:  # snapshots saved before proposals were recorded
        current.pop("proposals")
    if stored != current:
        if meta["rules_version"] != outcome.rules_version:
            raise RulesChanged(meta["rules_version"], outcome.rules_version)
        raise RuntimeError("recomputed system results differ from the stored snapshot under the same rules version")
    events = store.list_events(conn, run_id)
    state: ReviewState = build_state(outcome, events)
    return {
        "meta": meta,
        "bank": bank.rows,
        "ledger": ledger.rows,
        "outcome": outcome,
        "events": events,
        "state": state,
        "summary": summarize(bank.rows, ledger.rows, outcome),
    }


def rerun(conn, run_id: int) -> int:
    """Reconcile a saved run's stored inputs again under the current rules, as a NEW run without its decisions."""
    meta = store.load_run(conn, run_id)
    return start_run(conn, source_label=f"{meta['source_label']} (re-run of #{run_id})",
                     bank_name=meta["bank_filename"], ledger_name=meta["ledger_filename"],
                     bank_bytes=bytes(meta["bank_csv"]), ledger_bytes=bytes(meta["ledger_csv"]),
                     window_days=meta["window_days"])


def add_review_event(conn, run: dict, run_id: int, event: dict) -> int:
    event = {**event, "actor": "local-user"}
    # Re-read the stored events: the page's state may be stale (another tab may have saved a decision).
    current = build_state(run["outcome"], store.list_events(conn, run_id))
    assert_allowed(current, run["outcome"], event)
    return store.add_event(conn, run_id, event)
