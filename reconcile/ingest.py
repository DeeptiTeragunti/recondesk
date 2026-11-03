"""Strict CSV ingestion for the two prescribed input schemas. Nothing is silently dropped."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field

from reconcile.money import normalize_reference, parse_amount_minor

REQUIRED_COLUMNS = ("transaction_id", "date", "amount", "currency", "reference", "description")
NON_EMPTY_COLUMNS = ("transaction_id", "date", "amount", "currency")  # reference and description may be empty
ALLOWED_CURRENCY = "EUR"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


@dataclass
class InputRow:
    source: str  # "bank" or "ledger"
    row_number: int  # 1-based data row, header excluded
    raw: dict[str, str | None]  # original cell values, untouched
    transaction_id: str = ""
    date: dt.date | None = None
    amount_minor: int | None = None
    currency: str = ""
    reference: str = ""  # outer whitespace trimmed only
    reference_norm: str = ""  # case and whitespace normalised; empty means no usable reference
    description: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass
class IngestResult:
    source: str
    sha256: str
    rows: list[InputRow]
    file_errors: list[str]

    @property
    def accepted(self) -> bool:
        return not self.file_errors


def ingest_csv(source: str, data: bytes) -> IngestResult:
    """Parse one export. File-level problems return no rows; row-level problems are kept as invalid rows."""
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > MAX_BYTES:
        return IngestResult(source, digest, [], [f"file is {len(data):,} bytes; the limit is {MAX_BYTES:,}"])
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return IngestResult(source, digest, [], ["file is not valid UTF-8 text"])
    try:
        records = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:  # e.g. a cell over the csv module's field size limit
        return IngestResult(source, digest, [], [f"malformed CSV: {exc}"])
    if not records:
        return IngestResult(source, digest, [], ["file is empty"])

    header = [cell.strip() for cell in records[0]]
    file_errors = []
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        file_errors.append("missing required column(s): " + ", ".join(missing))
    duplicated = [name for name, count in Counter(header).items() if count > 1]
    if duplicated:
        file_errors.append("duplicate column(s): " + ", ".join(duplicated))
    body = records[1:]
    if len(body) > MAX_ROWS:
        file_errors.append(f"{len(body):,} data rows; the limit is {MAX_ROWS:,}")
    if file_errors:
        return IngestResult(source, digest, [], file_errors)

    rows = [_parse_row(source, number, header, cells) for number, cells in enumerate(body, start=1)]
    _flag_duplicate_ids(rows)
    return IngestResult(source, digest, rows, [])


def _parse_row(source: str, number: int, header: list[str], cells: list[str]) -> InputRow:
    raw = {name: (cells[i] if i < len(cells) else None) for i, name in enumerate(header)}
    row = InputRow(source=source, row_number=number, raw=raw)
    if not any(cell.strip() for cell in cells):
        row.errors.append("blank line")
        return row
    if len(cells) != len(header):
        row.errors.append(f"expected {len(header)} cells, found {len(cells)}")

    def cell(name: str) -> str:
        return (raw.get(name) or "").strip()

    row.transaction_id = cell("transaction_id")
    row.currency = cell("currency")
    row.reference = cell("reference")
    row.reference_norm = normalize_reference(row.reference)
    row.description = cell("description")
    for name in NON_EMPTY_COLUMNS:
        if not cell(name):
            row.errors.append(f"missing {name}")
    row.date = _parse_date(cell("date"), row.errors)
    if cell("amount"):
        try:
            row.amount_minor = parse_amount_minor(raw.get("amount"))
        except ValueError as exc:
            row.errors.append(str(exc))
    if row.currency and row.currency != ALLOWED_CURRENCY:
        row.errors.append(f"unsupported currency {row.currency!r}; only {ALLOWED_CURRENCY} is accepted")
    return row


def _parse_date(text: str, errors: list[str]) -> dt.date | None:
    if not text:
        return None
    if not _DATE_PATTERN.fullmatch(text):
        errors.append(f"malformed date {text!r}; expected YYYY-MM-DD")
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        errors.append(f"invalid calendar date {text!r}")
        return None


def _flag_duplicate_ids(rows: list[InputRow]) -> None:
    counts = Counter(row.transaction_id for row in rows if row.transaction_id)
    for row in rows:
        if row.transaction_id and counts[row.transaction_id] > 1:
            row.errors.append(f"duplicate transaction_id {row.transaction_id!r} within {row.source} file")
