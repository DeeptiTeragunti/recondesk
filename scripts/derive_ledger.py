"""Ledger side: derive a realistic cash-ledger CSV from a bank CSV, plus a truth manifest.

    python -m scripts.derive_ledger --bank data/berka/bank_berka.csv --out-dir data/berka --seed 7

The bank CSV must already be in ReconDesk format. The ledger is SYNTHETIC but deliberately messy, the way a
bookkeeper's records differ from a bank feed:
- its own wording (descriptions are written from the ledger's point of view, not copied from the bank),
- references left blank or mistyped, case and spacing noise,
- postings a few days early or late, fees netted, the occasional wrong sign,
- deposits batched into one entry, payments split in two, duplicate bookings,
- bank rows never booked, and ledger entries with no bank movement yet.

Because the ledger is built from the bank rows, the true pairing is known by construction. That makes the
measurement repeatable; it is still not real-world accuracy.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

from reconcile.ingest import REQUIRED_COLUMNS, ingest_csv
from reconcile.money import format_minor

# (category, share of valid bank rows). Shares sum to 1.
PLAN = [
    ("clean_pair", 0.52),
    ("delayed_in_window", 0.16),
    ("delayed_outside_window", 0.05),
    ("reference_missing_in_ledger", 0.07),
    ("reference_typo", 0.04),
    ("amount_differs", 0.04),
    ("wrong_sign", 0.01),
    ("bank_only", 0.05),
    ("duplicated_in_ledger", 0.02),
    ("batched_in_ledger", 0.03),
    ("split_in_ledger", 0.01),
]
LEDGER_ONLY_SHARE = 0.05
# Ledger wording by keyword in the bank description; first match wins.
WORDING = [
    ("interest", "Interest income"),
    ("statement fee", "Bank charges"),
    ("sanction", "Penalty interest"),
    ("household", "Household payment (SIPO)"),
    ("insurance", "Insurance premium"),
    ("pension", "Pension receipt"),
    ("loan", "Loan repayment"),
    ("cash credit", "Cash deposit"),
    ("cash withdrawal", "Petty cash withdrawal"),
    ("card withdrawal", "Card payment"),
    ("remittance", "Supplier payment"),
    ("collection", "Customer receipt"),
]


def _pick(rng: random.Random) -> str:
    roll, total = rng.random(), 0.0
    for category, share in PLAN:
        total += share
        if roll < total:
            return category
    return PLAN[0][0]


def ledger_wording(bank_description: str, cents: int) -> str:
    text = bank_description.lower()
    for keyword, wording in WORDING:
        if keyword in text:
            return wording
    head = bank_description.split(" (orig")[0].strip() or "counterparty"
    return f"{'Payment to' if cents < 0 else 'Receipt from'} {head}"


def _typo(reference: str, rng: random.Random) -> str:
    position = rng.randrange(len(reference))
    char = reference[position]
    replacement = str((int(char) + rng.randint(1, 9)) % 10) if char.isdigit() else "X"
    return reference[:position] + replacement + reference[position + 1:]


def _noisy(reference: str, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.15:
        return f"  {reference.lower()} "
    if roll < 0.20:
        return reference.replace(" ", "  ")
    return reference


def derive(bank_bytes: bytes, seed: int) -> tuple[list[dict], dict]:
    result = ingest_csv("bank", bank_bytes)
    if not result.accepted:
        raise SystemExit("bank file rejected: " + "; ".join(result.file_errors))
    valid = [r for r in result.rows if r.valid]
    if not valid:
        raise SystemExit("bank file has no valid rows")
    rng = random.Random(seed)
    ledger: list[dict] = []
    truth_pairs, unresolvable, many_to_one, one_to_many = [], [], [], []
    categories = {"bank": {}, "ledger": {}}

    def add(day, cents, reference, description, category):
        lid = f"L{len(ledger) + 1:05d}"
        ledger.append({"transaction_id": lid, "date": day.isoformat(), "amount": format_minor(cents),
                       "currency": "EUR", "reference": reference, "description": description})
        categories["ledger"][lid] = category
        return lid

    def pair(bank_row, lid, category, offset):
        truth_pairs.append({"bank_id": bank_row.transaction_id, "ledger_id": lid, "category": category,
                            "ledger_day_offset": offset})

    batch_pool = []
    for row in valid:
        category = _pick(rng)
        if category in ("reference_missing_in_ledger", "reference_typo") and not row.reference:
            category = "clean_pair"
        if category == "split_in_ledger" and abs(row.amount_minor) < 200:
            category = "clean_pair"
        categories["bank"][row.transaction_id] = category
        day, cents, ref = row.date, row.amount_minor, row.reference
        wording = ledger_wording(row.description, cents)
        if category == "bank_only":
            continue
        if category == "batched_in_ledger":
            batch_pool.append(row)
            continue
        if category == "split_in_ledger":
            first = int(cents * rng.uniform(0.3, 0.7))
            ids = [add(day, first, ref, wording + " (part 1)", category),
                   add(day, cents - first, ref, wording + " (part 2)", category)]
            one_to_many.append({"category": category, "bank_ids": [row.transaction_id], "ledger_ids": ids})
            continue
        offset = 0
        if category == "delayed_in_window":
            offset = rng.choice([-2, -1, 1, 1, 2])
        elif category == "delayed_outside_window":
            offset = rng.choice([-5, -4, -3, 3, 4, 5])
        elif category == "reference_missing_in_ledger":
            ref = ""
        elif category == "reference_typo":
            ref = _typo(ref, rng)
        elif category == "amount_differs":
            fee = max(25, abs(cents) * rng.choice([1, 2, 5]) // 1000)  # 0.1-0.5%, at least 0.25 EUR
            cents = cents - fee if rng.random() < 0.5 else cents + fee
        elif category == "wrong_sign":
            cents = -cents
        if ref and category not in ("reference_typo",):
            ref = _noisy(ref, rng)
        lid = add(day + timedelta(days=offset), cents, ref, wording, category)
        if category == "duplicated_in_ledger":
            twin = add(day + timedelta(days=rng.choice([0, 1])), cents, ref, wording, category)
            unresolvable.append({"category": category, "bank_ids": [row.transaction_id], "ledger_ids": [lid, twin]})
        else:
            pair(row, lid, category, offset)

    # Batches: 2-3 same-sign bank rows booked as one ledger entry on the last row's date.
    batch_pool.sort(key=lambda r: (r.amount_minor < 0, r.date, r.transaction_id))
    i = 0
    while i < len(batch_pool):
        size = rng.choice([2, 3])
        group = [r for r in batch_pool[i:i + size] if (r.amount_minor < 0) == (batch_pool[i].amount_minor < 0)]
        i += len(group)
        if len(group) == 1:  # nothing to batch with: book it normally
            only = group[0]
            categories["bank"][only.transaction_id] = "clean_pair"
            pair(only, add(only.date, only.amount_minor, only.reference,
                           ledger_wording(only.description, only.amount_minor), "clean_pair"), "clean_pair", 0)
            continue
        total = sum(r.amount_minor for r in group)
        lid = add(max(r.date for r in group), total, "",
                  "Batch deposit" if total > 0 else "Batch payment", "batched_in_ledger")
        many_to_one.append({"category": "batched_in_ledger", "bank_ids": [r.transaction_id for r in group],
                            "ledger_ids": [lid]})

    first_day, last_day = min(r.date for r in valid), max(r.date for r in valid)
    span = max((last_day - first_day).days, 0)
    for n in range(max(1, round(len(valid) * LEDGER_ONLY_SHARE))):
        # Sized like this account's own movements, so ledger-only entries do not dwarf the real ones.
        cents = int(rng.choice(valid).amount_minor * rng.uniform(0.5, 1.5)) or -100
        kind = rng.choice(["Accrued expense", "Outstanding cheque", "Deposit in transit", "Manual adjustment"])
        add(first_day + timedelta(days=rng.randint(0, span)), cents, f"JNL-{n + 1:04d}", kind, "ledger_only")

    rng.shuffle(ledger)
    manifest = {
        "dataset_id": f"derived-ledger-seed-{seed}",
        "synthetic": True,
        "seed": seed,
        "generator": "scripts/derive_ledger.py",
        "provenance": {
            "bank_sha256": result.sha256,
            "bank_valid_rows": len(valid),
            "bank_invalid_rows": len(result.rows) - len(valid),
            "ledger": "derived from the bank rows with seeded, labelled differences",
        },
        "row_counts": {"bank": len(valid), "ledger": len(ledger)},
        "truth_pairs": sorted(truth_pairs, key=lambda p: p["bank_id"]),
        "unresolvable_groups": unresolvable,
        "many_to_one_groups": many_to_one,
        "one_to_many_groups": one_to_many,
        "row_categories": categories,
        "notes": [
            "truth_pairs are one-to-one counterparts known by construction. Several categories are genuine pairs "
            "that the exact rules are expected to abstain on and the probable-match rule may propose.",
            "wrong_sign pairs are genuine but booked with the opposite sign; no rule should match them.",
            "duplicated_in_ledger groups have two identical ledger rows; the true one is not asserted.",
            "many_to_one (batched) and one_to_many (split) groups are not one-to-one and are unsupported by design.",
        ],
    }
    return ledger, manifest


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--out-dir", default="data/private")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    ledger, manifest = derive(Path(args.bank).read_bytes(), args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "ledger_derived.csv"
    with ledger_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(ledger)
    manifest_path = out_dir / "truth_manifest_derived.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(ledger)} ledger rows to {ledger_path} and {len(manifest['truth_pairs'])} truth pairs "
          f"to {manifest_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
