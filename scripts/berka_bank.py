"""Bank side from the PKDD'99 Czech bank dataset (Berka): real, anonymised transactions from 1993-1998.

    python -m scripts.berka_bank --trans data/private/berka/trans.asc --out data/private/berka/bank_berka.csv

Defaults to the account with the most transactions. Pass --account to choose another.

Mapping (translations from the dataset guide):
- type PRIJEM (credit) is an inflow (+); VYDAJ and VYBER (withdrawals) are outflows (-).
- reference: the partner's "bank account" for transfers, else the category code (k_symbol, e.g. UROK for
  interest), else empty. The dataset has no invoice numbers, so cash and card rows usually have no reference.
- amounts are in Czech koruna (CZK). ReconDesk accepts only EUR, so amounts are CONVERTED at one fixed,
  illustrative rate (--czk-per-eur, default 25.00, not a historical rate) and rounded half-even to cents.
  This is a documented preprocessing step; the original CZK amount is kept in the description.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from reconcile.ingest import MAX_ROWS, REQUIRED_COLUMNS

DEFAULT_CZK_PER_EUR = Decimal("25.00")
CREDIT_TYPES = {"PRIJEM"}
DEBIT_TYPES = {"VYDAJ", "VYBER"}
OPERATIONS = {
    "VYBER KARTOU": "Card withdrawal",
    "VKLAD": "Cash credit",
    "PREVOD Z UCTU": "Collection from another bank",
    "VYBER": "Cash withdrawal",
    "PREVOD NA UCET": "Remittance to another bank",
}
CATEGORIES = {
    "POJISTNE": "insurance payment",
    "SLUZBY": "statement fee",
    "UROK": "interest credited",
    "SANKC. UROK": "sanction interest",
    "SIPO": "household payment",
    "DUCHOD": "pension",
    "UVER": "loan payment",
}


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def busiest_account(rows: list[dict]) -> str:
    counts = Counter(row["account_id"] for row in rows)
    return min(counts, key=lambda acct: (-counts[acct], int(acct) if acct.isdigit() else acct))


def _date(text: str) -> str:
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"unexpected date {text!r}; expected YYMMDD")
    return f"19{text[:2]}-{text[2:4]}-{text[4:]}"  # the dataset covers 1993-1998


def convert(rows: list[dict], account_id: str, czk_per_eur: Decimal = DEFAULT_CZK_PER_EUR):
    """Return (rows, rejects) for one account. Every input row for that account lands in exactly one list."""
    if czk_per_eur <= 0:
        raise ValueError("czk_per_eur must be positive")
    out, rejects = [], []
    for row in rows:
        if row["account_id"] != account_id:
            continue
        tid = row["trans_id"].strip()
        try:
            kind = row["type"].strip()
            if kind in CREDIT_TYPES:
                sign = 1
            elif kind in DEBIT_TYPES:
                sign = -1
            else:
                raise ValueError(f"unknown type {kind!r}")
            czk = Decimal(row["amount"].strip())
            if not czk.is_finite():
                raise ValueError("non-finite amount")
            eur = (sign * czk / czk_per_eur).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            day = _date(row["date"].strip())
        except (ValueError, InvalidOperation, KeyError) as exc:
            rejects.append({"trans_id": tid, "reason": str(exc)})
            continue
        category = row.get("k_symbol", "").strip()
        partner = " ".join(part for part in (row.get("bank", "").strip(), row.get("account", "").strip()) if part)
        reference = partner or category
        words = [OPERATIONS.get(row.get("operation", "").strip(), "Transaction")]
        if category:
            words.append(CATEGORIES.get(category, category.lower()))
        description = f"{', '.join(words)} (orig CZK {sign * czk:.2f})"
        out.append({"transaction_id": tid, "date": day, "amount": f"{eur:.2f}", "currency": "EUR",
                    "reference": reference, "description": description})
    if len(out) > MAX_ROWS:
        raise SystemExit(f"account {account_id} has {len(out)} rows; ReconDesk accepts at most {MAX_ROWS}")
    out.sort(key=lambda r: (r["date"], int(r["transaction_id"]) if r["transaction_id"].isdigit() else 0))
    return out, rejects


def _write(path: Path, rows: list[dict], columns) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trans", default="data/private/berka/trans.asc")
    parser.add_argument("--out", default="data/private/berka/bank_berka.csv")
    parser.add_argument("--account", help="account_id to export (default: the busiest account)")
    parser.add_argument("--czk-per-eur", type=Decimal, default=DEFAULT_CZK_PER_EUR)
    args = parser.parse_args(argv)
    rows = read_rows(Path(args.trans))
    account = args.account or busiest_account(rows)
    converted, rejects = convert(rows, account, args.czk_per_eur)
    out = Path(args.out)
    _write(out, converted, REQUIRED_COLUMNS)
    _write(out.with_suffix(".rejects.csv"), rejects, ("trans_id", "reason"))
    print(f"account {account}: {len(converted)} rows written to {out}, {len(rejects)} rejected; "
          f"CZK converted at {args.czk_per_eur} per EUR")


if __name__ == "__main__":
    main(sys.argv[1:])
