"""
Ledger: append-only source of truth.
Balance is ALWAYS derived by summing entries — never stored as a mutable field.
This gives a free audit trail and makes "how did it die" trivially reconstructible.
"""

import sqlite3
import time
import uuid
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    parent_id TEXT,
    created_at REAL,
    balance_at_last_split REAL,
    status TEXT DEFAULT 'alive'   -- alive | dead | split_parent
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    ts REAL,
    type TEXT,          -- income | expense
    amount REAL,
    category TEXT,
    counterparty TEXT,
    decision_id TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    ts REAL,
    balance_at_time REAL,
    action TEXT,
    target TEXT,
    amount REAL,
    reasoning TEXT,
    status TEXT DEFAULT 'executed'   -- executed | pending_approval | rejected
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    decision_id TEXT,
    requested_at REAL,
    resolved_at REAL,
    approved INTEGER
);

CREATE TABLE IF NOT EXISTS pending_bounties (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    bounty_id TEXT,
    claim_id TEXT,
    repo TEXT,
    issue_number INTEGER,
    amount REAL,
    status TEXT DEFAULT 'claimed',   -- claimed | pr_open | merged | paid | rejected
    created_at REAL,
    updated_at REAL
);
"""


class Ledger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- agents / lineage ----

    def register_agent(self, agent_id: str, parent_id: str | None, balance_at_last_split: float):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO agents (agent_id, parent_id, created_at, balance_at_last_split, status) "
                "VALUES (?, ?, ?, ?, 'alive')",
                (agent_id, parent_id, time.time(), balance_at_last_split),
            )

    def set_agent_status(self, agent_id: str, status: str):
        with self._conn() as conn:
            conn.execute("UPDATE agents SET status = ? WHERE agent_id = ?", (status, agent_id))

    def update_split_point(self, agent_id: str, balance: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE agents SET balance_at_last_split = ? WHERE agent_id = ?",
                (balance, agent_id),
            )

    def get_agent(self, agent_id: str):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
            return dict(row) if row else None

    def all_agents(self):
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM agents").fetchall()]

    # ---- balance (derived, never stored directly) ----

    def balance(self, agent_id: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) AS bal "
                "FROM ledger_entries WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            return round(row["bal"], 2)

    def lineage_spend_since(self, root_agent_id: str, since_ts: float) -> float:
        """Total expenses across this agent and all its descendants since a timestamp."""
        agents = self.all_agents()
        lineage_ids = self._collect_lineage(root_agent_id, agents)
        with self._conn() as conn:
            q = "SELECT COALESCE(SUM(amount), 0) AS spend FROM ledger_entries " \
                "WHERE type = 'expense' AND ts >= ? AND agent_id IN (%s)" % (
                    ",".join("?" * len(lineage_ids))
                )
            row = conn.execute(q, (since_ts, *lineage_ids)).fetchone()
            return round(row["spend"], 2)

    def _collect_lineage(self, root_id: str, agents: list[dict]) -> list[str]:
        by_parent = {}
        for a in agents:
            by_parent.setdefault(a["parent_id"], []).append(a["agent_id"])
        result, stack = [], [root_id]
        while stack:
            aid = stack.pop()
            result.append(aid)
            stack.extend(by_parent.get(aid, []))
        return result

    # ---- entries ----

    def record_entry(self, agent_id: str, type_: str, amount: float, category: str,
                      counterparty: str, decision_id: str | None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ledger_entries (id, agent_id, ts, type, amount, category, counterparty, decision_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), agent_id, time.time(), type_, amount, category, counterparty, decision_id),
            )

    def record_decision(self, agent_id: str, balance_at_time: float, action: str,
                         target: str, amount: float, reasoning: str, status: str = "executed") -> str:
        decision_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO decisions (id, agent_id, ts, balance_at_time, action, target, amount, reasoning, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, agent_id, time.time(), balance_at_time, action, target, amount, reasoning, status),
            )
        return decision_id

    def recent_decisions(self, agent_id: str, limit: int = 10):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE agent_id = ? ORDER BY ts DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- pending bounties (v0.4: real claim -> ship -> payout tracking) ----

    def add_pending_bounty(self, agent_id: str, bounty_id: str, claim_id: str,
                            repo: str, issue_number: int, amount: float) -> str:
        pb_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pending_bounties (id, agent_id, bounty_id, claim_id, repo, issue_number, "
                "amount, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'claimed', ?, ?)",
                (pb_id, agent_id, bounty_id, claim_id, repo, issue_number, amount, time.time(), time.time()),
            )
        return pb_id

    def get_pending_bounties(self, agent_id: str, exclude_status: tuple = ("paid", "rejected")):
        with self._conn() as conn:
            placeholders = ",".join("?" * len(exclude_status))
            rows = conn.execute(
                f"SELECT * FROM pending_bounties WHERE agent_id = ? AND status NOT IN ({placeholders})",
                (agent_id, *exclude_status),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_pending_bounty_status(self, pb_id: str, status: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_bounties SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), pb_id),
            )
