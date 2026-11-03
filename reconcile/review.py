"""Human review layer. Decisions are events replayed over system results; system statuses never change."""
from __future__ import annotations

from dataclasses import dataclass, field

from reconcile.matcher import Outcome

ACTOR = "local-user"  # identity label only; this is not authentication
EVENT_TYPES = ("confirm_match", "mark_reviewed")


class ReviewError(ValueError):
    """Raised when a review event would break a constraint."""


@dataclass
class ReviewState:
    matches: dict[str, str] = field(default_factory=dict)  # bank_id -> ledger_id (automatic or confirmed)
    origin: dict[str, str] = field(default_factory=dict)  # bank_id -> "auto" | "manual"
    reserved_ledger: dict[str, str] = field(default_factory=dict)  # ledger_id -> bank_id
    reviewed: dict[tuple[str, str], dict] = field(default_factory=dict)  # (source, id) -> {note, event_id}
    rejected: list[str] = field(default_factory=list)  # stored events that failed replay (should be empty)


def build_state(outcome: Outcome, events: list[dict]) -> ReviewState:
    state = ReviewState()
    for bank_id, ledger_id, _rule in outcome.matches:
        state.matches[bank_id] = ledger_id
        state.origin[bank_id] = "auto"
        state.reserved_ledger[ledger_id] = bank_id
    for event in events:
        error = validate_event(state, outcome, event)
        if error:
            state.rejected.append(f"event {event.get('id')}: {error}")
            continue
        _apply(state, event)
    return state


def validate_event(state: ReviewState, outcome: Outcome, event: dict) -> str | None:
    """Return None when the event is allowed, otherwise a human-readable reason."""
    kind = event.get("event_type")
    if kind == "confirm_match":
        bank = outcome.results.get(("bank", event.get("bank_id") or ""))
        ledger = outcome.results.get(("ledger", event.get("ledger_id") or ""))
        if bank is None or ledger is None:
            return "both rows must be valid input rows"
        if bank.transaction_id in state.matches:
            return f"bank row {bank.transaction_id} is already matched"
        if ledger.transaction_id in state.reserved_ledger:
            return f"ledger row {ledger.transaction_id} is already matched to {state.reserved_ledger[ledger.transaction_id]}"
        listed = (ledger.transaction_id in {c.transaction_id for c in bank.candidates}
                  or bank.transaction_id in {c.transaction_id for c in ledger.candidates})
        if not listed:
            return "the pair is not a candidate of either row"
        if bank.amount_minor != ledger.amount_minor and not (event.get("note") or "").strip():
            return "the amounts differ, so a note explaining the difference is required"
        return None
    if kind == "mark_reviewed":
        if not (event.get("note") or "").strip():
            return "a review note is required"
        if (event.get("source"), event.get("transaction_id")) not in outcome.results:
            return "only valid input rows can be reviewed"
        return None
    return f"unknown event type {kind!r}"


def _apply(state: ReviewState, event: dict) -> None:
    if event["event_type"] == "confirm_match":
        state.matches[event["bank_id"]] = event["ledger_id"]
        state.origin[event["bank_id"]] = "manual"
        state.reserved_ledger[event["ledger_id"]] = event["bank_id"]
        if (event.get("note") or "").strip():  # keep the explanation with both rows
            for key in (("bank", event["bank_id"]), ("ledger", event["ledger_id"])):
                state.reviewed[key] = {"note": event["note"].strip(), "event_id": event.get("id")}
    else:
        state.reviewed[(event["source"], event["transaction_id"])] = {
            "note": event["note"].strip(),
            "event_id": event.get("id"),
        }


def assert_allowed(state: ReviewState, outcome: Outcome, event: dict) -> None:
    error = validate_event(state, outcome, event)
    if error:
        raise ReviewError(error)
