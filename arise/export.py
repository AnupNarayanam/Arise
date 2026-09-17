"""
Export: dumps the ledger to a single JSON file the dashboard reads.
Deliberately decoupled from a live DB connection — the dashboard is a plain
static HTML file with no backend, so this is the only bridge between them.

Usage: python -m arise.export [--db arise.db] [--out arise_data.json]
"""

import argparse
import json
import sqlite3

from .config import CONFIG


def export(db_path: str, out_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    agents = [dict(r) for r in conn.execute("SELECT * FROM agents").fetchall()]
    entries = [dict(r) for r in conn.execute("SELECT * FROM ledger_entries ORDER BY ts").fetchall()]
    decisions = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY ts").fetchall()]
    conn.close()

    # Derive running balance per agent over time, for the balance-over-time chart.
    balances = {}
    running = {}
    for e in entries:
        aid = e["agent_id"]
        running[aid] = running.get(aid, 0.0) + (e["amount"] if e["type"] == "income" else -e["amount"])
        balances.setdefault(aid, []).append({"ts": e["ts"], "balance": round(running[aid], 2)})

    # Category breakdown — income and expense grouped by category, for the
    # observability breakdown chart.
    category_totals = {}
    for e in entries:
        key = e["category"] or "uncategorized"
        bucket = category_totals.setdefault(key, {"income": 0.0, "expense": 0.0})
        bucket[e["type"]] += e["amount"]
    for k in category_totals:
        category_totals[k] = {t: round(v, 2) for t, v in category_totals[k].items()}

    # Days survived per agent (from creation to now, or to death if dead —
    # approximated by its last ledger entry timestamp).
    import time as _time
    now = _time.time()
    days_survived = {}
    last_entry_ts = {}
    for e in entries:
        last_entry_ts[e["agent_id"]] = max(last_entry_ts.get(e["agent_id"], 0), e["ts"])
    for a in agents:
        end = now if a["status"] == "alive" else last_entry_ts.get(a["agent_id"], a["created_at"])
        days_survived[a["agent_id"]] = round((end - a["created_at"]) / 86400, 2)

    data = {
        "agents": agents,
        "ledger_entries": entries,
        "decisions": decisions,
        "balances_over_time": balances,
        "category_totals": category_totals,
        "days_survived": days_survived,
        "summary": {
            "total_agents_ever": len(agents),
            "alive": sum(1 for a in agents if a["status"] == "alive"),
            "dead": sum(1 for a in agents if a["status"] == "dead"),
            "total_balance": round(sum(running.get(a["agent_id"], 0) for a in agents if a["status"] == "alive"), 2),
            "oldest_lineage_days": max(days_survived.values()) if days_survived else 0,
        },
    }

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Exported {len(agents)} agents, {len(entries)} ledger entries, {len(decisions)} decisions -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=CONFIG.db_path)
    parser.add_argument("--out", default="arise_data.json")
    args = parser.parse_args()
    export(args.db, args.out)
