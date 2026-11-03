"""Generate the SYNTHETIC ReconDesk demo: two CSV exports plus an independent truth manifest.

Run from the project root:  python -m scripts.generate_demo
Output is deterministic for a given SEED.
"""
from __future__ import annotations

import csv
import itertools
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

SEED = 20261103
BASE_DATE = date(2026, 9, 1)
OUT_DIR = Path("data/demo")
HEADER = ["transaction_id", "date", "amount", "currency", "reference", "description"]
VENDORS = [
    "Northwind Office Supplies", "Contoso Cloud Hosting", "Fabrikam Logistics", "Adventure Works Travel",
    "Litware Consulting", "Tailspin Utilities", "Wide World Catering", "Proseware Software",
    "Alpine Ski Rentals", "Blue Yonder Freight",
]
# (category, count, candidate ledger day offsets)
PAIR_PLAN = [
    ("clean_pair", 45, [0]),
    ("delayed_in_window", 15, [-2, -1, 1, 2]),
    ("delayed_outside_window", 6, [-4, -3, 3, 4]),
    ("empty_reference_pair", 6, [0, 1]),
]


@dataclass
class Line:
    source: str
    transaction_id: str
    day: date
    amount_minor: int
    reference: str
    description: str
    category: str


class Generator:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.refs = itertools.count(1000)
        self.bank: list[Line] = []
        self.ledger: list[Line] = []
        self.truth_pairs: list[dict] = []
        self.unresolvable: list[dict] = []

    def next_ref(self) -> str:
        return f"INV-2026-{next(self.refs):04d}"

    def amount(self) -> int:
        cents = self.rng.randint(500, 250000)
        return -cents if self.rng.random() < 0.6 else cents  # outflows are the majority, signs mixed

    def day(self) -> date:
        return BASE_DATE + timedelta(days=self.rng.randint(0, 29))

    def vendor(self) -> str:
        return self.rng.choice(VENDORS)

    def noisy_ref(self, ref: str) -> str:
        # Only outer whitespace and case vary: normalisation must collapse these.
        if self.rng.random() < 0.15:
            return f"  {ref.lower()}  "
        return ref

    def add(self, source: str, day: date, amount: int, ref: str, category: str) -> Line:
        prefix = "B" if source == "bank" else "L"
        bucket = self.bank if source == "bank" else self.ledger
        line = Line(source, f"{prefix}{len(bucket) + 1:04d}", day, amount, ref, self.vendor(), category)
        bucket.append(line)
        return line

    def pair(self, category: str, ledger_offset: int, ref: str, ledger_ref: str | None = None,
             amount: int | None = None) -> None:
        amount = self.amount() if amount is None else amount
        day = self.day()
        bank = self.add("bank", day, amount, ref, category)
        ledger = self.add("ledger", day + timedelta(days=ledger_offset), amount,
                          ref if ledger_ref is None else ledger_ref, category)
        self.truth_pairs.append({"bank_id": bank.transaction_id, "ledger_id": ledger.transaction_id,
                                 "category": category, "ledger_day_offset": ledger_offset})

    def build(self) -> None:
        for category, count, offsets in PAIR_PLAN:
            for _ in range(count):
                ref = "" if category == "empty_reference_pair" else self.next_ref()
                offset = self.rng.choice(offsets)
                self.pair(category, offset, self.noisy_ref(ref) if ref else "")
        # Duplicate reference and amount on both sides: genuinely ambiguous, truth not asserted.
        for _ in range(2):
            ref = self.next_ref()
            amount = self.amount()
            day = self.day()
            bank_ids = [self.add("bank", day, amount, ref, "duplicate_reference_amount").transaction_id for _ in range(2)]
            ledger_ids = [self.add("ledger", day, amount, ref, "duplicate_reference_amount").transaction_id for _ in range(2)]
            self.unresolvable.append({"category": "duplicate_reference_amount", "bank_ids": bank_ids,
                                      "ledger_ids": ledger_ids})
        # Same amount and date, typo-variant references: a conflict, never a match.
        for _ in range(5):
            ref = self.next_ref()
            amount = self.amount()
            day = self.day()
            self.add("bank", day, amount, ref, "conflicting_reference")
            self.add("ledger", day, amount, f"INV-2026-{self.rng.randint(9000, 9999):04d}", "conflicting_reference")
        # Same amount, near date, unrelated references.
        for _ in range(5):
            amount = self.amount()
            day = self.day()
            self.add("bank", day, amount, self.next_ref(), "same_amount_unrelated_ref")
            self.add("ledger", day + timedelta(days=self.rng.choice([-1, 1])), amount,
                     f"TRF-{self.rng.randint(10000, 99999)}", "same_amount_unrelated_ref")
        # Shared reference, amount differs by a few cents.
        for _ in range(4):
            ref = self.next_ref()
            amount = self.amount()
            day = self.day()
            self.add("bank", day, amount, ref, "shared_ref_amount_differs")
            self.add("ledger", day, amount + self.rng.choice([-25, 15, 40]), ref, "shared_ref_amount_differs")
        for _ in range(10):
            self.add("bank", self.day(), self.amount(), self.next_ref(), "bank_only")
        for _ in range(10):
            self.add("ledger", self.day(), self.amount(), self.next_ref(), "ledger_only")

    def shuffled(self) -> tuple[list[Line], list[Line]]:
        # Shuffle so file order does not reveal the category layout.
        self.rng.shuffle(self.bank)
        self.rng.shuffle(self.ledger)
        return self.bank, self.ledger


def write_csv(path: Path, lines: list[Line]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for line in lines:
            writer.writerow([line.transaction_id, line.day.isoformat(), _money(line.amount_minor), "EUR",
                             line.reference, line.description])


def _money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d}"


def main(out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    gen = Generator(SEED)
    gen.build()
    bank, ledger = gen.shuffled()
    write_csv(out_dir / "bank_export_demo.csv", bank)
    write_csv(out_dir / "ledger_export_demo.csv", ledger)
    manifest = {
        "dataset_id": "recondesk-demo-v1",
        "synthetic": True,
        "seed": SEED,
        "generator": "scripts/generate_demo.py",
        "row_counts": {"bank": len(bank), "ledger": len(ledger)},
        "truth_pairs": sorted(gen.truth_pairs, key=lambda p: p["bank_id"]),
        "unresolvable_groups": gen.unresolvable,
        "row_categories": {
            "bank": {line.transaction_id: line.category for line in bank},
            "ledger": {line.transaction_id: line.category for line in ledger},
        },
        "notes": [
            "truth_pairs are genuine counterparts used for measurement; the system must not assert them when the rules abstain.",
            "unresolvable_groups have duplicate reference and amount; the true pairing is not asserted and they are excluded from recall.",
            "conflicting_reference, same_amount_unrelated_ref and shared_ref_amount_differs rows have no counterpart in truth.",
        ],
    }
    (out_dir / "truth_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(bank)} bank rows, {len(ledger)} ledger rows, {len(gen.truth_pairs)} truth pairs to {out_dir}")


if __name__ == "__main__":
    main()
