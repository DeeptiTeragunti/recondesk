import json
from pathlib import Path

import pytest

from reconcile.evaluation import evaluate
from reconcile.ingest import ingest_csv
from reconcile.matcher import WINDOW_DEFAULT
from scripts import obp_bank
from scripts.derive_ledger import derive, main as derive_main
from scripts.obp_bank import convert, fetch_transactions, login

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "obp_transactions_sample.json").read_text(encoding="utf-8"))
DEMO_BANK = (ROOT / "data" / "demo" / "bank_export_demo.csv").read_bytes()


# --- bank side: convert ------------------------------------------------------

def test_convert_maps_fields_and_rejects_without_rounding_or_dropping():
    rows, rejects = convert(SAMPLE["transactions"])
    assert len(rows) + len(rejects) == len(SAMPLE["transactions"])  # every input lands somewhere
    first, second = rows
    assert first == {"transaction_id": "c1a2-0001", "date": "2026-09-01", "amount": "-12.50", "currency": "EUR",
                     "reference": "INV-2026-0001 office supplies", "description": "INV-2026-0001 office supplies"}
    assert second["date"] == "2026-09-03"  # 23:30 at UTC-2 is 01:30 UTC the next day
    assert second["amount"] == "250.00" and second["currency"] == "EUR"
    reasons = {r["id"]: r["reason"] for r in rejects}
    assert "not EUR" in reasons["c1a2-0003"]
    assert "at most 2 places" in reasons["c1a2-0004"]  # -1.005 is rejected, not rounded
    assert "duplicate id" in reasons["c1a2-0001"]
    assert "missing completed/posted date" in reasons["c1a2-0006"]


def test_convert_options_reference_source_and_sign_flip():
    rows, _ = convert(SAMPLE["transactions"], reference_from="id", flip_sign=True)
    assert rows[0]["reference"] == "c1a2-0001"
    assert rows[0]["amount"] == "12.50"
    rows, _ = convert(SAMPLE["transactions"], reference_from="none")
    assert rows[0]["reference"] == ""
    with pytest.raises(ValueError):
        convert(SAMPLE["transactions"], reference_from="other_account")


def test_converted_csv_passes_reconDesk_ingest(tmp_path):
    out = tmp_path / "bank.csv"
    obp_bank.main(["convert", "--raw", str(ROOT / "tests/fixtures/obp_transactions_sample.json"), "--out", str(out)])
    result = ingest_csv("bank", out.read_bytes())
    assert result.accepted and all(r.valid for r in result.rows) and len(result.rows) == 2
    assert out.with_suffix(".rejects.csv").read_text(encoding="utf-8").count("\n") == 1 + 4


# --- bank side: fetch (network faked) ----------------------------------------

def test_login_uses_directlogin_header(monkeypatch):
    seen = {}

    def fake(url, headers, data=None):
        seen.update(url=url, headers=headers, data=data)
        return {"token": "tok"}

    monkeypatch.setattr(obp_bank, "_request", fake)
    assert login("https://sandbox.test", "u", "p", "k") == "tok"
    assert seen["url"] == "https://sandbox.test/my/logins/direct"
    assert seen["headers"]["Authorization"] == 'DirectLogin username="u",password="p",consumer_key="k"'
    assert seen["data"] == b""
    assert login("https://sandbox.test", "u", "p,with comma", "k") == "tok"
    assert 'password="p,with comma"' in seen["headers"]["Authorization"]
    with pytest.raises(SystemExit, match="double quote"):
        login("https://sandbox.test", "u", 'p"q', "k")


def test_http_errors_show_the_server_message(monkeypatch):
    import io
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"code":401,"message":"OBP-20004: Invalid login credentials"}'))

    monkeypatch.setattr(obp_bank.urllib.request, "urlopen", boom)
    with pytest.raises(obp_bank.ApiError, match="HTTP 401 from /my/logins/direct: .*OBP-20004"):
        obp_bank._request("https://sandbox.test/my/logins/direct", {}, data=b"")


def test_list_accounts_calls_my_accounts_with_token(monkeypatch):
    seen = {}

    def fake(url, headers, data=None):
        seen.update(url=url, headers=headers)
        return {"accounts": [{"id": "a1", "bank_id": "b1", "views_available": [{"id": "owner"}]}]}

    monkeypatch.setattr(obp_bank, "_request", fake)
    accounts = obp_bank.list_accounts("https://sandbox.test", "v5.1.0", "tok")
    assert accounts[0]["bank_id"] == "b1"
    assert seen["url"] == "https://sandbox.test/obp/v5.1.0/my/accounts"
    assert seen["headers"]["Authorization"] == "DirectLogin token=tok"


def test_fetch_stops_when_server_repeats_a_page(monkeypatch):
    page = {"transactions": [{"id": f"t{i}"} for i in range(obp_bank.PAGE_SIZE)]}
    calls = []
    monkeypatch.setattr(obp_bank, "_request", lambda url, headers, data=None: calls.append(url) or page)
    result = fetch_transactions("https://sandbox.test", "v5.1.0", "tok", "bank 1", "acc/1", "owner")
    assert len(result) == obp_bank.PAGE_SIZE and len(calls) == 2  # second identical page ends the loop
    assert "/banks/bank%201/accounts/acc%2F1/owner/transactions?limit=" in calls[0]


def test_fetch_follows_pages_until_short_page(monkeypatch):
    pages = [{"transactions": [{"id": f"a{i}"} for i in range(obp_bank.PAGE_SIZE)]},
             {"transactions": [{"id": "b1"}]}]
    monkeypatch.setattr(obp_bank, "_request", lambda url, headers, data=None: pages.pop(0))
    assert len(fetch_transactions("https://x.test", "v5.1.0", "tok", "b", "a", "owner")) == obp_bank.PAGE_SIZE + 1


# --- ledger side ---------------------------------------------------------------

def test_derived_ledger_is_reproducible_and_consistent_with_its_manifest():
    ledger_a, manifest_a = derive(DEMO_BANK, seed=7)
    ledger_b, manifest_b = derive(DEMO_BANK, seed=7)
    assert ledger_a == ledger_b and manifest_a == manifest_b
    assert derive(DEMO_BANK, seed=8)[0] != ledger_a

    ledger_ids = {row["transaction_id"] for row in ledger_a}
    bank_categories = manifest_a["row_categories"]["bank"]
    paired = {p["bank_id"] for p in manifest_a["truth_pairs"]}
    for bank_id, category in bank_categories.items():
        if category == "bank_only":
            assert bank_id not in paired
    assert all(p["ledger_id"] in ledger_ids for p in manifest_a["truth_pairs"])
    assert all(len(g["ledger_ids"]) == 2 for g in manifest_a["unresolvable_groups"])
    assert ingest_csv("ledger", _csv(ledger_a)).accepted


def test_derived_pair_evaluates_without_false_positives():
    ledger, manifest = derive(DEMO_BANK, seed=7)
    result = evaluate(DEMO_BANK, _csv(ledger), manifest, WINDOW_DEFAULT)
    assert result["false_positive"] == 0
    for category in ("delayed_outside_window", "reference_typo", "amount_differs"):
        if category in result["per_category"]:
            assert result["per_category"][category]["auto_matched"] == 0


def test_derive_cli_writes_both_files(tmp_path):
    bank = tmp_path / "bank.csv"
    bank.write_bytes(DEMO_BANK)
    derive_main(["--bank", str(bank), "--out-dir", str(tmp_path), "--seed", "3"])
    assert (tmp_path / "ledger_derived.csv").exists()
    assert json.loads((tmp_path / "truth_manifest_derived.json").read_text())["seed"] == 3


def _csv(rows: list[dict]) -> bytes:
    header = "transaction_id,date,amount,currency,reference,description"
    lines = [header] + [",".join(_quote(row[c]) for c in header.split(",")) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def test_derived_ledger_is_messy_in_labelled_ways():
    ledger, manifest = derive(DEMO_BANK, seed=7)
    by_id = {row["transaction_id"]: row for row in ledger}
    bank = {r.transaction_id: r for r in ingest_csv("bank", DEMO_BANK).rows}
    copied = sum(by_id[p["ledger_id"]]["description"] == bank[p["bank_id"]].description for p in manifest["truth_pairs"])
    assert copied == 0  # ledger wording is its own, never the bank text
    for group in manifest["many_to_one_groups"]:
        (lid,) = group["ledger_ids"]
        total = sum(bank[b].amount_minor for b in group["bank_ids"])
        assert ingest_csv("ledger", _csv([by_id[lid]])).rows[0].amount_minor == total
    for group in manifest["one_to_many_groups"]:
        parts = [ingest_csv("ledger", _csv([by_id[l]])).rows[0].amount_minor for l in group["ledger_ids"]]
        assert sum(parts) == bank[group["bank_ids"][0]].amount_minor and len(parts) == 2
    assert any(cat == "ledger_only" for cat in manifest["row_categories"]["ledger"].values())


def test_wrong_sign_pairs_are_never_matched_or_proposed():
    ledger, manifest = derive(DEMO_BANK, seed=7)
    result = evaluate(DEMO_BANK, _csv(ledger), manifest, WINDOW_DEFAULT)
    if "wrong_sign" in result["per_category"]:
        stats = result["per_category"]["wrong_sign"]
        assert stats["auto_matched"] == 0 and stats["proposed"] == 0


def test_plain_http_base_url_is_refused(monkeypatch):
    monkeypatch.setenv("OBP_BASE_URL", "http://apisandbox.openbankproject.com")
    with pytest.raises(SystemExit, match="https"):
        obp_bank._base_url()
