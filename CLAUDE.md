# ReconDesk: project instructions

## Scope
Local reconciliation exception workbench for a portfolio demo. Two fixed-schema CSVs (bank export, cash-ledger export) in; candidate matches, exceptions, review notes and CSV exports out. Prototype only. It does not certify accounts, post journal entries, authenticate users, or call external services.

## Run commands
- Launch: `./start.ps1` (creates `.venv`, installs `requirements.lock.txt`, serves http://127.0.0.1:8501)
- Tests: `.venv\Scripts\python.exe -m pytest -q`
- Regenerate synthetic demo: `.venv\Scripts\python.exe -m scripts.generate_demo`
- Regenerate evaluation report: `.venv\Scripts\python.exe -m scripts.evaluate_demo`
- Bank side from OBP sandbox: `.venv\Scripts\python.exe -m scripts.obp_bank fetch|convert` (credentials from env vars only)
- Ledger side derived from a bank CSV: `.venv\Scripts\python.exe -m scripts.derive_ledger --bank <csv>`
- Outputs of real or sandbox data go in `data/private/` (git-ignored). Never commit them.

## Money and date conventions
- Money is parsed from text to integer cents (`reconcile/money.py`). No floats anywhere in matching or totals.
- Amount sign: inflow positive, outflow negative, in both files. Differences are always ledger minus bank.
- Dates are strict `YYYY-MM-DD` calendar dates. Date deltas are whole days.
- Currency is EUR only.

## Invariants (do not break)
1. Source files are never modified. Original bytes are stored in SQLite and hashed.
2. Invalid rows are kept and reported, never silently dropped or rounded.
3. Automatic matches require an equal normalised non-empty reference, equal signed amount, EUR, date within window, and uniqueness on both sides. Decided on the full candidate graph, so input order cannot change results.
4. An empty reference never auto-matches.
5. Probable matches (R6) and suggestions (R4/R5) are never automatic matches and never assert a cause. Only R1 auto-matches.
5a. Probable matches must be unique on both sides, like R1. Their score is a rule score, never a probability.
6. System statuses are immutable. Human decisions are events replayed on top, with one-to-one enforcement.
7. Reopening a run recomputes results from stored bytes and fails loudly if they differ from the stored snapshot.
8. Export neutralises spreadsheet formula prefixes in text cells only.
9. Identity label is `local-user`. This is not authentication and the logs are not tamper-proof.
10. No live model calls. Explanations are rule-based only.

## Layout
- `reconcile/` core package (ingest, matcher, review, store, service, exporter, evaluation)
- `app.py` Streamlit UI
- `scripts/` demo generator, evaluation runner, bank converters (`obp_bank`, `berka_bank`), `derive_ledger`
- `data/berka/` converted Berka bank file, derived ledger and its truth manifest
- `data/demo/` synthetic inputs and `truth_manifest.json`
- `tests/` pytest suite
- `docs/EVAL_REPORT.md` generated measurements
- `PLAN.md`, `README.md`, `DEMO_SCRIPT.md`
