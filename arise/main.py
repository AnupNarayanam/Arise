"""
Entry point.

Phase 0 usage:
    python -m arise.main --cycles 20

Set ANTHROPIC_API_KEY to use real LLM decisions; otherwise the decision
engine dry-runs with safe no-ops so the loop and ledger can be exercised
end-to-end without any API key.
"""

import argparse

from .config import CONFIG
from .ledger import Ledger
from .orchestrator import Orchestrator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--db", type=str, default=CONFIG.db_path)
    args = parser.parse_args()

    ledger = Ledger(args.db)
    orch = Orchestrator(ledger)
    root = orch.bootstrap()

    print(f"Bootstrapped root agent {root.agent_id} with ${CONFIG.seed_balance}")

    for cycle in range(1, args.cycles + 1):
        results = orch.run_all_cycles()
        report = orch.status_report()

        print(f"\n--- cycle {cycle} ---")
        for r in results:
            print(" ", r)
        print(f"  lineage: {report}")

        if report["alive"] == 0:
            print("\nAll agents dead. Lineage did not survive.")
            break
    else:
        print(f"\nSurvived all {args.cycles} cycles. Final report: {orch.status_report()}")


if __name__ == "__main__":
    main()
