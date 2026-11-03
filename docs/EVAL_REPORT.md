# Evaluation report (synthetic or derived data only)

Dataset `recondesk-demo-v1`, seed 20261103. These are measurements on this data set. They are not real-world accuracy and say nothing about time savings.

## Default configuration (window = 2 day(s))

- Valid rows: bank 100, ledger 100
- Truth pairs (one-to-one): 72

### Automatic matches (applied without review)

- Automatic matches: 60 (correct 60, wrong 0)
- **Precision: 100.0%** (denominator: 60 automatic matches)
- **Recall: 83.3%** (denominator: 72 truth pairs)

### Probable matches (proposed for one-click review, never applied automatically)

- Proposals: 17 (correct 12, wrong 5)
- **Proposal precision: 70.6%** (denominator: 17 proposals)
  - high confidence: 11 proposed, 6 correct, precision 54.5%
  - medium confidence: 6 proposed, 6 correct, precision 100.0%
- Recall if every proposal were accepted: 100.0% (denominator: 72 truth pairs). A reviewer should reject the wrong ones, so treat this as an upper bound.

### Per truth category

| Category | Truth pairs | Auto-matched | Proposed | Left for manual review |
|---|---:|---:|---:|---:|
| clean_pair | 45 | 45 | 0 | 0 |
| delayed_in_window | 15 | 15 | 0 | 0 |
| delayed_outside_window | 6 | 0 | 6 | 0 |
| empty_reference_pair | 6 | 0 | 6 | 0 |

Not counted as truth pairs (no one-to-one answer): 2 duplicate groups, 0 batched (many-to-one) groups, 0 split (one-to-many) groups.

### Row status counts (valid rows, both sides)

| Status | Rows |
|---|---:|
| ambiguous | 8 |
| matched | 120 |
| probable | 34 |
| unmatched | 38 |

## Window sweep

| Window (days) | Auto matches | Auto precision | Auto recall | Proposals | Proposal precision |
|---:|---:|---:|---:|---:|---:|
| 0 | 45 | 100.0% | 62.5% | 30 | 83.3% |
| 1 | 54 | 100.0% | 75.0% | 23 | 78.3% |
| 2 | 60 | 100.0% | 83.3% | 17 | 70.6% |
| 3 | 63 | 100.0% | 87.5% | 14 | 64.3% |

## Reading these numbers

- Automatic matches need an exact, unique match; recall is lower by design.
- Probable matches are near-misses (typo, missing reference, small amount difference, up to 5 days apart). Some are wrong, which is why they need a human decision.
- Rule scores (3 = medium, 4 = high) are rule outputs, not calibrated probabilities.
