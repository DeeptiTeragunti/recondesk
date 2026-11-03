import csv
import io
import json

import pytest

from reconcile import service, store
from reconcile.exporter import events_csv, neutralize, results_csv
from reconcile.review import ReviewError

HEADER = "transaction_id,date,amount,currency,reference,description"


def make_csv(*lines):
    return (HEADER + "\n" + "\n".join(lines) + "\n").encode("utf-8")


BANK = make_csv(
    "B1,2026-09-01,-5.00,EUR,X1,Clean vendor",
    "B2,2026-09-01,-7.00,EUR,,Empty ref vendor",
    "B3,2026-09-03,-9.00,EUR,X3,Outside window",
    "B4,2026-09-05,-2.00,EUR,X4,=HYPERLINK(\"http://evil\")",
    "B5,2026-09-01,-1.00,EUR,X5,Shared ref other amount",
)
LEDGER = make_csv(
    "L1,2026-09-01,-5.00,EUR,x1,Clean vendor",
    "L2,2026-09-01,-7.00,EUR,,Empty ref vendor",
    "L3,2026-09-10,-9.00,EUR,X3,Outside window",
    "L4,2026-09-05,-2.00,EUR,X4,@SUM(A1)",
    "L5,2026-09-01,-1.50,EUR,X5,Shared ref other amount",
)


@pytest.fixture
def conn(tmp_path):
    connection = store.connect(tmp_path / "test.sqlite3")
    yield connection
    connection.close()


def start(conn, bank=BANK, ledger=LEDGER, window=2, name="bank.csv"):
    return service.start_run(conn, source_label="upload", bank_name=name, ledger_name="ledger.csv",
                             bank_bytes=bank, ledger_bytes=ledger, window_days=window)


def ledger_row(run, tid):
    return next(r for r in run["ledger"] if r.transaction_id == tid)


def test_manual_confirmation_of_suggested_pair_is_stored_and_reloaded(conn):
    run_id = start(conn)
    run = service.open_run(conn, run_id)
    assert ("B2", "L2") not in run["outcome"].matches  # proposals never auto-match
    assert ("B2", "L2", 3) in run["outcome"].proposals
    service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B2", "ledger_id": "L2"})

    reopened = service.open_run(conn, run_id)  # persistence after reload
    assert reopened["state"].matches["B2"] == "L2"
    assert reopened["state"].origin["B2"] == "manual"
    assert reopened["events"][0]["actor"] == "local-user"
    assert reopened["outcome"].results[("bank", "B2")].status == "probable"  # system status untouched


def test_manual_match_enforces_one_to_one_and_candidate_constraints(conn):
    run_id = start(conn)
    run = service.open_run(conn, run_id)
    service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B2", "ledger_id": "L2"})
    run = service.open_run(conn, run_id)
    with pytest.raises(ReviewError, match="already matched"):
        service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B2", "ledger_id": "L2"})
    # B3/L3: equal reference and amount but 7 days apart, beyond even the probable-match limit.
    with pytest.raises(ReviewError, match="not a candidate"):
        service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B3", "ledger_id": "L3"})
    # B5/L5: probable match with a 0.50 difference: confirming needs a note that explains it.
    with pytest.raises(ReviewError, match="note explaining the difference"):
        service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B5", "ledger_id": "L5"})
    assert len(service.open_run(conn, run_id)["events"]) == 1
    service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B5", "ledger_id": "L5",
                                                  "note": "0.50 bank fee"})
    assert service.open_run(conn, run_id)["state"].matches["B5"] == "L5"


def test_review_note_is_required(conn):
    run_id = start(conn)
    run = service.open_run(conn, run_id)
    with pytest.raises(ReviewError, match="note is required"):
        service.add_review_event(conn, run, run_id, {"event_type": "mark_reviewed", "source": "bank",
                                                      "transaction_id": "B3", "note": "   "})
    service.add_review_event(conn, run, run_id, {"event_type": "mark_reviewed", "source": "bank",
                                                  "transaction_id": "B3", "note": "Checked with AP"})
    reopened = service.open_run(conn, run_id)
    assert reopened["state"].reviewed[("bank", "B3")]["note"] == "Checked with AP"


def test_new_upload_creates_new_run_and_does_not_reuse_decisions(conn):
    first = start(conn)
    run = service.open_run(conn, first)
    service.add_review_event(conn, run, first, {"event_type": "confirm_match", "bank_id": "B2", "ledger_id": "L2"})
    second = start(conn)
    assert second != first
    assert service.open_run(conn, second)["events"] == []


def test_stored_bytes_are_verified_on_reopen(conn):
    run_id = start(conn)
    conn.execute("update runs set bank_sha256 = 'deadbeef' where id = ?", (run_id,))
    conn.commit()
    with pytest.raises(RuntimeError, match="hashes"):
        service.open_run(conn, run_id)


def test_export_neutralises_formula_text_but_not_signed_amounts(conn):
    run_id = start(conn)
    run = service.open_run(conn, run_id)
    service.add_review_event(conn, run, run_id, {"event_type": "mark_reviewed", "source": "bank",
                                                  "transaction_id": "B4", "note": "+1-800 call vendor"})
    run = service.open_run(conn, run_id)
    text = results_csv(run_id, run["bank"], run["ledger"], run["outcome"], run["state"]).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    b4 = next(r for r in rows if r["transaction_id"] == "B4")
    assert b4["description"] == "'=HYPERLINK(\"http://evil\")"
    assert b4["review_note"] == "'+1-800 call vendor"
    assert b4["amount"] == "-2.00"  # numeric amount column is not prefixed
    assert next(r for r in rows if r["transaction_id"] == "L4")["description"] == "'@SUM(A1)"
    events = list(csv.DictReader(io.StringIO(events_csv(run["events"]).decode("utf-8-sig"))))
    assert events[0]["note"] == "'+1-800 call vendor"


def test_invalid_row_amount_text_is_neutralised_on_export(conn):
    bad_bank = make_csv("B9,2026-09-01,=1+1,EUR,X9,bad amount")
    run_id = start(conn, bank=bad_bank, ledger=make_csv("L9,2026-09-01,1.00,EUR,X9,ok"))
    run = service.open_run(conn, run_id)
    text = results_csv(run_id, run["bank"], run["ledger"], run["outcome"], run["state"]).decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["amount"] == "'=1+1"


@pytest.mark.parametrize("value, expected", [("=cmd", "'=cmd"), ("-5", "'-5"), ("plain", "plain"), (None, "")])
def test_neutralize_prefixes(value, expected):
    assert neutralize(value) == expected


def test_system_snapshot_is_stored_as_json(conn):
    run_id = start(conn)
    meta = store.load_run(conn, run_id)
    snapshot = json.loads(meta["system_results_json"])
    assert ["B1", "L1", "R1_STRONG_UNIQUE"] in snapshot["matches"]


def test_run_saved_under_older_rules_asks_for_a_rerun(conn):
    run_id = start(conn)
    conn.execute("update runs set rules_version = 'old', system_results_json = '{\"matches\": [], \"statuses\": {}}' "
                 "where id = ?", (run_id,))
    conn.commit()
    with pytest.raises(service.RulesChanged, match="Re-run"):
        service.open_run(conn, run_id)
    new_id = service.rerun(conn, run_id)
    rerun = service.open_run(conn, new_id)
    assert new_id != run_id and rerun["events"] == []
    assert "re-run of #" in rerun["meta"]["source_label"]


def test_stale_page_state_cannot_store_a_conflicting_decision(conn):
    run_id = start(conn)
    tab_a = service.open_run(conn, run_id)
    tab_b = service.open_run(conn, run_id)  # a second tab opened before tab A saves anything
    service.add_review_event(conn, tab_a, run_id, {"event_type": "confirm_match", "bank_id": "B2", "ledger_id": "L2"})
    with pytest.raises(ReviewError, match="already matched"):
        service.add_review_event(conn, tab_b, run_id, {"event_type": "confirm_match", "bank_id": "B2", "ledger_id": "L2"})
    assert service.open_run(conn, run_id)["state"].rejected == []


def test_pair_listed_only_on_the_ledger_side_can_be_confirmed(conn):
    bank = make_csv("B1,2026-09-01,10.00,EUR,R1,x", "B2,2026-09-01,10.00,EUR,R1,x")
    ledger = make_csv("L1,2026-09-01,10.00,EUR,ZZZ,x", "L2,2026-09-01,10.00,EUR,R1,x", "L3,2026-09-01,10.00,EUR,R1,x")
    run_id = start(conn, bank=bank, ledger=ledger)
    run = service.open_run(conn, run_id)
    assert "L1" not in {c.transaction_id for c in run["outcome"].results[("bank", "B1")].candidates}
    assert "B1" in {c.transaction_id for c in run["outcome"].results[("ledger", "L1")].candidates}
    service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B1", "ledger_id": "L1"})
    assert service.open_run(conn, run_id)["state"].matches["B1"] == "L1"


def test_confirm_note_appears_in_results_export(conn):
    run_id = start(conn)
    run = service.open_run(conn, run_id)
    service.add_review_event(conn, run, run_id, {"event_type": "confirm_match", "bank_id": "B5", "ledger_id": "L5",
                                                  "note": "0.50 bank fee"})
    run = service.open_run(conn, run_id)
    rows = list(csv.DictReader(io.StringIO(
        results_csv(run_id, run["bank"], run["ledger"], run["outcome"], run["state"]).decode("utf-8-sig"))))
    for tid in ("B5", "L5"):
        row = next(r for r in rows if r["transaction_id"] == tid)
        assert row["review_state"] == "confirmed_match" and row["review_note"] == "0.50 bank fee"


def test_snapshot_records_proposals_and_old_snapshots_still_open(conn):
    run_id = start(conn)
    meta = store.load_run(conn, run_id)
    snapshot = json.loads(meta["system_results_json"])
    assert ["B2", "L2", 3] in snapshot["proposals"]
    snapshot.pop("proposals")
    conn.execute("update runs set system_results_json = ? where id = ?", (json.dumps(snapshot), run_id))
    conn.commit()
    assert service.open_run(conn, run_id)["outcome"].proposals  # legacy snapshot without proposals still opens
