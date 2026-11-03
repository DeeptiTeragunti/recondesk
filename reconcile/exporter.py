"""CSV export. Text cells that start with a spreadsheet formula trigger are neutralised; amounts are not."""
from __future__ import annotations

import csv
import io

from reconcile.money import format_minor

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

RESULT_COLUMNS = [
    "run_id", "source", "row_number", "transaction_id", "date", "amount", "currency", "reference",
    "description", "system_status", "rule_id", "counterpart_ids", "date_delta_days", "amount_delta_minor",
    "evidence", "review_state", "review_partner_id", "review_note",
]
EVENT_COLUMNS = ["event_id", "run_id", "created_at", "actor", "event_type", "source", "transaction_id",
                 "bank_id", "ledger_id", "note"]


def neutralize(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _amount_cell(row) -> str:
    if row.amount_minor is not None:
        return format_minor(row.amount_minor)  # signed numeric text, never prefixed
    return neutralize(row.raw.get("amount"))  # invalid rows keep raw text, so it is treated as text


def results_csv(run_id: int, bank_rows, ledger_rows, outcome, state) -> bytes:
    invalid_results = {(r.source, r.row_number): r for r in outcome.invalid}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(RESULT_COLUMNS)
    all_rows = list(bank_rows) + list(ledger_rows)
    for row in sorted(all_rows, key=lambda r: (r.source, r.row_number)):
        if row.valid:
            result = outcome.results[(row.source, row.transaction_id)]
            candidates = result.candidates
            review = state.reviewed.get((row.source, row.transaction_id))
            manual_partner = _manual_partner(state, row)
            if manual_partner:
                review_state = "confirmed_match"
            else:
                review_state = "reviewed" if review else "open"
            values = [
                run_id, row.source, row.row_number, neutralize(row.transaction_id),
                row.date.isoformat(), _amount_cell(row), neutralize(row.currency),
                neutralize(row.reference), neutralize(row.description), result.status, result.rule_id,
                neutralize(";".join(c.transaction_id for c in candidates)),
                candidates[0].date_delta_days if len(candidates) == 1 else "",
                candidates[0].amount_delta_minor if len(candidates) == 1 else "",
                neutralize(result.evidence), review_state, neutralize(manual_partner or ""),
                neutralize(review["note"] if review else ""),
            ]
        else:
            result = invalid_results[(row.source, row.row_number)]
            values = [
                run_id, row.source, row.row_number, neutralize(row.transaction_id),
                neutralize(row.raw.get("date")), _amount_cell(row), neutralize(row.raw.get("currency")),
                neutralize(row.raw.get("reference")), neutralize(row.raw.get("description")),
                "invalid", result.rule_id, "", "", "", neutralize(result.evidence), "n/a", "", "",
            ]
        writer.writerow(values)
    return out.getvalue().encode("utf-8-sig")


def _manual_partner(state, row) -> str | None:
    if row.source == "bank":
        return state.matches.get(row.transaction_id) if state.origin.get(row.transaction_id) == "manual" else None
    bank_id = state.reserved_ledger.get(row.transaction_id)
    return bank_id if bank_id and state.origin.get(bank_id) == "manual" else None


def events_csv(events: list[dict]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(EVENT_COLUMNS)
    for ev in events:
        writer.writerow([
            ev["id"], ev["run_id"], ev["created_at"], neutralize(ev["actor"]), neutralize(ev["event_type"]),
            neutralize(ev["source"]), neutralize(ev["transaction_id"]), neutralize(ev["bank_id"]),
            neutralize(ev["ledger_id"]), neutralize(ev["note"]),
        ])
    return out.getvalue().encode("utf-8-sig")
