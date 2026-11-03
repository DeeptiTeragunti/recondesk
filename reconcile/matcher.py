"""Deterministic reconciliation rules. Outputs are rule results, never calibrated probabilities."""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from reconcile.ingest import InputRow
from reconcile.money import format_minor

RULES_VERSION = "2026.11.2"
WINDOW_MIN, WINDOW_MAX, WINDOW_DEFAULT = 0, 3, 2
STATUSES = ("matched", "probable", "ambiguous", "unmatched")  # plus "invalid" for rows that failed validation

# Probable-match (near-miss) limits. A pair may miss on reference, amount or date, but only by this much.
PROBABLE_MAX_DAYS = 5
AMOUNT_TOLERANCE_MIN_MINOR = 200  # 2.00 EUR
AMOUNT_TOLERANCE_DIVISOR = 100  # or 1% of the bank amount, whichever is larger
REFERENCE_MAX_EDITS = 2  # a "typo" is at most this many character edits
REFERENCE_MIN_LENGTH_FOR_TYPO = 5

RULE_DESCRIPTIONS = {
    "R0_INVALID_ROW": "Failed input validation. Excluded from matching and totals.",
    "R1_STRONG_UNIQUE": "Equal normalised non-empty reference, equal signed amount, EUR, date within window, unique on both sides. Auto-matched.",
    "R2_STRONG_AMBIGUOUS": "Strong candidate exists but is not unique on both sides. Not auto-matched.",
    "R3_NO_CANDIDATE": "No strong, probable, reference-conflict or amount/date candidate found.",
    "R4_REF_AMOUNT_CONFLICT": "Equal normalised reference but different signed amount, with dates inside the window. Flagged for review; no cause is asserted.",
    "R5_AMOUNT_DATE_SUGGESTION": "Equal signed amount and date within window without a strong reference. Suggestion only; never auto-matched.",
    "R6_PROBABLE_MATCH": (
        "Near-miss, unique on both sides: reference equal, a typo of the other (up to 2 edits) or missing; amount "
        "equal or within max(2.00 EUR, 1%); date within 5 days; same sign. Rule score 3 = medium, 4 = high (not a "
        "probability). Proposed for one-click review; never auto-matched."
    ),
}


@dataclass
class Candidate:
    transaction_id: str  # counterpart on the other side
    rule_id: str
    date_delta_days: int  # ledger date minus bank date
    amount_delta_minor: int  # ledger amount minus bank amount
    score: int | None = None  # R6 rule score only


@dataclass
class RowResult:
    source: str
    row_number: int
    transaction_id: str
    status: str  # matched | probable | ambiguous | unmatched | invalid
    rule_id: str
    evidence: str
    amount_minor: int | None = None
    partner_id: str | None = None  # automatic match, or the proposed partner for a probable match
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Outcome:
    window_days: int
    rules_version: str
    matches: list[tuple[str, str, str]]  # (bank_id, ledger_id, rule_id), automatic only
    results: dict[tuple[str, str], RowResult]  # valid rows keyed by (source, transaction_id)
    invalid: list[RowResult]
    proposals: list[tuple[str, str, int]] = field(default_factory=list)  # (bank_id, ledger_id, score), never automatic


def confidence(score: int | None) -> str:
    return {4: "high", 3: "medium"}.get(score, "")


def reconcile(bank: list[InputRow], ledger: list[InputRow], window_days: int = WINDOW_DEFAULT) -> Outcome:
    if not WINDOW_MIN <= window_days <= WINDOW_MAX:
        raise ValueError(f"window_days must be between {WINDOW_MIN} and {WINDOW_MAX}")
    valid_bank = [r for r in bank if r.valid]
    valid_ledger = [r for r in ledger if r.valid]

    strong_from_bank, strong_from_ledger = _strong_edges(valid_bank, valid_ledger, window_days)
    matches = _unique_matches(strong_from_bank, strong_from_ledger)
    partner_of = {("bank", b): l for b, l, _ in matches} | {("ledger", l): b for b, l, _ in matches}
    matched_ids = {("bank", b) for b, _, _ in matches} | {("ledger", l) for _, l, _ in matches}

    # Rows with any strong edge are settled by R1/R2; only the rest can be probable matches.
    open_bank = [r for r in valid_bank if not strong_from_bank[r.transaction_id]]
    open_ledger = [r for r in valid_ledger if not strong_from_ledger[r.transaction_id]]
    probable_from_bank, probable_from_ledger = _probable_edges(open_bank, open_ledger, window_days)
    proposals = _unique_proposals(probable_from_bank, probable_from_ledger)
    proposed_of = {("bank", b): (l, s) for b, l, s in proposals} | {("ledger", l): (b, s) for b, l, s in proposals}

    results: dict[tuple[str, str], RowResult] = {}
    for source, rows, strong, probable, other_rows in (
        ("bank", valid_bank, strong_from_bank, probable_from_bank, valid_ledger),
        ("ledger", valid_ledger, strong_from_ledger, probable_from_ledger, valid_bank),
    ):
        other_side = "ledger" if source == "bank" else "bank"
        taken_other = {tid for side, tid in matched_ids if side == other_side}
        taken_other |= {tid for (side, _), (tid, _) in proposed_of.items() if side == source}
        index = _index(other_rows)
        for row in rows:
            key = (source, row.transaction_id)
            results[key] = _row_result(
                row, source, strong[row.transaction_id], partner_of.get(key), proposed_of.get(key),
                probable.get(row.transaction_id, []), index, taken_other, window_days,
            )

    invalid = [_invalid_result(r) for r in bank + ledger if not r.valid]
    return Outcome(window_days, RULES_VERSION, sorted(matches), results,
                   sorted(invalid, key=lambda r: (r.source, r.row_number)), sorted(proposals))


def _strong_edges(bank, ledger, window):
    ledger_by_key = defaultdict(list)
    for row in ledger:
        if row.reference_norm:
            ledger_by_key[(row.reference_norm, row.amount_minor)].append(row)
    from_bank = {row.transaction_id: [] for row in bank}
    from_ledger = {row.transaction_id: [] for row in ledger}
    for b in bank:
        if not b.reference_norm:  # an empty reference can never produce a strong edge
            continue
        for l in ledger_by_key.get((b.reference_norm, b.amount_minor), ()):
            if abs((l.date - b.date).days) <= window:
                from_bank[b.transaction_id].append(l)
                from_ledger[l.transaction_id].append(b)
    return from_bank, from_ledger


def _unique_matches(from_bank, from_ledger):
    # Pure set logic on the full candidate graph: no input order is consulted.
    matches = []
    for bank_id, candidates in from_bank.items():
        if len(candidates) == 1 and len(from_ledger[candidates[0].transaction_id]) == 1:
            matches.append((bank_id, candidates[0].transaction_id, "R1_STRONG_UNIQUE"))
    return matches


def _within_edits(a: str, b: str, limit: int = REFERENCE_MAX_EDITS) -> bool:
    """Bounded Levenshtein distance check: True when a and b differ by at most `limit` edits."""
    if abs(len(a) - len(b)) > limit or min(len(a), len(b)) < REFERENCE_MIN_LENGTH_FOR_TYPO:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def probable_score(bank: InputRow, ledger: InputRow, window: int) -> int | None:
    """Rule score for a near-miss pair, or None when it is too far off on any criterion.

    reference: equal 2, typo 1, missing on either side 0, different -> None
    amount:    equal 2, within tolerance 1, otherwise or opposite sign -> None
    date:      within window 1, within PROBABLE_MAX_DAYS 0, otherwise -> None
    A pair needs at least 3 points; 5 points would be a strong match and is handled by R1/R2.
    """
    days = abs((ledger.date - bank.date).days)
    if days > PROBABLE_MAX_DAYS or bank.amount_minor * ledger.amount_minor < 0:
        return None
    difference = abs(ledger.amount_minor - bank.amount_minor)
    tolerance = max(AMOUNT_TOLERANCE_MIN_MINOR, abs(bank.amount_minor) // AMOUNT_TOLERANCE_DIVISOR)
    if difference > tolerance:
        return None
    a, b = bank.reference_norm, ledger.reference_norm
    if a and b:
        if a == b:
            ref_points = 2
        elif _within_edits(a, b):
            ref_points = 1
        else:
            return None
    else:
        ref_points = 0
    score = ref_points + (2 if difference == 0 else 1) + (1 if days <= window else 0)
    return min(score, 4) if score >= 3 else None


def _probable_edges(bank, ledger, window):
    by_amount = sorted(ledger, key=lambda r: r.amount_minor)
    amounts = [r.amount_minor for r in by_amount]
    from_bank, from_ledger = defaultdict(list), defaultdict(list)
    for b in bank:
        tolerance = max(AMOUNT_TOLERANCE_MIN_MINOR, abs(b.amount_minor) // AMOUNT_TOLERANCE_DIVISOR)
        lo = bisect.bisect_left(amounts, b.amount_minor - tolerance)
        hi = bisect.bisect_right(amounts, b.amount_minor + tolerance)
        for l in by_amount[lo:hi]:
            score = probable_score(b, l, window)
            if score is not None:
                from_bank[b.transaction_id].append((l, score))
                from_ledger[l.transaction_id].append((b, score))
    return from_bank, from_ledger


def _unique_proposals(from_bank, from_ledger):
    # Same discipline as automatic matches: propose only when the pair is the sole near-miss on both sides.
    proposals = []
    for bank_id, edges in from_bank.items():
        if len(edges) == 1:
            ledger_row, score = edges[0]
            if len(from_ledger[ledger_row.transaction_id]) == 1:
                proposals.append((bank_id, ledger_row.transaction_id, score))
    return proposals


def _index(rows):
    by_amount, by_ref = defaultdict(list), defaultdict(list)
    for row in rows:
        by_amount[row.amount_minor].append(row)
        if row.reference_norm:
            by_ref[row.reference_norm].append(row)
    return by_amount, by_ref


def _orient(row, other, source):
    """Return (bank, ledger) so date and amount deltas always mean ledger minus bank."""
    return (row, other) if source == "bank" else (other, row)


def _candidate(row, other, source, rule_id, score=None) -> Candidate:
    bank, ledger = _orient(row, other, source)
    return Candidate(
        transaction_id=other.transaction_id,
        rule_id=rule_id,
        date_delta_days=(ledger.date - bank.date).days,
        amount_delta_minor=ledger.amount_minor - bank.amount_minor,
        score=score,
    )


def _row_result(row, source, strong, partner_id, proposed, probable, index, taken_other, window) -> RowResult:
    by_amount, by_ref = index
    tid = row.transaction_id
    ref = row.reference_norm
    if partner_id is not None:
        partner = next(o for o in strong if o.transaction_id == partner_id)
        candidate = _candidate(row, partner, source, "R1_STRONG_UNIQUE")
        return RowResult(source, row.row_number, tid, "matched", "R1_STRONG_UNIQUE",
                         _evidence_strong_unique(row, candidate, window), row.amount_minor, partner_id, [candidate])
    if strong:
        candidates = [_candidate(row, o, source, "R2_STRONG_AMBIGUOUS")
                      for o in sorted(strong, key=lambda r: r.transaction_id)]
        ids = ", ".join(c.transaction_id for c in candidates)
        evidence = (f"{len(candidates)} strong candidate(s) ({ids}); no unique pairing exists on both sides, "
                    "so no automatic match.")
        return RowResult(source, row.row_number, tid, "ambiguous", "R2_STRONG_AMBIGUOUS", evidence,
                         row.amount_minor, None, candidates)
    if proposed is not None:
        other_id, score = proposed
        other = next(o for o, _ in probable if o.transaction_id == other_id)
        candidate = _candidate(row, other, source, "R6_PROBABLE_MATCH", score)
        return RowResult(source, row.row_number, tid, "probable", "R6_PROBABLE_MATCH",
                         _evidence_probable(row, other, source, candidate, window), row.amount_minor, other_id,
                         [candidate])

    def in_window(o):
        return abs(_candidate(row, o, source, "").date_delta_days) <= window

    conflicts = sorted((o for o in by_ref.get(ref, ()) if o.amount_minor != row.amount_minor and in_window(o)),
                       key=lambda r: r.transaction_id) if ref else []
    suggestions = [
        o for o in by_amount.get(row.amount_minor, ())
        if o.transaction_id not in taken_other and not (ref and o.reference_norm == ref) and in_window(o)
    ]
    candidates = [_candidate(row, o, source, "R4_REF_AMOUNT_CONFLICT") for o in conflicts]
    near_misses = [(o, s) for o, s in probable if o.transaction_id not in {c.transaction_id for c in candidates}]
    candidates += [_candidate(row, o, source, "R6_PROBABLE_MATCH", s) for o, s in sorted(
        near_misses, key=lambda e: (-e[1], e[0].transaction_id))]
    seen = {c.transaction_id for c in candidates}
    candidates += sorted(
        (_candidate(row, o, source, "R5_AMOUNT_DATE_SUGGESTION") for o in suggestions if o.transaction_id not in seen),
        key=lambda c: (abs(c.date_delta_days), c.transaction_id),
    )
    if conflicts:
        rule = "R4_REF_AMOUNT_CONFLICT"
        evidence = (f"Reference {row.reference_norm} is shared with {len(conflicts)} row(s) of a different amount "
                    f"within ±{window} day(s). Flagged for review; the cause is not asserted.")
    elif suggestions:
        rule = "R5_AMOUNT_DATE_SUGGESTION"
        reference_state = "is empty" if not ref else "differs"
        evidence = (f"Amount {format_minor(row.amount_minor)} is equal and {len(suggestions)} row(s) fall within the "
                    f"date window; the reference {reference_state}. Suggestion only.")
    else:
        rule = "R3_NO_CANDIDATE"
        evidence = "No candidate found on the other side."
    if near_misses:
        evidence += f" {len(near_misses)} near-miss candidate(s), none unique, so nothing is proposed."
    return RowResult(source, row.row_number, tid, "unmatched", rule, evidence, row.amount_minor, None, candidates)


def _evidence_strong_unique(row, candidate, window) -> str:
    return (f"Reference {row.reference_norm} equal after normalisation; amount {format_minor(row.amount_minor)} equal; "
            f"EUR; date delta {candidate.date_delta_days:+d} day(s) within the ±{window} day window; "
            "unique on both sides.")


def _evidence_probable(row, other, source, candidate, window) -> str:
    bank, ledger = _orient(row, other, source)
    parts = []
    if bank.reference_norm and ledger.reference_norm:
        parts.append("reference equal" if bank.reference_norm == ledger.reference_norm
                     else f"reference differs slightly ({bank.reference_norm} vs {ledger.reference_norm})")
    else:
        parts.append("reference missing on " + ("both sides" if not (bank.reference_norm or ledger.reference_norm)
                                                 else ("the bank side" if not bank.reference_norm else "the ledger side")))
    parts.append("amount equal" if candidate.amount_delta_minor == 0
                 else f"amount differs by {format_minor(candidate.amount_delta_minor)} (ledger minus bank)")
    days = candidate.date_delta_days
    parts.append(f"date delta {days:+d} day(s)" + ("" if abs(days) <= window else f", outside the ±{window} day window"))
    return (f"Probable match, {confidence(candidate.score)} confidence (rule score {candidate.score}/4): "
            + "; ".join(parts) + ". Unique on both sides. Confirm or reject; it is not matched automatically.")


def _invalid_result(row: InputRow) -> RowResult:
    return RowResult(row.source, row.row_number, row.transaction_id, "invalid", "R0_INVALID_ROW",
                     "; ".join(row.errors))


def summarize(bank: list[InputRow], ledger: list[InputRow], outcome: Outcome) -> dict:
    """Totals over every valid row, including unmatched rows, plus an accounting check."""
    sides = {}
    for source, rows in (("bank", bank), ("ledger", ledger)):
        valid = [r for r in rows if r.valid]
        total = sum(r.amount_minor for r in valid)
        statuses = Counter(outcome.results[(source, r.transaction_id)].status for r in valid)
        status_totals = {s: 0 for s in STATUSES}
        for r in valid:
            status_totals[outcome.results[(source, r.transaction_id)].status] += r.amount_minor
        sides[source] = {
            "rows": len(rows),
            "valid": len(valid),
            "invalid": len(rows) - len(valid),
            "total_minor": total,
            "status_counts": {s: statuses.get(s, 0) for s in STATUSES},
            "status_totals_minor": status_totals,
            "accounted": sum(status_totals.values()) == total,
            "coverage": (statuses.get("matched", 0) / len(valid)) if valid else None,
        }
    difference = sides["bank"]["total_minor"] - sides["ledger"]["total_minor"]
    return {
        "bank": sides["bank"],
        "ledger": sides["ledger"],
        "difference_minor": difference,
        "rules_version": outcome.rules_version,
        "window_days": outcome.window_days,
    }
