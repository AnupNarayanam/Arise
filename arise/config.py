"""
Central configuration for Arise.
Phase 0 defaults: simulated money, conservative caps, everything logged.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Guardrails:
    max_spend_per_action: float = 25.0       # hard ceiling on any single action
    max_spend_per_cycle: float = 50.0        # hard ceiling per decision cycle
    max_lineage_spend_per_day: float = 200.0 # global cap across ALL agents in a lineage
    approval_threshold: float = 20.0         # actions above this need human approval
    fixed_cost_per_cycle: float = 0.05       # est. LLM + infra cost per decision cycle
    min_post_split_balance: float = 50.0     # both parent & child must clear this to split
    min_safe_balance_floor: float = 50.0     # absolute $ floor — never spend below this
    min_safe_balance_fraction: float = 0.15  # relative floor — never spend below this % of balance


@dataclass
class ReplicationConfig:
    enabled: bool = True
    split_multiplier: float = 2.0            # balance must reach N x last-split balance
    parent_reserve_fraction: float = 0.10    # parent keeps this extra fraction on split


@dataclass
class Config:
    seed_balance: float = 500.0
    simulated: bool = True                   # Phase 0: no real money moves
    cycle_hours: int = 6
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    db_path: str = "arise.db"
    guardrails: Guardrails = field(default_factory=Guardrails)
    replication: ReplicationConfig = field(default_factory=ReplicationConfig)
    allowed_tools: tuple = ("content_gig", "cost_cutting", "algora_bounty")  # whitelist — nothing else is callable


CONFIG = Config()
