"""
Agent: one member of a Arise lineage.
Runs the SENSE -> THINK -> ACT -> RECORD -> CHECK loop for itself.
Replication is decided here but executed by the Orchestrator, which owns
the ability to create new agents.
"""

import uuid

from . import tools
from .config import CONFIG
from .decision_engine import decide
from .guardrails import check_action, can_split
from .ledger import Ledger
from . import notifier


class Agent:
    def __init__(self, ledger: Ledger, agent_id: str, root_agent_id: str):
        self.ledger = ledger
        self.agent_id = agent_id
        self.root_agent_id = root_agent_id  # for lineage-wide guardrail checks

    @classmethod
    def spawn_root(cls, ledger: Ledger) -> "Agent":
        agent_id = str(uuid.uuid4())
        ledger.register_agent(agent_id, parent_id=None, balance_at_last_split=CONFIG.seed_balance)
        ledger.record_entry(agent_id, "income", CONFIG.seed_balance, "seed", "founder", None)
        return cls(ledger, agent_id, root_agent_id=agent_id)

    def balance(self) -> float:
        return self.ledger.balance(self.agent_id)

    def is_alive(self) -> bool:
        return self.balance() > 0

    def run_cycle(self) -> dict:
        """Runs one SENSE -> THINK -> ACT -> RECORD -> CHECK cycle.
        Returns a dict describing what happened, including whether a split
        should be triggered (the orchestrator handles the actual spawn)."""
        balance = self.balance()

        if balance <= 0:
            self.ledger.set_agent_status(self.agent_id, "dead")
            notifier.send(self.agent_id, "dead", f"balance hit {balance} — agent is dead")
            return {"status": "dead", "balance": balance}

        # SENSE: check any pending bounties for a real, confirmed payout
        # before deciding anything new this cycle.
        payout_events = self._check_pending_payouts()

        # SENSE + THINK
        recent = self.ledger.recent_decisions(self.agent_id, limit=5)
        decision = decide(balance, list(CONFIG.allowed_tools), recent)

        action = decision.get("action", "wait")
        target = decision.get("target", "") or ""
        amount = float(decision.get("amount", 0) or 0)
        reasoning = decision.get("reasoning", "")

        # GUARDRAIL CHECK before anything executes
        result = check_action(self.ledger, self.agent_id, self.root_agent_id, action, target, amount, balance)

        if not result.allowed:
            # Forced into escalate rather than silently dropped — a rejected
            # action near the survival floor is exactly when a human needs
            # to know, not exactly when to go quiet.
            decision_id = self.ledger.record_decision(
                self.agent_id, balance, "escalate", target, 0,
                f"{reasoning} [ORIGINAL ACTION REJECTED: {result.reason}]", status="rejected",
            )
            notifier.send(self.agent_id, "escalate", f"balance ${balance:.2f} — {result.reason}")
            return {"status": "rejected", "reason": result.reason, "decision_id": decision_id}

        if result.requires_approval:
            decision_id = self.ledger.record_decision(
                self.agent_id, balance, action, target, amount, reasoning, status="pending_approval",
            )
            notifier.send(self.agent_id, "pending_approval",
                           f"{action} {target} ${amount:.2f} awaiting approval — balance ${balance:.2f}")
            return {"status": "pending_approval", "decision_id": decision_id}

        # ACT
        decision_id = self.ledger.record_decision(self.agent_id, balance, action, target, amount, reasoning)
        self._execute(action, target, amount, decision_id)

        # CHECK for split eligibility
        new_balance = self.balance()
        agent_row = self.ledger.get_agent(self.agent_id)
        should_split = can_split(new_balance, agent_row["balance_at_last_split"])

        # Early warning: balance is getting close to the survival floor, even
        # though this particular action was still allowed. Better to flag it
        # a cycle early than to only notify once an action gets rejected.
        g = CONFIG.guardrails
        floor = max(g.min_safe_balance_floor, new_balance * g.min_safe_balance_fraction)
        if new_balance < floor * 1.5:
            notifier.send(self.agent_id, "near_floor",
                           f"balance ${new_balance:.2f} is approaching the survival floor (${floor:.2f})")

        return {
            "status": "executed",
            "action": action,
            "balance": new_balance,
            "should_split": should_split,
            "decision_id": decision_id,
            "payout_events": payout_events,
        }

    def _check_pending_payouts(self) -> list[dict]:
        """v0.4: checks every pending bounty this agent holds against Algora's
        real payout status. Only a 'paid' status ever creates a real ledger
        income entry — 'pending'/'merged'-but-not-paid stays untouched, and
        dry-run claims always report pending (see real_tools.check_payout)."""
        from . import real_tools  # local import: avoids import cycle with real_tools -> completion -> shipping

        events = []
        for pb in self.ledger.get_pending_bounties(self.agent_id):
            result = real_tools.check_payout(pb["claim_id"])
            status = result.get("status", "pending")
            status_before = pb["status"]
            if status == status_before:
                continue  # no change, nothing to record or notify about
            self.ledger.update_pending_bounty_status(pb["id"], status)
            if status == "paid":
                amount = result.get("amount") or pb["amount"]
                self.ledger.record_entry(self.agent_id, "income", amount, "algora_bounty",
                                          pb["repo"], None)
                notifier.send(self.agent_id, "payout",
                               f"bounty paid: ${amount} for {pb['repo']}#{pb['issue_number']}")
            events.append({"pending_bounty_id": pb["id"], "old_status": status_before, "new_status": status})
        return events

    def _execute(self, action: str, target: str, amount: float, decision_id: str):
        if action == "wait" or action == "escalate":
            return
        if action == "earn":
            result = tools.run_tool(target or "content_gig", amount)
            if result.get("requires_human"):
                # e.g. a real bounty was found but claiming/completion isn't
                # automated yet — this needs a human, not a silent no-op.
                pb = result.get("pending_bounty")
                if pb:
                    # It shipped (or was verified-claimed) even though it's
                    # not income yet — track it so future cycles can check
                    # for a real payout instead of losing the thread here.
                    self.ledger.add_pending_bounty(
                        self.agent_id, pb["bounty_id"], pb["claim_id"] or "",
                        pb["repo"], pb["issue_number"], pb["amount"],
                    )
                notifier.send(self.agent_id, "escalate",
                               f"found opportunity needs human action: {result.get('note')}")
                return
            if result.get("expense"):
                self.ledger.record_entry(self.agent_id, "expense", result["expense"], target, target, decision_id)
            if result.get("income"):
                self.ledger.record_entry(self.agent_id, "income", result["income"], target, target, decision_id)
        elif action == "spend":
            result = tools.run_tool(target or "cost_cutting", amount)
            # cost-cutting doesn't spend the ledger amount itself — it removes a
            # future recurring cost, modeled here as an immediate small fee.
            if result.get("savings", 0) == 0:
                self.ledger.record_entry(self.agent_id, "expense", amount * 0.1, target, target, decision_id)
