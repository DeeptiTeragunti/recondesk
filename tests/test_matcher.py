import random
from datetime import date, timedelta

import pytest

from reconcile.ingest import ingest_csv
from reconcile.matcher import RULES_VERSION, reconcile, summarize

HEADER = "transaction_id,date,amount,currency,reference,description"


def rows(source, *lines):
    text = HEADER + "\n" + "\n".join(lines) + "\n"
    result = ingest_csv(source, text.encode("utf-8"))
    assert result.accepted, result.file_errors
    return result.rows


def run(bank_lines, ledger_lines, window=2):
    bank, ledger = rows("bank", *bank_lines), rows("ledger", *ledger_lines)
    return bank, ledger, reconcile(bank, ledger, window)


def status(outcome, source, tid):
    return outcome.results[(source, tid)]


def test_clean_pair_is_auto_matched_with_rule_and_evidence():
    _, _, out = run(["B1,2026-09-01,-12.50,EUR,INV-1,Vendor"], ["L1,2026-09-01,-12.50,EUR,inv-1,Vendor"])
    assert out.matches == [("B1", "L1", "R1_STRONG_UNIQUE")]
    result = status(out, "bank", "B1")
    assert result.status == "matched" and result.rule_id == "R1_STRONG_UNIQUE"
    assert "unique on both sides" in result.evidence
    assert result.candidates[0].date_delta_days == 0 and result.candidates[0].amount_delta_minor == 0


def test_no_row_is_reused_across_matches():
    bank = [f"B{i},2026-09-0{1 + i % 3},{-i}.00,EUR,REF{i % 7},d" for i in range(1, 25)]
    ledger = [f"L{i},2026-09-0{1 + i % 3},{-i}.00,EUR,REF{i % 7},d" for i in range(1, 25)]
    _, _, out = run(bank, ledger)
    bank_ids = [b for b, _, _ in out.matches]
    ledger_ids = [l for _, l, _ in out.matches]
    assert len(bank_ids) == len(set(bank_ids)) and len(ledger_ids) == len(set(ledger_ids))


def test_ambiguity_on_bank_side_blocks_automatic_match():
    # B1 has two strong ledger candidates. Neither pair may be auto-matched.
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,X1,d"],
                    ["L1,2026-09-01,-5.00,EUR,X1,d", "L2,2026-09-02,-5.00,EUR,X1,d"])
    assert out.matches == []
    assert status(out, "bank", "B1").status == "ambiguous"
    assert status(out, "ledger", "L1").status == "ambiguous"


def test_ambiguity_on_ledger_side_blocks_automatic_match():
    # Both bank rows point at L1, so the single ledger row is ambiguous.
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,X1,d", "B2,2026-09-01,-5.00,EUR,X1,d"],
                    ["L1,2026-09-01,-5.00,EUR,X1,d"])
    assert out.matches == []
    assert status(out, "ledger", "L1").status == "ambiguous"
    assert status(out, "bank", "B1").status == "ambiguous"


def test_empty_reference_never_auto_matches_but_unique_pair_is_proposed():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,,d"], ["L1,2026-09-01,-5.00,EUR,,d"])
    assert out.matches == []
    result = status(out, "bank", "B1")
    assert result.status == "probable" and result.rule_id == "R6_PROBABLE_MATCH"
    assert out.proposals == [("B1", "L1", 3)]  # medium: no reference to confirm it
    assert "reference missing on both sides" in result.evidence


def test_empty_reference_with_two_candidates_is_only_suggested():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,,d"], ["L1,2026-09-01,-5.00,EUR,,d", "L2,2026-09-02,-5.00,EUR,,d"])
    assert out.matches == [] and out.proposals == []
    result = status(out, "bank", "B1")
    assert result.status == "unmatched" and "none unique" in result.evidence
    assert {c.transaction_id for c in result.candidates} == {"L1", "L2"}


def test_same_amount_with_unrelated_reference_is_suggestion_not_match():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,INV-1,d"], ["L1,2026-09-01,-5.00,EUR,TRF-99,d"])
    assert out.matches == []
    assert status(out, "bank", "B1").rule_id == "R5_AMOUNT_DATE_SUGGESTION"


def test_shared_reference_with_large_amount_difference_is_flagged_only():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,INV-1,d"], ["L1,2026-09-01,-50.00,EUR,INV-1,d"])
    assert out.matches == [] and out.proposals == []
    result = status(out, "bank", "B1")
    assert result.rule_id == "R4_REF_AMOUNT_CONFLICT" and result.status == "unmatched"
    assert "cause is not asserted" in result.evidence
    assert result.candidates[0].amount_delta_minor == -4500


def test_reference_conflict_is_only_flagged_inside_the_date_window():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,UROK,d"], ["L1,2026-09-15,-50.00,EUR,UROK,d"])
    assert status(out, "bank", "B1").rule_id == "R3_NO_CANDIDATE"  # 14 days apart: unrelated months


def test_small_fee_difference_is_a_high_confidence_proposal():
    _, _, out = run(["B1,2026-09-01,-500.00,EUR,INV-1,d"], ["L1,2026-09-01,-501.50,EUR,inv-1,d"])
    assert out.matches == [] and out.proposals == [("B1", "L1", 4)]
    result = status(out, "bank", "B1")
    assert "amount differs by -1.50" in result.evidence and "high confidence" in result.evidence


def test_reference_typo_is_proposed_but_unrelated_reference_is_not():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,INV-2026-1001,d"], ["L1,2026-09-01,-5.00,EUR,INV-2026-1007,d"])
    assert out.proposals == [("B1", "L1", 4)]
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,INV-2026-1001,d"], ["L1,2026-09-01,-5.00,EUR,TRF-99,d"])
    assert out.proposals == [] and status(out, "bank", "B1").rule_id == "R5_AMOUNT_DATE_SUGGESTION"


@pytest.mark.parametrize("days, proposed", [(3, True), (5, True), (6, False)])
def test_late_posting_is_proposed_up_to_five_days(days, proposed):
    ledger_day = (date(2026, 9, 1) + timedelta(days=days)).isoformat()
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,X1,d"], [f"L1,{ledger_day},-5.00,EUR,X1,d"])
    assert out.matches == []
    assert (out.proposals != []) is proposed


def test_opposite_sign_is_never_proposed():
    _, _, out = run(["B1,2026-09-01,-1.00,EUR,X1,d"], ["L1,2026-09-01,1.00,EUR,X1,d"])
    assert out.proposals == [] and out.matches == []


@pytest.mark.parametrize("offset, matched", [(0, True), (1, True), (2, True), (3, False), (-2, True), (-3, False)])
def test_date_window_boundaries_default_two_days(offset, matched):
    ledger_day = date(2026, 9, 1) + timedelta(days=offset)
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,X1,d"], [f"L1,{ledger_day.isoformat()},-5.00,EUR,X1,d"])
    assert (out.matches != []) is matched


def test_window_zero_means_same_day_only_and_bad_window_rejected():
    _, _, out = run(["B1,2026-09-01,-5.00,EUR,X1,d"], ["L1,2026-09-02,-5.00,EUR,X1,d"], window=0)
    assert out.matches == []
    with pytest.raises(ValueError):
        run(["B1,2026-09-01,-5.00,EUR,X1,d"], [], window=4)


def test_input_order_does_not_change_pairs_or_statuses():
    bank = [
        "B1,2026-09-01,-5.00,EUR,X1,d", "B2,2026-09-02,-7.00,EUR,X2,d",
        "B3,2026-09-03,-9.00,EUR,X3,d", "B4,2026-09-03,-9.00,EUR,X3,d",
    ]
    ledger = [
        "L1,2026-09-01,-5.00,EUR,X1,d", "L2,2026-09-02,-7.00,EUR,X2,d",
        "L3,2026-09-03,-9.00,EUR,X3,d", "L4,2026-09-03,-9.00,EUR,X3,d", "L5,2026-09-04,-3.00,EUR,,d",
        "L6,2026-09-08,-4.00,EUR,INV-77,d",
    ]
    bank.append("B5,2026-09-05,-4.00,EUR,INV-77,d")  # 3 days late: a probable proposal
    baseline = run(bank, ledger)[2]
    assert baseline.proposals == [("B5", "L6", 4)]
    expected = (baseline.matches, baseline.proposals, {k: (v.status, v.rule_id) for k, v in baseline.results.items()})
    for seed in range(5):
        rng = random.Random(seed)
        b, l = bank[:], ledger[:]
        rng.shuffle(b)
        rng.shuffle(l)
        shuffled = run(b, l)[2]
        assert (shuffled.matches, shuffled.proposals,
                {k: (v.status, v.rule_id) for k, v in shuffled.results.items()}) == expected


def test_invalid_rows_are_excluded_from_matching_and_totals():
    bank = rows("bank", "B1,2026-09-01,-5.00,EUR,X1,d", "B2,2026-09-01,not-a-number,EUR,X2,d")
    ledger = rows("ledger", "L1,2026-09-01,-5.00,EUR,X1,d")
    out = reconcile(bank, ledger, 2)
    assert out.matches == [("B1", "L1", "R1_STRONG_UNIQUE")]
    assert out.invalid[0].status == "invalid" and out.invalid[0].rule_id == "R0_INVALID_ROW"
    summary = summarize(bank, ledger, out)
    assert summary["bank"]["valid"] == 1 and summary["bank"]["invalid"] == 1
    assert summary["bank"]["total_minor"] == -500


def test_totals_account_for_every_valid_row_including_unmatched():
    bank_lines = ["B1,2026-09-01,-5.00,EUR,X1,d", "B2,2026-09-01,10.00,EUR,X2,d", "B3,2026-09-01,-1.25,EUR,,d"]
    ledger_lines = ["L1,2026-09-01,-5.00,EUR,X1,d", "L2,2026-09-05,4.00,EUR,X9,d"]
    bank, ledger, out = run(bank_lines, ledger_lines)
    summary = summarize(bank, ledger, out)
    assert summary["bank"]["accounted"] and summary["ledger"]["accounted"]
    assert summary["bank"]["total_minor"] == 375 and summary["ledger"]["total_minor"] == -100
    assert summary["difference_minor"] == 475
    assert summary["bank"]["coverage"] == pytest.approx(1 / 3)
    assert summary["rules_version"] == RULES_VERSION


def test_zero_match_system_reports_no_precision_denominator():
    from reconcile.evaluation import evaluate
    manifest = {"truth_pairs": [{"bank_id": "B1", "ledger_id": "L1", "category": "clean_pair"}]}
    bank = (HEADER + "\nB1,2026-09-01,-5.00,EUR,,d\n").encode()
    ledger = (HEADER + "\nL1,2026-09-01,-5.00,EUR,,d\n").encode()
    result = evaluate(bank, ledger, manifest, 2)
    assert result["automatic_matches"] == 0
    assert result["precision"] is None  # reported as N/A, not as success
    assert result["recall"] == 0.0
    assert result["proposals"] == 1 and result["proposal_precision"] == 1.0  # empty-ref pair is only proposed
