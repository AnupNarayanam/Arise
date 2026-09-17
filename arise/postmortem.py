"""
Postmortem: when an agent dies, generate a report — cause, timeline, and
whether an existing guardrail should have caught it but didn't (vs. a
genuinely unguardable market event). This is what turns "it died" from a
failure into a documented engineering finding.

Usage: python -m arise.postmortem <agent_id> [--db arise.db]
"""

import argparse
import sqlite3

from .config import CONFIG


def generate(db_path: str, agent_id: str) -> str:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    agent = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not agent:
        return f"No agent found with id {agent_id}"

    entries = conn.execute(
        "SELECT * FROM ledger_entries WHERE agent_id = ? ORDER BY ts", (agent_id,)
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM decisions WHERE agent_id = ? ORDER BY ts", (agent_id,)
    ).fetchall()
    conn.close()

    # Reconstruct balance history to find exactly when it crossed zero.
    running = 0.0
    history = []
    for e in entries:
        running += e["amount"] if e["type"] == "income" else -e["amount"]
        history.append((e["ts"], round(running, 2), e))

    death_point = next((h for h in history if h[1] <= 0), None)
    final_balance = history[-1][1] if history else 0.0

    # Look at the decisions leading up to death.
    last_five = list(decisions)[-5:]
    rejected_before_death = [d for d in last_five if d["status"] == "rejected"]
    escalated_before_death = [d for d in last_five if d["action"] == "escalate"]

    g = CONFIG.guardrails
    lines = [
        f"# Postmortem: agent {agent_id[:8]}",
        "",
        f"**Status:** {agent['status']}",
        f"**Parent:** {agent['parent_id'][:8] if agent['parent_id'] else 'none (root agent)'}",
        f"**Final balance:** ${final_balance}",
        "",
        "## Timeline",
    ]

    if death_point:
        lines.append(f"- Balance crossed zero at timestamp {death_point[0]:.0f} "
                      f"(entry: {death_point[2]['type']} ${death_point[2]['amount']} / {death_point[2]['category']})")
    else:
        lines.append("- Balance never crossed zero in the recorded ledger (agent may still be alive, "
                      "or died from a cause outside the ledger — e.g. manually killed).")

    lines.append(f"- {len(entries)} total ledger entries, {len(decisions)} total decisions recorded")
    lines.append("")
    lines.append("## Decisions leading up to death (last 5)")
    if last_five:
        for d in last_five:
            lines.append(f"- [{d['status']}] {d['action']} {d['target'] or ''} ${d['amount']} — {d['reasoning']}")
    else:
        lines.append("- (no decisions recorded)")

    lines.append("")
    lines.append("## Guardrail analysis")
    if rejected_before_death:
        lines.append(
            f"- {len(rejected_before_death)} of the last {len(last_five)} decisions were REJECTED by a "
            f"guardrail before death. This means the guardrail layer was working correctly — it caught "
            f"risky actions — but the agent ran out of safe options entirely (e.g. no tool available "
            f"within the survival floor and lineage cap simultaneously). This is a genuine strategy/tool "
            f"diversity gap, not a guardrail bug."
        )
    elif escalated_before_death:
        lines.append(
            f"- The agent escalated {len(escalated_before_death)} time(s) before death. Check whether "
            f"the notifier actually reached a human in time (see arise_notifications.log) — an "
            f"unanswered escalation is the most preventable cause of death in this system."
        )
    else:
        lines.append(
            "- No rejections or escalations immediately preceded death. This suggests either a single "
            "large loss the guardrails didn't anticipate (review max_spend_per_action/cycle in config.py "
            "against what actually happened above), or a market-level failure (e.g. an income tool's "
            "underlying platform stopped paying) that no code-level guardrail could have caught. "
            "Worth adding a guardrail specifically for whichever case applies here."
        )

    lines.append("")
    lines.append(f"*Config at analysis time: survival floor = "
                  f"max(${g.min_safe_balance_floor}, {g.min_safe_balance_fraction*100:.0f}% of balance), "
                  f"daily lineage cap = ${g.max_lineage_spend_per_day}*")

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_id")
    parser.add_argument("--db", default=CONFIG.db_path)
    args = parser.parse_args()
    print(generate(args.db, args.agent_id))
