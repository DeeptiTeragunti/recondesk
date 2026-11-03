"""Write an evaluation report from a bank CSV, a ledger CSV and a truth manifest.

Run from the project root:
    python -m scripts.evaluate_demo                       # the shipped synthetic demo -> docs/EVAL_REPORT.md
    python -m scripts.evaluate_demo --bank B.csv --ledger L.csv --manifest M.json --report R.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reconcile.evaluation import evaluate, render_markdown, sweep
from reconcile.matcher import WINDOW_DEFAULT

DEMO = Path("data/demo")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default=str(DEMO / "bank_export_demo.csv"))
    parser.add_argument("--ledger", default=str(DEMO / "ledger_export_demo.csv"))
    parser.add_argument("--manifest", default=str(DEMO / "truth_manifest.json"))
    parser.add_argument("--report", default="docs/EVAL_REPORT.md")
    args = parser.parse_args(argv)
    bank = Path(args.bank).read_bytes()
    ledger = Path(args.ledger).read_bytes()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    default = evaluate(bank, ledger, manifest, WINDOW_DEFAULT)
    sweeps = sweep(bank, ledger, manifest)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_markdown(default, sweeps, manifest), encoding="utf-8")
    print(f"wrote {report}: precision={default['precision']} recall={default['recall']} "
          f"auto={default['automatic_matches']} truth={default['truth_pairs']}")


if __name__ == "__main__":
    main(sys.argv[1:])
