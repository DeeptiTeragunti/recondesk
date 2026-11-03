# Two-day project market audit

Research date: 3 November 2026. Primary-source feature comparison; not customer-demand validation. ECC is provisionally interpreted as Everything Claude Code.

## Recommendation

Build a narrowly scoped financial reconciliation exception workbench: upload two fixed-schema CSVs, deterministically identify exact matches and discrepancies, review ambiguous candidates, retain evidence and decisions, export results. A local-first design is a plausible positioning hypothesis, not a proven competitive moat. Keep AI confined to explaining supplied evidence; matching and money arithmetic must remain deterministic. A two-day result is a credible demo/MVP, not a production financial system.

## Existing market

| Idea | Existing capability from primary sources | Implication for two days |
|---|---|---|
| Financial reconciliation | [BlackLine Transaction Matching](https://www.blackline.com/products/financial-close/transaction-matching/) imports multiple data sources, applies business rules and identifies exceptions. | Matching and exception handling are established, valuable business workflows; generic novelty is low. A small evidence-focused tool can demonstrate clear business impact without replicating the suite. |
| Financial reconciliation | [Numeric Cash Matching](https://www.numeric.io/product/cash-matching) offers 1-to-1 through many-to-many matching, AI-assisted rule creation, exceptions, drafted entries, and rule suggestions from manual matches. | “AI reconciliation copilot” alone is not differentiation. Avoid claiming AI explanations or human review are unique. |
| Financial reconciliation | [Modern Treasury manual reconciliation](https://docs.moderntreasury.com/payments/docs/manual-reconciliation) documents automatic matching of bank transactions to its originated money movement, plus manual exception resolution; it distinguishes this from its customer-managed-rules reconciliation product. | Broad bank/payment connectivity is an incumbent strength and a poor two-day target. CSV-only intentionally narrows scope. |
| Agent security scanner | [AgentShield](https://github.com/affaan-m/agentshield/releases) already scans agent configuration, MCP and tool permissions and supplies CLI/CI workflows. Its published releases also cover runtime interception, baselines and evidence reports. | Especially direct overlap when building with ECC. A generic rule scanner is unlikely to stand out; a narrow reproducible exploit or config-diff workflow would need explicit differentiation and realistic security tests. |
| Agent evaluation harness | [Promptfoo](https://github.com/promptfoo/promptfoo) provides declarative evaluations, model/prompt comparison, red teaming and CI integration. | A generic test runner and pass-rate dashboard are crowded features. Build a vertical test pack or adapter atop an existing runner instead of a replacement. |
| Agent evaluation harness | [DeepEval quickstart](https://deepeval.com/docs/getting-started) documents local tests, Pytest integration, full agent trajectory evaluation and tracing of internal components. | Agent trajectory checks are already available. Evaluation quality depends on meaningful fixtures and reference criteria, not a new dashboard. |
| Agent evaluation harness | [Langfuse evaluation overview](https://langfuse.com/docs/evaluation/overview) supports online/offline evaluation, datasets, experiments, manual and automated evaluators, deterministic checks and CI regression gates. | Dataset/experiment management and deployment gates are mature territory. A focused business-specific benchmark could still be useful. |

Vendor performance statements are marketing claims; no independent ROI estimates are established here. Existing products establish category competition, not willingness to buy this proposed tool.

## ECC integration facts

The [Everything Claude Code repository](https://github.com/affaan-m/everything-claude-code) currently redirects to [affaan-m/ECC](https://github.com/affaan-m/ECC). It supplies development skills, rules, agents, hooks and workflows; it should be used as the build workflow rather than becoming the product's orchestration runtime.

Current README guidance: `/ecc:plan` is the Claude plugin planning entry; manual installations may expose `/plan`. Use `tdd-workflow`, code review, security review and verification-loop capabilities selectively. `/tdd` and `/eval` are retired short-name shims in an explicit opt-in archive, so a prompt must inspect installed surfaces rather than assume those commands exist. Source: [ECC README workflow section](https://github.com/affaan-m/ECC#readme).

Native Windows core CLI/installers are supported with limitations. Optional shell-backed features may require Git Bash/WSL, and the README lists native-Windows defects in observer/memory paths. For two days, avoid continuous-learning daemons, memory vaults and multi-agent orchestration dependencies. Source: [ECC platform support](https://github.com/affaan-m/ECC#platform-support).

## Practical scope and validation

Choose bank-versus-ledger or settlement-versus-bank, not both. Use one currency and integer minor units or decimals; never binary float. Require explicit date formats, retain original row IDs and input hashes, and never reuse an input row in two matches. Exact, unique matches may be classified deterministically; duplicates and multiple candidates must become review items. Show unresolved totals and do not infer fraud or root causes solely from amount differences.

Include synthetic labeled fixtures covering exact matches, duplicate references, equal-amount ambiguous candidates, fees, delayed dates, missing transactions, malformed rows and currency mismatch. Measure matching precision, abstention, and unresolved amounts; show comparison to a simple exact-match baseline. No live posting, money movement, bank credentials or fabricated time-saved claims.

The original orchestration prompt describes a separate platform with remote Discord control, secure command execution, workers, approvals, scheduling, recovery, startup, persistent memory, backup and dashboard. Security boundaries and recovery semantics cannot be established credibly in two days. Retain environment verification, local project state, a minimal review log and clear setup instructions; omit the orchestrator from this MVP.
