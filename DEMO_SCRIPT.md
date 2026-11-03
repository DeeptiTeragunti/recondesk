# Three-minute demo script

Setup: run `./start.ps1` and open http://127.0.0.1:8501. Expand the sidebar.

**0:00–0:20: framing.** "This is ReconDesk, a local tool that matches a bank export against a cash ledger. It matches what it can prove, proposes near-misses for a person to confirm, and flags the rest with evidence. Nothing leaves this machine."

**0:20–0:45: load and reconcile.** Choose **Real bank data (Berka, derived ledger)**. Point at the note: real anonymised Czech bank transactions, CZK converted to EUR, ledger derived from them. Leave the window at ±2 days and click **Validate and reconcile as a new run**.

**0:45–1:10: Overview.** Show the five metrics: bank total, ledger total, the 5,568.74 difference, 406 automatic matches and 148 probable pairs. Read the warning aloud: equal totals do not prove reconciliation. Point at the accounting check, which passes because every valid row is counted in exactly one status.

**1:10–1:40: Exceptions.** Filter to **probable** and **unmatched**. Open "What the rule IDs mean" and contrast R1 (exact and unique: automatic) with R6 (near-miss: proposed, never automatic). Mention that scores are rule scores, not probabilities.

**1:40–2:20: Inspect and review.** Pick a **probable** row. Read its evidence, for example "reference missing on both sides; amount equal". Click **Confirm match**. Then pick a probable row whose amount differs by a fee and click **Confirm match** with an empty note: it is refused. Type "bank fee" and confirm again. Finally mark an unmatched row as reviewed with a note.

**2:20–2:45: Reopen and export.** Choose the run under **Saved runs** and reopen it: the decisions are still there. Go to **Export** and download the results CSV. Mention that formula-like text is neutralised and amounts stay numeric.

**2:45–3:00: limits.** "On this data, 406 automatic and 148 probable matches had zero errors, and on the synthetic demo 5 of 17 proposals were wrong, which is why a person confirms them. The ledger is derived, so these are not real-world accuracy figures. Batched, split and wrong-sign bookings are left for manual review."
