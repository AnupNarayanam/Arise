"""
Guardrails: build this before anything touches real money.
Every action passes through here before it is ever executed.
"""

import time
from dataclasses import dataclass

from .config import CONFIG
from .ledger import Ledger


@dataclass
class GuardrailResult:
    allowed: bool
    requires_approval: bool
    reason: str


def check_action(ledger: Ledger, agent_id: str, root_agent_id: str, action: str,
                  target: str, amount: float, balance: float) -> GuardrailResult:
    g = CONFIG.guardrails

    if action not in ("earn", "spend", "split", "wait", "escalate"):
        return GuardrailResult(False, False, f"'{action}' is not a recognized action type")

    if target and action in ("earn", "spend") and target not in CONFIG.allowed_tools:
        return GuardrailResult(False, False, f"'{target}' is not on the tool whitelist")

    if amount < 0:
        return GuardrailResult(False, False, "negative amounts are never valid")

    if amount > g.max_spend_per_action:
        return GuardrailResult(False, False,
                                f"amount {amount} exceeds per-action cap {g.max_spend_per_action}")

    # Survival floor: never commit spend that would push balance below the
    # greater of an absolute floor and a fraction of current balance. This is
    # checked BEFORE the lineage cap so a near-death agent is always caught,
    # even if the daily cap has room left.
    if action in ("earn", "spend"):
        floor = max(g.min_safe_balance_floor, balance * g.min_safe_balance_fraction)
        if balance - amount < floor:
            return GuardrailResult(False, False,
                                    f"would drop balance to {balance - amount:.2f}, below survival floor {floor:.2f} "
                                    f"— forcing escalate instead")

    day_ago = time.time() - 86400
    spent_today = ledger.lineage_spend_since(root_agent_id, day_ago)
    if spent_today + amount > g.max_lineage_spend_per_day:
        return GuardrailResult(False, False,
                                f"would exceed lineage daily cap ({spent_today}+{amount} > {g.max_lineage_spend_per_day})")

    if amount > g.approval_threshold:
        return GuardrailResult(True, True, "within caps but above human-approval threshold")

    return GuardrailResult(True, False, "within all guardrails")


def can_split(balance: float, balance_at_last_split: float) -> bool:
    r = CONFIG.replication
    g = CONFIG.guardrails
    if not r.enabled:
        return False
    if balance < r.split_multiplier * balance_at_last_split:
        return False
    reserve = balance * r.parent_reserve_fraction
    each_side = (balance - reserve) / 2
    return each_side >= g.min_post_split_balance
