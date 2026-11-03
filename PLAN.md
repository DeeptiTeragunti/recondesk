# ReconDesk plan

Goal: a tested local prototype that reduces review effort for bank-vs-ledger exceptions. Not a production financial system.

## Milestones (status)
1. Preflight: Python 3.14.6, git, Claude CLI logged in, ECC plugin 2.2.3. Pinned Streamlit 1.45.1 and pytest 8.3.5 in `.venv`. **Done.**
2. Input contract and validation: strict CSV ingest, row-level errors, file limits, hashes. **Done.**
3. Deterministic matcher: strong unique auto-match (R1), ambiguity (R2), windowed conflict (R4), suggestions (R5), invalid exclusion. **Done.**
4. Synthetic fixture with truth manifest, generator, evaluation with denominators. **Done.**
5. Vertical slice: load → reconcile → inspect → review → export, with SQLite persistence. **Done.**
6. Review constraints, reopen with integrity check, CSV export with formula neutralisation. **Done.**
7. Documentation: README, DEMO_SCRIPT, EVAL_REPORT. **Done.**
8. Realistic data (added on request): Open Bank Project sandbox fetch/convert script, real Berka bank data converter, messy derived ledger with truth manifest. **Done.** The live OBP fetch has not been run from this repository; it needs the user's sandbox credentials.
9. Probable matches (added on request): R6 near-miss proposals with high/medium rule scores, never automatic; confirm with a note when amounts differ; rules-version check and re-run for older saved runs. **Done.**
10. Verification: **Done.** 102 pytest tests pass. Browser smoke run in the in-app browser on the synthetic demo: reconcile → inspect an ambiguous row (B0076) → review note → page reload and reopen of the saved run → note still present → export buttons present (files not downloaded; export content checked in code). ECC 2.2.3 `python-reviewer` and `security-reviewer` agents run; their findings were fixed (stale review state, one-sided candidate check, confirm notes missing from export, oversized CSV cell, Markdown rendering of untrusted text, https-only OBP URL, connection leak). The open item is whether `data/berka/` may be committed.

## Deferred
- Optional live AI explanation adapter (not built; explanations are rule-based).
- Visual polish beyond Streamlit defaults.

## Key decisions
- Rules rather than probabilities. Precision and recall are reported with denominators, separately for automatic matches and proposals.
- Ambiguity is detected on the whole candidate graph before any pair is accepted, for R1 and R6 alike, so input order never matters.
- Review decisions are events replayed over immutable system results.
- Reopen recomputes results from stored bytes and compares them with a stored snapshot; a rules change asks for a re-run instead of silently showing different results.
- Money is integer cents parsed with a strict regex; `Decimal` is used only for the CZK-to-EUR preprocessing of the Berka data.

## Explicitly out of scope
Many-to-one and one-to-many (batches, splits), FX, netting, partial settlement, PDF/OCR, live bank integration, authentication, a multi-user audit trail, posting entries, live model calls.
