# ReconDesk: local reconciliation exception workbench

A local prototype that matches a bank export against a cash-ledger export, shows exceptions with evidence, lets a local user record review decisions, and exports results. **Synthetic data only. It does not certify accounts or post journal entries.**

## Fresh install and run (Windows PowerShell)

Prerequisites: Python 3.11 or newer (`py -3 --version`). Installing Python packages needs network access; the app itself makes no network calls.

```powershell
git clone <this repository> FinanceAi
cd FinanceAi
./start.ps1
```

`start.ps1` creates `.venv`, installs the pins in `requirements.lock.txt`, and serves http://127.0.0.1:8501.

To run the checks:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

To regenerate the demo and the evaluation report:

```powershell
.venv\Scripts\python.exe -m scripts.generate_demo
.venv\Scripts\python.exe -m scripts.evaluate_demo
```

Tested here with Python 3.14.6 and Streamlit 1.45.1 on Windows 11. The 3.11 minimum is declared in `start.ps1` but has not been run on 3.11.

## Input contract

Two CSV files, UTF-8 (BOM accepted), with exactly these columns (extra columns are ignored):

| Column | Rule |
|---|---|
| `transaction_id` | required, unique within the file |
| `date` | required, `YYYY-MM-DD` calendar date |
| `amount` | required, signed decimal, at most 2 places, no separators or exponents |
| `currency` | required, `EUR` only |
| `reference` | may be empty; empty references never auto-match |
| `description` | may be empty |

Sign convention for both files: **positive = cash inflow, negative = cash outflow.** Limits: 5 MB and 5,000 rows per file. Filenames are stored as display text only and are never used as paths.

Row-level errors (malformed date, bad amount, non-EUR, duplicate ID, missing field, wrong cell count) make that row **invalid**. It is shown and excluded from matching and totals. Nothing is silently dropped or rounded. File-level errors (missing columns, too large, not UTF-8) reject the whole upload.

## Rules (version `2026.11.2`)

- **Normalisation:** reference is upper-cased and whitespace-collapsed. Digits and punctuation are kept.
- **R1 strong, unique:** equal normalised non-empty reference, equal signed amount, EUR, dates within the window (default ±2 days, configurable 0–3), and the pair is the only strong edge for both rows. **Auto-matched.**
- **R2 strong, ambiguous:** strong candidates exist but not uniquely on both sides. Not matched.
- **R6 probable match (status `probable`):** a near-miss that is unique on both sides. The reference may be equal, a typo of the other (up to 2 character edits) or missing; the amount may be equal or within max(2.00 EUR, 1%); the dates may be up to 5 days apart; the signs must agree. Each criterion earns points (reference 2/1/0, amount 2/1, date 1/0); 4 points is **high** and 3 is **medium** confidence. This is a rule score, not a probability. Probable matches are proposed for one-click review and **never applied automatically**.
- **R4 reference/amount conflict:** equal reference, different amount, dates inside the window. Flagged only; no cause is asserted. (Before 2026.11.2 this compared the whole file, so a monthly payment was flagged against every other month.)
- **R5 amount/date suggestion:** equal amount, date within window, no strong reference, not unique enough to propose. Suggested only.
- **R3 no candidate:** nothing found.
- **R0 invalid:** failed input validation.

Auto-matching is decided on the whole candidate graph at once, so no greedy or input-order choice is involved. A test shuffles inputs to check this. Each row can appear in at most one accepted match.

Each result carries its rule ID, counterpart IDs, original values, date and amount deltas (ledger minus bank), and a rule-based evidence sentence. There are **no scores or probabilities**.

## Workflow

1. **Load:** choose the labelled synthetic demo or upload two CSVs. Validation errors are shown before anything runs.
2. **Overview:** valid totals, bank-minus-ledger difference, match coverage, and an accounting check (matched + ambiguous + unmatched equals the total). The UI states that equal totals do not prove reconciliation.
3. **Exceptions:** filter by status, with rule descriptions.
4. **Inspect and review:** side-by-side evidence and candidates, with confidence for probable ones. The local user can confirm a probable, suggested, ambiguous or conflicting candidate as a one-to-one match, or mark a row reviewed with a required note. Constraints: both rows must be valid, neither may already be matched, and the pair must be a candidate. If the amounts differ (a fee, say), the confirmation needs a note explaining the difference.
   Runs saved under an older rules version say so when reopened and offer to re-run their stored inputs as a new run (decisions are not carried over).
5. **Export:** results CSV (system status and review state kept as separate columns) and review-history CSV. Text cells starting with `= + - @` or tab/CR get an apostrophe prefix. Signed amount columns are left numeric.

Runs are saved in `data/recondesk.sqlite3`: original bytes, SHA-256 hashes, rules version, window, a system-results snapshot, and timestamped review events (identity `local-user`). Reopening a run recomputes results from the stored bytes and fails if they differ from the snapshot. A new upload always creates a new run, so decisions are never reused silently.

## Architecture

```
app.py                 Streamlit UI (local, 127.0.0.1)
reconcile/money.py     text -> integer cents, reference normalisation
reconcile/ingest.py    strict CSV parsing, row and file errors, hashes
reconcile/matcher.py   deterministic rules R0-R6, totals and accounting check
reconcile/review.py    review events replayed over system results, one-to-one checks
reconcile/store.py     SQLite schema and access
reconcile/service.py   ingest -> reconcile -> persist; reopen with integrity check; re-run
reconcile/exporter.py  CSV export with formula neutralisation
reconcile/evaluation.py precision/recall for automatic matches and proposals
scripts/               demo generator, evaluation runner, OBP and Berka bank converters, ledger derivation
tests/                 pytest suite (96 tests)
```

Tradeoffs: SQLite and Streamlit keep it to two pinned dependencies and no server. Money uses integer cents rather than `Decimal` objects because the input grammar is strict and the arithmetic is only addition and comparison. Results are recomputed on reopen (deterministic, verified against a snapshot) rather than trusted from storage.

## Measured results (synthetic fixture only)

From `docs/EVAL_REPORT.md`, default window ±2 days, 100 bank and 100 ledger rows, 72 truth pairs:

- Automatic matches: 60. True positives 60, false positives 0.
- **Automatic-match precision: 100.0%** (denominator 60).
- **Recall: 83.3%** (denominator 72 truth pairs). The 12 abstentions are 6 `delayed_outside_window` pairs and 6 `empty_reference_pair` pairs. Abstaining on these is the designed behaviour.
- Probable proposals: 17, of which 12 are correct, **proposal precision 70.6%** (denominator 17). The 5 wrong ones are high-confidence proposals between rows the manifest says are unrelated (shared reference, amount off by cents). That is why proposals need a human decision.
- A zero-match system reports precision as N/A and recall as 0%, not as success (tested).

Window sweep: 0 days → recall 62.5%; 1 → 75.0%; 2 → 83.3%; 3 → 87.5%, all with 100% precision on this fixture. These are measurements on one seeded fixture, not real-world accuracy.

## Synthetic data

`data/demo/` is generated by `scripts/generate_demo.py` (seed `20261103`). Vendor names are invented. `data/demo/truth_manifest.json` is written by the same generator, and it assigns each row a category. Some categories have no counterpart by design: conflicting reference, same amount with unrelated reference, shared reference with a different amount, and bank-only or ledger-only rows. Two duplicate reference/amount groups are listed as `unresolvable_groups` and excluded from recall because the true pairing is not asserted. The generator is reproducible and a test checks that.

## Bank and ledger data pipeline (sandbox API)

Two scripts produce a bank file from an API and a ledger file to reconcile it against. Outputs go to `data/private/`, which is git-ignored.

**Bank side: `scripts/obp_bank.py`** (Open Bank Project sandbox, standard library only)

1. Register a free developer account at the [Open Bank Project sandbox](https://apisandbox.openbankproject.com) and request a consumer key. Use a sandbox test user, never a real bank login.
2. Set the credentials in your own shell (they are read from the environment and never written to disk):

```powershell
$env:OBP_USERNAME = "..."; $env:OBP_PASSWORD = "..."; $env:OBP_CONSUMER_KEY = "..."
$env:OBP_BANK_ID = "..."; $env:OBP_ACCOUNT_ID = "..."   # optional: OBP_VIEW_ID (default owner), OBP_BASE_URL, OBP_API_VERSION
.venv\Scripts\python.exe -m scripts.obp_bank fetch   --out data/private/obp_raw.json
.venv\Scripts\python.exe -m scripts.obp_bank convert --raw data/private/obp_raw.json --out data/private/bank_obp.csv
```

`fetch` logs in with DirectLogin and pages through `GET /obp/<version>/banks/{bank}/accounts/{account}/{view}/transactions`. `convert` maps `id`, `details.completed` (or `posted`, taken as a UTC date), `details.value.amount`, `details.value.currency` and `details.description` to the six ReconDesk columns. Rows that do not fit (non-EUR, more than two decimals, duplicate ID, no date) go to `bank_obp.rejects.csv` with a reason. Nothing is rounded or silently dropped.

Options: `--reference-from description|id|none` (the API has no separate reference field; the default uses the description) and `--flip-sign` if the API reports outflows as positive. Check a few rows against the sandbox UI before trusting the sign.

**Ledger side: `scripts/derive_ledger.py`**

```powershell
.venv\Scripts\python.exe -m scripts.derive_ledger --bank data/private/bank_obp.csv --out-dir data/private --seed 7
.venv\Scripts\python.exe -m scripts.evaluate_demo --bank data/private/bank_obp.csv --ledger data/private/ledger_derived.csv --manifest data/private/truth_manifest_derived.json --report data/private/EVAL_REPORT_derived.md
```

It builds a deliberately messy ledger from the bank rows, the way a bookkeeper's records differ from a bank feed. Ledger descriptions are written in the ledger's own words ("Interest income", "Supplier payment"), never copied from the bank. Each bank row gets one seeded, labelled treatment: clean copy with case and spacing noise (52%), posted 1-2 days off (16%), posted 3-5 days off (5%), reference left blank (7%), reference mistyped (4%), fee netted (4%), wrong sign (1%), never booked (5%), booked twice (2%), batched with 1-2 other rows into one entry (3%) or split into two entries (1%). It also adds about 5% ledger-only entries (accruals, outstanding cheques) sized like the account's own movements. The truth manifest records the one-to-one pairs and lists duplicate, batched and split groups separately, because they have no one-to-one answer.

Run on the shipped 100-row demo bank file (seed 7): 70 automatic matches, 0 wrong, precision 100.0%, recall 76.9% (91 truth pairs); 18 probable proposals, 0 wrong; 96.7% of truth pairs would be covered if every proposal were accepted.

**What this does and does not prove.** The bank rows come from a sandbox (fictional test data) and the ledger is derived from them, so the truth is known by construction. That makes the pipeline and the measurement repeatable. It is still not real-world accuracy, because real ledgers differ from bank feeds in ways this script does not model. A real measurement needs a real ledger and a hand-labelled answer key.

The fetch step has not been run against the live sandbox from this repository. The response mapping follows the OBP resource docs and is tested against a hand-written sample (`tests/fixtures/obp_transactions_sample.json`).

## Real bank data: PKDD'99 Czech bank dataset (Berka)

The dataset is real, anonymised bank transactions from a Czech bank, 1993–1998, prepared by Petr Berka and Marta Sochorová for the PKDD'99 Discovery Challenge (1,056,320 transactions). It is not shipped with this repository.

Download (about 68 MB) from the copy in the `ldaniel/fgv-exploratory-data-analysis` repository on GitHub into `data/private/berka/` (git-ignored), then convert one account:

```powershell
curl.exe -L -o data/private/berka/trans.asc https://raw.githubusercontent.com/ldaniel/fgv-exploratory-data-analysis/master/data/trans.asc
.venv\Scripts\python.exe -m scripts.berka_bank        # default: busiest account (8261, 675 rows)
.venv\Scripts\python.exe -m scripts.derive_ledger --bank data/private/berka/bank_berka.csv --out-dir data/private/berka --seed 7
.venv\Scripts\python.exe -m scripts.evaluate_demo --bank data/private/berka/bank_berka.csv --ledger data/private/berka/ledger_derived.csv --manifest data/private/berka/truth_manifest_derived.json --report data/private/berka/EVAL_REPORT_berka.md
```

Downloaded file used here: `trans.asc`, 68,350,257 bytes, SHA-256 `111c70dc6626411d1d6547245c318b507e0c2892093f092d579468b71619bd3e`. The repository carries an Apache-2.0 licence; the data itself comes from the original research release, so check its terms before redistributing.

Conversion (`scripts/berka_bank.py`):
- `PRIJEM` (credit) is an inflow; `VYDAJ` and `VYBER` (withdrawals) are outflows. Dates `YYMMDD` become `19YY-MM-DD`.
- Reference is the partner's bank and account for transfers, else the category code (`k_symbol`), else empty. The dataset has no invoice numbers.
- **Amounts are in CZK and are converted to EUR** at a fixed, illustrative 25.00 CZK per EUR (not a historical rate), rounded half-even to cents. This is a documented preprocessing step outside ReconDesk; the original CZK amount is kept in each description.

Measured on account 8261 with the messy ledger derived at seed 7 (690 rows, window ±2 days):

- Automatic matches: 406, 0 wrong. **Precision 100.0%** (denominator 406), **recall 67.4%** (denominator 602 truth pairs).
- Probable proposals: 148 (46 high, 102 medium confidence), 0 wrong. **Proposal precision 100.0%** (denominator 148).
- If every proposal were accepted, 92.0% of truth pairs would be covered. The rest are left for manual review: wrong-sign bookings (never matched by design), and near-misses that were not unique.
- Not counted as truth pairs: 15 duplicate bookings, 6 batched entries and 9 split entries.
- At window 0, 6 automatic matches count as wrong. All six pair a bank row with one copy of its own duplicated ledger booking; the manifest does not say which copy is true, so they are scored as wrong. They are a labelling artifact, not a matching error.

The bank side is real; the ledger is still derived from it, so this is not a measure of real-world accuracy.

## Known limitations and unsupported cases

- Single user, no authentication. `local-user` is a label, and the event log is not tamper-proof.
- Many-to-one, one-to-many, FX, fees, netting, partial settlement, and split payments are **not** handled. They will show as unmatched or ambiguous.
- Only the fixed schema is accepted; there is no column mapper, PDF, or OCR.
- The fixture is small and generated by the same author as the rules. Its numbers show the rules behave as designed on this data, not how they behave on real exports.
- Equal totals are never treated as proof of reconciliation.
- No live AI explanation is included. The "explanations" are the rule-based evidence sentences. Nothing is sent to a model.
- No browser end-to-end run is recorded in this README. The review flow was exercised with Streamlit's headless test harness.

## Competitor positioning

Established products already cover this space. BlackLine's [Transaction Matching](https://www.blackline.com/products/financial-close/transaction-matching/) imports multiple sources, applies business rules and identifies exceptions, and Numeric's [Cash Matching](https://www.numeric.io/product/cash-matching) offers one-to-one through many-to-many matching with rule suggestions (both as described in `research/2026-project-market-audit.md`). ReconDesk is not a substitute for those platforms. Its narrower claim is a local, single-user prototype that keeps AI out of the matching path, shows rule evidence for every row, and records review decisions beside immutable system results. That is a positioning hypothesis, not a proven advantage, and it has not been validated with customers.

## Verification

- `pytest`: 102 tests pass (money, ingest, matcher rules R1-R6, review constraints, persistence, export escaping, evaluation, bank and ledger pipelines).
- Browser smoke run (in-app browser): synthetic demo → reconcile → inspect ambiguous row → review note → reload and reopen the saved run → export buttons present. Downloads were not clicked; export content is covered by tests.
- ECC 2.2.3 reviews: `python-reviewer` (no wrong-match or wrong-total bugs; review-handling issues fixed) and `security-reviewer` (nothing above low severity; low findings fixed). ECC is a development aid, not a security sandbox or proof of correctness.

## Documents

- `PLAN.md`: milestones and decisions
- `CLAUDE.md`: project scope, invariants, and conventions
- `DEMO_SCRIPT.md`: three-minute demo
- `docs/EVAL_REPORT.md`: generated measurements
