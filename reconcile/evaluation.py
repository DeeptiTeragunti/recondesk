"""Measure automatic matches and probable-match proposals against a truth manifest.

Precision is None (reported as N/A) when the system makes no matches of that kind: abstaining is not success.
Automatic matches and probable proposals are measured separately, because only the first are applied without
a human decision.
"""
from __future__ import annotations

from collections import Counter

from reconcile.matcher import WINDOW_MAX, WINDOW_MIN, reconcile
from reconcile.service import ingest_pair


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate(bank_bytes: bytes, ledger_bytes: bytes, manifest: dict, window_days: int) -> dict:
    bank, ledger = ingest_pair(bank_bytes, ledger_bytes)
    outcome = reconcile(bank.rows, ledger.rows, window_days)
    auto = {(b, l) for b, l, _ in outcome.matches}
    proposed = {(b, l) for b, l, _ in outcome.proposals}
    score_of = {(b, l): s for b, l, s in outcome.proposals}
    truth_rows = manifest["truth_pairs"]
    truth = {(p["bank_id"], p["ledger_id"]) for p in truth_rows}
    category_of = {(p["bank_id"], p["ledger_id"]): p["category"] for p in truth_rows}
    auto_tp, proposed_tp = auto & truth, proposed & truth
    per_category = {}
    for category in sorted(set(category_of.values())):
        pairs = [pair for pair, cat in category_of.items() if cat == category]
        per_category[category] = {
            "truth_pairs": len(pairs),
            "auto_matched": sum(1 for pair in pairs if pair in auto),
            "proposed": sum(1 for pair in pairs if pair in proposed),
        }
    by_confidence = {}
    for score, label in ((4, "high"), (3, "medium")):
        pairs = {p for p in proposed if score_of[p] == score}
        by_confidence[label] = {"proposed": len(pairs), "correct": len(pairs & truth),
                                "precision": _ratio(len(pairs & truth), len(pairs))}
    statuses = Counter(r.status for r in outcome.results.values())
    return {
        "window_days": window_days,
        "valid_bank_rows": sum(1 for r in bank.rows if r.valid),
        "valid_ledger_rows": sum(1 for r in ledger.rows if r.valid),
        "automatic_matches": len(auto),
        "true_positive": len(auto_tp),
        "false_positive": len(auto - truth),
        "precision": _ratio(len(auto_tp), len(auto)),
        "truth_pairs": len(truth),
        "recall": _ratio(len(auto_tp), len(truth)),
        "abstained_truth_pairs": len(truth - auto),
        "false_positive_pairs": sorted(auto - truth),
        "proposals": len(proposed),
        "proposal_true_positive": len(proposed_tp),
        "proposal_false_positive": len(proposed - truth),
        "proposal_precision": _ratio(len(proposed_tp), len(proposed)),
        "proposal_by_confidence": by_confidence,
        "recall_if_proposals_accepted": _ratio(len(auto_tp | proposed_tp), len(truth)),
        "per_category": per_category,
        "status_counts": dict(statuses),
        "excluded_groups": {name: len(manifest.get(name, [])) for name in
                            ("unresolvable_groups", "many_to_one_groups", "one_to_many_groups")},
    }


def sweep(bank_bytes: bytes, ledger_bytes: bytes, manifest: dict) -> list[dict]:
    return [evaluate(bank_bytes, ledger_bytes, manifest, w) for w in range(WINDOW_MIN, WINDOW_MAX + 1)]


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def render_markdown(default: dict, sweeps: list[dict], manifest: dict) -> str:
    d = default
    lines = [
        "# Evaluation report (synthetic or derived data only)",
        "",
        f"Dataset `{manifest['dataset_id']}`, seed {manifest['seed']}. These are measurements on this data set. "
        "They are not real-world accuracy and say nothing about time savings.",
        "",
        f"## Default configuration (window = {d['window_days']} day(s))",
        "",
        f"- Valid rows: bank {d['valid_bank_rows']}, ledger {d['valid_ledger_rows']}",
        f"- Truth pairs (one-to-one): {d['truth_pairs']}",
        "",
        "### Automatic matches (applied without review)",
        "",
        f"- Automatic matches: {d['automatic_matches']} (correct {d['true_positive']}, wrong {d['false_positive']})",
        f"- **Precision: {_pct(d['precision'])}** (denominator: {d['automatic_matches']} automatic matches)",
        f"- **Recall: {_pct(d['recall'])}** (denominator: {d['truth_pairs']} truth pairs)",
        "",
        "### Probable matches (proposed for one-click review, never applied automatically)",
        "",
        f"- Proposals: {d['proposals']} (correct {d['proposal_true_positive']}, wrong {d['proposal_false_positive']})",
        f"- **Proposal precision: {_pct(d['proposal_precision'])}** (denominator: {d['proposals']} proposals)",
    ]
    for label, stats in d["proposal_by_confidence"].items():
        lines.append(f"  - {label} confidence: {stats['proposed']} proposed, {stats['correct']} correct, "
                     f"precision {_pct(stats['precision'])}")
    lines += [
        f"- Recall if every proposal were accepted: {_pct(d['recall_if_proposals_accepted'])} "
        f"(denominator: {d['truth_pairs']} truth pairs). A reviewer should reject the wrong ones, so treat this as "
        "an upper bound.",
        "",
        "### Per truth category",
        "",
        "| Category | Truth pairs | Auto-matched | Proposed | Left for manual review |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, stats in d["per_category"].items():
        left = stats["truth_pairs"] - stats["auto_matched"] - stats["proposed"]
        lines.append(f"| {category} | {stats['truth_pairs']} | {stats['auto_matched']} | {stats['proposed']} | {left} |")
    groups = d["excluded_groups"]
    lines += [
        "",
        f"Not counted as truth pairs (no one-to-one answer): {groups['unresolvable_groups']} duplicate groups, "
        f"{groups['many_to_one_groups']} batched (many-to-one) groups, {groups['one_to_many_groups']} split "
        "(one-to-many) groups.",
        "",
        "### Row status counts (valid rows, both sides)",
        "",
        "| Status | Rows |",
        "|---|---:|",
    ]
    for status, count in sorted(d["status_counts"].items()):
        lines.append(f"| {status} | {count} |")
    lines += [
        "",
        "## Window sweep",
        "",
        "| Window (days) | Auto matches | Auto precision | Auto recall | Proposals | Proposal precision |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sweeps:
        lines.append(f"| {row['window_days']} | {row['automatic_matches']} | {_pct(row['precision'])} | "
                     f"{_pct(row['recall'])} | {row['proposals']} | {_pct(row['proposal_precision'])} |")
    lines += [
        "",
        "## Reading these numbers",
        "",
        "- Automatic matches need an exact, unique match; recall is lower by design.",
        "- Probable matches are near-misses (typo, missing reference, small amount difference, up to 5 days apart). "
        "Some are wrong, which is why they need a human decision.",
        "- Rule scores (3 = medium, 4 = high) are rule outputs, not calibrated probabilities.",
    ]
    return "\n".join(lines) + "\n"
