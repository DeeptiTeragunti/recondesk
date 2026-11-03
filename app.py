"""ReconDesk: local reconciliation exception workbench (Streamlit UI over the reconcile package)."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

import streamlit as st

from reconcile import exporter, service, store
from reconcile.matcher import RULE_DESCRIPTIONS, STATUSES, WINDOW_DEFAULT, WINDOW_MAX, WINDOW_MIN, confidence
from reconcile.money import format_minor
from reconcile.review import ReviewError

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "recondesk.sqlite3"
DEMO_BANK = ROOT / "data" / "demo" / "bank_export_demo.csv"
DEMO_LEDGER = ROOT / "data" / "demo" / "ledger_export_demo.csv"
BERKA_BANK = ROOT / "data" / "berka" / "bank_berka.csv"
BERKA_LEDGER = ROOT / "data" / "berka" / "ledger_derived.csv"
_MARKDOWN_SPECIALS = set("\\`*_{}[]()<>#+-.!|~$:")


def plain(text) -> str:
    """Escape Markdown so untrusted CSV text shows literally (no links, images or formatting)."""
    return "".join("\\" + ch if ch in _MARKDOWN_SPECIALS else ch for ch in str(text))


EXCEPTION_STATUSES = ["probable", "ambiguous", "unmatched", "invalid"]
CONFIRMABLE_RULES = ("R6_PROBABLE_MATCH", "R5_AMOUNT_DATE_SUGGESTION", "R2_STRONG_AMBIGUOUS", "R4_REF_AMOUNT_CONFLICT")


def db():
    return store.connect(DB_PATH)


def row_table(run) -> list[dict]:
    rows = []
    outcome = run["outcome"]
    state = run["state"]
    for source, input_rows in (("bank", run["bank"]), ("ledger", run["ledger"])):
        for row in input_rows:
            if row.valid:
                result = outcome.results[(source, row.transaction_id)]
                review = state.reviewed.get((source, row.transaction_id))
                manual = exporter._manual_partner(state, row)
                rows.append({
                    "source": source, "transaction_id": row.transaction_id, "date": row.date.isoformat(),
                    "amount": format_minor(row.amount_minor), "reference": row.reference,
                    "description": row.description, "system_status": result.status, "rule_id": result.rule_id,
                    "counterparts": ", ".join(c.transaction_id for c in result.candidates),
                    "review": "confirmed match" if manual else ("reviewed" if review else "open"),
                })
    for result in outcome.invalid:
        rows.append({
            "source": result.source, "transaction_id": result.transaction_id or f"row {result.row_number}",
            "date": "", "amount": "", "reference": "", "description": result.evidence,
            "system_status": "invalid", "rule_id": result.rule_id, "counterparts": "", "review": "n/a",
        })
    return rows


def sidebar(conn):
    st.sidebar.header("1. Load data")
    mode = st.sidebar.radio("Input", ["Synthetic demo (labelled)", "Real bank data (Berka, derived ledger)",
                                      "Upload two CSV files"])
    window = st.sidebar.slider("Date window (± days)", WINDOW_MIN, WINDOW_MAX, WINDOW_DEFAULT)
    st.sidebar.caption("Both files: positive = cash inflow, negative = cash outflow. EUR only. "
                       "Max 5 MB and 5,000 rows per file.")
    bank_name = "bank_export_demo.csv"
    ledger_name = "ledger_export_demo.csv"
    bank_bytes = ledger_bytes = None
    if mode.startswith("Synthetic"):
        st.sidebar.info("SYNTHETIC demo data, not a real bank or ledger.")
        if DEMO_BANK.exists() and DEMO_LEDGER.exists():
            bank_bytes, ledger_bytes = DEMO_BANK.read_bytes(), DEMO_LEDGER.read_bytes()
    elif mode.startswith("Real bank"):
        st.sidebar.info("Real, anonymised Czech bank transactions (account 8261, 1993-1998), CZK converted to EUR "
                        "at 25 CZK/EUR. The ledger is derived from them, so it is not a real ledger.")
        if BERKA_BANK.exists() and BERKA_LEDGER.exists():
            bank_name, ledger_name = BERKA_BANK.name, BERKA_LEDGER.name
            bank_bytes, ledger_bytes = BERKA_BANK.read_bytes(), BERKA_LEDGER.read_bytes()
        else:
            st.sidebar.error("data/berka/ files are missing. See README: Real bank data.")
    else:
        bank_file = st.sidebar.file_uploader("Bank export (CSV)", type=["csv"], key="bank_up")
        ledger_file = st.sidebar.file_uploader("Cash-ledger export (CSV)", type=["csv"], key="ledger_up")
        if bank_file and ledger_file:
            bank_name, bank_bytes = bank_file.name, bank_file.getvalue()
            ledger_name, ledger_bytes = ledger_file.name, ledger_file.getvalue()
    if mode.startswith("Synthetic"):
        source_label = "demo"
    elif mode.startswith("Real bank"):
        source_label = "berka"
    else:
        source_label = "upload"
    if st.sidebar.button("Validate and reconcile as a new run", type="primary", disabled=bank_bytes is None):
        try:
            run_id = service.start_run(conn, source_label=source_label, bank_name=bank_name, ledger_name=ledger_name,
                                       bank_bytes=bank_bytes, ledger_bytes=ledger_bytes, window_days=window)
            st.session_state["run_id"] = run_id
            st.sidebar.success(f"Created run {run_id}.")
        except service.InputRejected as exc:
            st.sidebar.error("Input rejected. Nothing was reconciled:")
            for message in exc.errors:
                st.sidebar.text(f"- {message}")

    st.sidebar.divider()
    st.sidebar.subheader("Saved runs")
    runs = store.list_runs(conn)
    if runs:
        labels = {r["id"]: f"#{r['id']} · {r['source_label']} · {r['created_at'][:19]} · window {r['window_days']}d"
                  for r in runs}
        choice = st.sidebar.selectbox("Reopen a run", list(labels), format_func=labels.get)
        if st.sidebar.button("Open saved run"):
            st.session_state["run_id"] = choice
    else:
        st.sidebar.caption("No saved runs yet.")


def overview(run):
    summary = run["summary"]
    bank, ledger = summary["bank"], summary["ledger"]
    cols = st.columns(5)
    cols[0].metric("Bank valid total (EUR)", format_minor(bank["total_minor"]))
    cols[1].metric("Ledger valid total (EUR)", format_minor(ledger["total_minor"]))
    cols[2].metric("Bank minus ledger (EUR)", format_minor(summary["difference_minor"]))
    cols[3].metric("Auto-matched pairs", len(run["outcome"].matches))
    cols[4].metric("Probable pairs to review", len(run["outcome"].proposals))
    st.warning("Equal totals do not prove reconciliation. Totals include every valid row, unmatched rows too. "
               "Invalid rows are excluded and listed on the Exceptions tab.")
    table = []
    for label, side in (("Bank", bank), ("Ledger", ledger)):
        table.append({
            "side": label, "rows": side["rows"], "valid": side["valid"], "invalid": side["invalid"],
            **{status: side["status_counts"][status] for status in STATUSES},
            "coverage (matched / valid)": "n/a" if side["coverage"] is None else f"{side['coverage'] * 100:.1f}%",
            "accounting check": "pass" if side["accounted"] else "FAIL",
        })
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.caption(f"Rules {summary['rules_version']} · window ±{summary['window_days']} day(s) · "
               f"source {run['meta']['source_label']} · bank {run['meta']['bank_filename']} "
               f"(sha256 {run['meta']['bank_sha256'][:12]}…) · ledger {run['meta']['ledger_filename']} "
               f"(sha256 {run['meta']['ledger_sha256'][:12]}…)")
    if run["state"].rejected:
        st.error("Some stored review events failed replay: " + "; ".join(run["state"].rejected))


def exceptions(run):
    rows = row_table(run)
    chosen = st.multiselect("Show statuses", [*STATUSES, "invalid"],
                            default=EXCEPTION_STATUSES)
    shown = [r for r in rows if r["system_status"] in chosen]
    st.dataframe(shown, hide_index=True, use_container_width=True)
    st.caption(f"{len(shown)} of {len(rows)} rows shown. Review state is separate from system status.")
    with st.expander("What the rule IDs mean"):
        for rule, text in RULE_DESCRIPTIONS.items():
            st.write(f"**{rule}**: {text}")


def inspect(conn, run_id, run):
    rows = [r for r in row_table(run) if r["system_status"] != "invalid"]
    if not rows:
        st.info("No valid rows to inspect.")
        return
    labels = {f"{r['source']}:{r['transaction_id']}": r for r in rows}
    key = st.selectbox("Row", list(labels), index=next((i for i, r in enumerate(labels.values())
                                                      if r["system_status"] in ("probable", "ambiguous", "unmatched")), 0))
    chosen = labels[key]
    result = run["outcome"].results[(chosen["source"], chosen["transaction_id"])]
    left, right = st.columns(2)
    with left:
        st.subheader(f"{chosen['source']} · {plain(chosen['transaction_id'])}")
        st.write(f"System status: **{chosen['system_status']}** ({result.rule_id})")
        st.text(f"Date {chosen['date']} · Amount {chosen['amount']} EUR · Reference {chosen['reference'] or '(empty)'}")
        st.text(chosen["description"])
        st.info(plain(result.evidence))
    with right:
        st.subheader("Candidates")
        if not result.candidates:
            st.write("No candidates.")
        else:
            st.dataframe([{
                "counterpart": c.transaction_id, "rule": c.rule_id, "confidence": confidence(c.score),
                "date delta (days, ledger − bank)": c.date_delta_days,
                "amount delta (EUR, ledger − bank)": format_minor(c.amount_delta_minor),
            } for c in result.candidates], hide_index=True, use_container_width=True)
    st.divider()
    st.subheader("Review decisions (local-user)")
    if chosen["review"] == "confirmed match":
        st.success("Already confirmed as a manual match.")
    note = st.text_area("Review note (required to mark reviewed, or to confirm a pair whose amounts differ)",
                        key=f"note_{key}")
    confirmable = [c.transaction_id for c in result.candidates if c.rule_id in CONFIRMABLE_RULES]
    if confirmable and chosen["review"] != "confirmed match":
        pick = st.selectbox("Confirm a candidate as a one-to-one match", confirmable)
        if st.button("Confirm match"):
            bank_id, ledger_id = (chosen["transaction_id"], pick) if chosen["source"] == "bank" \
                else (pick, chosen["transaction_id"])
            _try_event(conn, run_id, run, {"event_type": "confirm_match", "bank_id": bank_id, "ledger_id": ledger_id,
                                           "note": note})
    if st.button("Mark as reviewed"):
        _try_event(conn, run_id, run, {"event_type": "mark_reviewed", "source": chosen["source"],
                                       "transaction_id": chosen["transaction_id"], "note": note})


def _try_event(conn, run_id, run, event):
    try:
        service.add_review_event(conn, run, run_id, event)
        st.session_state["flash"] = "Review event saved."
        st.rerun()
    except ReviewError as exc:
        st.error(f"Rejected: {exc}")


def export(conn, run_id, run):
    results = exporter.results_csv(run_id, run["bank"], run["ledger"], run["outcome"], run["state"])
    events = exporter.events_csv(run["events"])
    st.download_button("Download results CSV", results, file_name=f"recondesk_run{run_id}_results.csv", mime="text/csv")
    st.download_button("Download review history CSV", events, file_name=f"recondesk_run{run_id}_reviews.csv",
                       mime="text/csv")
    st.caption("Text cells beginning with = + - @ are prefixed with an apostrophe for spreadsheet safety. "
               "Signed amounts are left numeric.")


def main():
    st.set_page_config(page_title="ReconDesk", layout="wide")
    st.title("ReconDesk: reconciliation exception workbench")
    st.caption("Local prototype. It identifies candidate matches and exceptions. It does not "
               "certify accounts or post entries.")
    with closing(db()) as conn:
        sidebar(conn)
        if flash := st.session_state.pop("flash", None):
            st.success(flash)
        run_id = st.session_state.get("run_id")
        if run_id is None:
            st.info("Choose the synthetic demo or upload two files in the sidebar, then validate and reconcile.")
            return
        try:
            run = service.open_run(conn, run_id)
        except service.RulesChanged as exc:
            st.warning(f"Run {run_id}: {exc}")
            if st.button("Re-run these inputs with the current rules (new run, no decisions carried over)"):
                st.session_state["run_id"] = service.rerun(conn, run_id)
                st.rerun()
            return
        except (KeyError, RuntimeError, service.InputRejected) as exc:
            st.error(f"Could not open run {run_id}: {exc}")
            return
        overview_tab, exceptions_tab, inspect_tab, export_tab = st.tabs(
            ["Overview", "Exceptions", "Inspect and review", "Export"])
        with overview_tab:
            overview(run)
        with exceptions_tab:
            exceptions(run)
        with inspect_tab:
            inspect(conn, run_id, run)
        with export_tab:
            export(conn, run_id, run)


main()
