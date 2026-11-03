from decimal import Decimal

import pytest

from reconcile.ingest import ingest_csv
from scripts.berka_bank import busiest_account, convert

# Hand-written lines in the Berka trans.asc shape (not copied from the dataset).
ROWS = [
    {"trans_id": "1", "account_id": "7", "date": "930105", "type": "PRIJEM", "operation": "VKLAD",
     "amount": "1000.00", "balance": "1000.00", "k_symbol": "", "bank": "", "account": ""},
    {"trans_id": "2", "account_id": "7", "date": "930201", "type": "VYDAJ", "operation": "PREVOD NA UCET",
     "amount": "333.33", "balance": "666.67", "k_symbol": "SIPO", "bank": "AB", "account": "41403269"},
    {"trans_id": "3", "account_id": "7", "date": "930131", "type": "PRIJEM", "operation": "",
     "amount": "12.50", "balance": "679.17", "k_symbol": "UROK", "bank": "", "account": ""},
    {"trans_id": "4", "account_id": "7", "date": "930301", "type": "VYBER", "operation": "VYBER",
     "amount": "100.00", "balance": "579.17", "k_symbol": " ", "bank": "", "account": ""},
    {"trans_id": "5", "account_id": "7", "date": "93-03-1", "type": "VYDAJ", "operation": "VYBER",
     "amount": "1.00", "balance": "0", "k_symbol": "", "bank": "", "account": ""},
    {"trans_id": "6", "account_id": "7", "date": "930302", "type": "????", "operation": "VYBER",
     "amount": "1.00", "balance": "0", "k_symbol": "", "bank": "", "account": ""},
    {"trans_id": "9", "account_id": "8", "date": "930101", "type": "PRIJEM", "operation": "VKLAD",
     "amount": "5.00", "balance": "5.00", "k_symbol": "", "bank": "", "account": ""},
]


def test_convert_signs_dates_references_and_keeps_original_czk():
    rows, rejects = convert(ROWS, "7")
    assert len(rows) + len(rejects) == 6  # account 8's row is not included
    by_id = {r["transaction_id"]: r for r in rows}
    assert by_id["1"]["amount"] == "40.00" and by_id["1"]["date"] == "1993-01-05"
    assert by_id["1"]["reference"] == "" and "orig CZK 1000.00" in by_id["1"]["description"]
    assert by_id["2"]["amount"] == "-13.33"  # -333.33 / 25 = -13.3332, half-even to cents
    assert by_id["2"]["reference"] == "AB 41403269"
    assert by_id["2"]["description"].startswith("Remittance to another bank, household payment")
    assert by_id["3"]["reference"] == "UROK" and by_id["3"]["amount"] == "0.50"
    assert "interest credited" in by_id["3"]["description"]
    assert by_id["4"]["amount"] == "-4.00" and by_id["4"]["reference"] == ""  # blank k_symbol is no reference
    reasons = {r["trans_id"]: r["reason"] for r in rejects}
    assert "YYMMDD" in reasons["5"] and "unknown type" in reasons["6"]
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)


def test_converted_rows_pass_reconDesk_ingest():
    rows, _ = convert(ROWS, "7", Decimal("1"))
    header = "transaction_id,date,amount,currency,reference,description"
    text = header + "\n" + "\n".join(
        ",".join('"' + r[c] + '"' for c in header.split(",")) for r in rows) + "\n"
    result = ingest_csv("bank", text.encode("utf-8"))
    assert result.accepted and all(r.valid for r in result.rows)
    by_id = {r.transaction_id: r for r in result.rows}
    assert by_id["2"].amount_minor == -33333  # rate 1 keeps the CZK value exactly


def test_busiest_account_and_bad_rate():
    assert busiest_account(ROWS) == "7"
    with pytest.raises(ValueError):
        convert(ROWS, "7", Decimal("0"))
