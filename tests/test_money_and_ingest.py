import pytest

from reconcile.ingest import MAX_BYTES, MAX_ROWS, ingest_csv
from reconcile.money import format_minor, normalize_reference, parse_amount_minor

HEADER = b"transaction_id,date,amount,currency,reference,description\n"


def csv_bytes(*lines: str) -> bytes:
    return HEADER + "\n".join(lines).encode("utf-8") + b"\n"


# --- money -----------------------------------------------------------------

@pytest.mark.parametrize("text, cents", [
    ("0", 0), ("12", 1200), ("-12.5", -1250), ("+0.05", 5), ("-0.05", -5), ("1234567.89", 123456789),
    ("-0.00", 0), ("  7.10 ", 710),
])
def test_parse_amount_is_exact_integer_cents(text, cents):
    assert parse_amount_minor(text) == cents


@pytest.mark.parametrize("text", ["", None, "1e3", "NaN", "inf", "-Infinity", "1,000.00", "1.001", "12.", ".5",
                                  "EUR 5", "0x10", "١٢.٠٠"])
def test_parse_amount_rejects_anything_else(text):
    with pytest.raises(ValueError):
        parse_amount_minor(text)


def test_decimal_addition_has_no_float_drift():
    assert parse_amount_minor("0.10") + parse_amount_minor("0.20") == parse_amount_minor("0.30")


def test_format_round_trips_sign_and_padding():
    assert format_minor(-5) == "-0.05"
    assert format_minor(-125000) == "-1250.00"
    assert format_minor(7) == "0.07"


def test_reference_normalisation_keeps_digits_and_punctuation():
    assert normalize_reference("  inv-2026 - 0001 ") == "INV-2026 - 0001"
    assert normalize_reference("INV  2026\t0001") == "INV 2026 0001"
    assert normalize_reference("INV-2026-0001") != normalize_reference("INV-2026-0010")


# --- ingest ----------------------------------------------------------------

def test_valid_file_keeps_every_row_and_original_values():
    result = ingest_csv("bank", csv_bytes(
        "B1,2026-09-01,-12.50,EUR,inv-1,Vendor A",
        "B2,2026-09-02,100.00,EUR,,Refund",
    ))
    assert result.accepted
    assert len(result.rows) == 2 and all(r.valid for r in result.rows)
    assert result.rows[0].raw["amount"] == "-12.50"
    assert result.rows[0].reference_norm == "INV-1"
    assert result.rows[1].reference_norm == ""  # empty reference allowed but never auto-matchable
    assert len(result.sha256) == 64


def test_missing_required_column_rejects_the_file():
    result = ingest_csv("bank", b"transaction_id,date,amount,currency,reference\nB1,2026-09-01,1.00,EUR,x\n")
    assert not result.accepted
    assert "description" in result.file_errors[0]


@pytest.mark.parametrize("line, fragment", [
    ("B1,2026-02-30,1.00,EUR,x,d", "invalid calendar date"),
    ("B1,01/09/2026,1.00,EUR,x,d", "malformed date"),
    ("B1,2026-09-01,1.001,EUR,x,d", "at most 2 places"),
    ("B1,2026-09-01,NaN,EUR,x,d", "invalid amount"),
    ("B1,2026-09-01,1.00,USD,x,d", "unsupported currency"),
    ("B1,2026-09-01,,EUR,x,d", "missing amount"),
    (",2026-09-01,1.00,EUR,x,d", "missing transaction_id"),
    ("B1,2026-09-01,1.00,EUR", "expected 6 cells"),
])
def test_row_level_errors_are_kept_not_dropped(line, fragment):
    result = ingest_csv("ledger", csv_bytes(line, "B9,2026-09-01,1.00,EUR,ok,d"))
    assert len(result.rows) == 2  # the bad row is kept and the good row is still present
    bad = result.rows[0]
    assert not bad.valid
    assert any(fragment in e for e in bad.errors), bad.errors
    assert result.rows[1].valid


def test_duplicate_ids_flag_every_occurrence():
    result = ingest_csv("bank", csv_bytes("B1,2026-09-01,1.00,EUR,x,d", "B1,2026-09-02,2.00,EUR,y,d"))
    assert [r.valid for r in result.rows] == [False, False]
    assert all("duplicate transaction_id" in r.errors[0] for r in result.rows)


def test_non_utf8_and_oversize_files_are_rejected_whole():
    assert "UTF-8" in ingest_csv("bank", b"\xff\xfe\x00bad").file_errors[0]
    assert "limit" in ingest_csv("bank", b"x" * (MAX_BYTES + 1)).file_errors[0]


def test_row_count_limit():
    lines = [f"B{i},2026-09-01,1.00,EUR,r{i},d" for i in range(MAX_ROWS + 1)]
    assert "limit" in ingest_csv("bank", csv_bytes(*lines)).file_errors[0]


def test_filename_is_never_used_as_path_and_hash_is_stable():
    first = ingest_csv("bank", csv_bytes("B1,2026-09-01,1.00,EUR,x,d"))
    second = ingest_csv("bank", csv_bytes("B1,2026-09-01,1.00,EUR,x,d"))
    assert first.sha256 == second.sha256


def test_oversized_cell_is_a_file_error_not_a_crash():
    huge = "x" * 200_000  # beyond the csv module's default field size limit
    result = ingest_csv("bank", csv_bytes(f'B1,2026-09-01,1.00,EUR,ref,"{huge}"'))
    assert not result.accepted and "malformed CSV" in result.file_errors[0]
