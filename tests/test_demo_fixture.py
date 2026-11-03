"""Checks the shipped synthetic demo against its independent truth manifest."""
import json
from pathlib import Path

from reconcile.evaluation import evaluate
from reconcile.matcher import WINDOW_DEFAULT

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"


def load():
    bank = (DEMO / "bank_export_demo.csv").read_bytes()
    ledger = (DEMO / "ledger_export_demo.csv").read_bytes()
    manifest = json.loads((DEMO / "truth_manifest.json").read_text(encoding="utf-8"))
    return bank, ledger, manifest


def test_demo_is_labelled_synthetic_with_about_100_rows_per_side():
    _, _, manifest = load()
    assert manifest["synthetic"] is True
    assert 95 <= manifest["row_counts"]["bank"] <= 105
    assert 95 <= manifest["row_counts"]["ledger"] <= 105


def test_default_window_has_no_false_positive_automatic_matches():
    bank, ledger, manifest = load()
    result = evaluate(bank, ledger, manifest, WINDOW_DEFAULT)
    assert result["false_positive"] == 0
    assert result["automatic_matches"] > 0
    assert result["precision"] == 1.0
    # Outside-window and empty-reference pairs must be abstained on, which caps recall below 100%.
    assert result["per_category"]["delayed_outside_window"]["auto_matched"] == 0
    assert result["per_category"]["empty_reference_pair"]["auto_matched"] == 0
    assert result["per_category"]["clean_pair"]["auto_matched"] == result["per_category"]["clean_pair"]["truth_pairs"]


def test_generator_is_reproducible(tmp_path):
    from scripts.generate_demo import main
    main(tmp_path)
    assert (tmp_path / "bank_export_demo.csv").read_bytes() == (DEMO / "bank_export_demo.csv").read_bytes()
    assert (tmp_path / "ledger_export_demo.csv").read_bytes() == (DEMO / "ledger_export_demo.csv").read_bytes()
