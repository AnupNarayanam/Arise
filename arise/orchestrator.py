"""
Orchestrator: owns the lineage. Runs every live agent's cycle, and is the
only thing allowed to spawn a child agent when an agent reports should_split.
Also owns the global kill switch, which must cascade to every descendant.
"""

import uuid

from .agent import Agent
from .config import CONFIG
from .ledger import Ledger


class Orchestrator:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.agents: dict[str, Agent] = {}
        self.root_agent_id: str | None = None
        self.killed = False

    def bootstrap(self):
        root = Agent.spawn_root(self.ledger)
        self.agents[root.agent_id] = root
        self.root_agent_id = root.agent_id
        return root

    def kill_switch(self):
        """Global halt. Cascades to every agent in the lineage."""
        self.killed = True
        for agent_id in list(self.agents.keys()):
            self.ledger.set_agent_status(agent_id, "dead")

    def run_all_cycles(self) -> list[dict]:
        if self.killed:
            return [{"status": "halted", "reason": "kill switch engaged"}]

        results = []
        for agent_id, agent in list(self.agents.items()):
            row = self.ledger.get_agent(agent_id)
            if row["status"] != "alive":
                continue

            outcome = agent.run_cycle()
            outcome["agent_id"] = agent_id
            results.append(outcome)

            if outcome.get("status") == "dead":
                continue

            if outcome.get("should_split"):
                child = self._split(agent)
                results.append({"status": "spawned", "parent_id": agent_id, "child_id": child.agent_id})

        return results

    def _split(self, parent: Agent) -> Agent:
        balance = parent.balance()
        reserve = balance * CONFIG.replication.parent_reserve_fraction
        each_side = (balance - reserve) / 2

        child_id = str(uuid.uuid4())
        child_strategy = self._diversified_strategy_for(parent.agent_id)

        # Move funds: parent keeps reserve + its share, child gets its share.
        self.ledger.record_entry(parent.agent_id, "expense", each_side, "split", child_id, None)
        self.ledger.register_agent(child_id, parent_id=parent.agent_id, balance_at_last_split=each_side,
                                    strategy_tag=child_strategy)
        self.ledger.record_entry(child_id, "income", each_side, "split", parent.agent_id, None)

        self.ledger.update_split_point(parent.agent_id, parent.balance())

        child = Agent(self.ledger, child_id, root_agent_id=self.root_agent_id)
        self.agents[child_id] = child
        return child

    def _diversified_strategy_for(self, parent_id: str) -> str:
        """Picks a strategy_tag for a new child that differs from its
        parent's, so the lineage isn't betting everything on one income
        tool/platform. Rotates through allowed earn-tools; if there's only
        one, there's nothing to diversify into and the child inherits it."""
        earn_tools = [t for t in CONFIG.allowed_tools if t != "cost_cutting"]
        if len(earn_tools) <= 1:
            return earn_tools[0] if earn_tools else ""
        parent_row = self.ledger.get_agent(parent_id)
        parent_tag = parent_row.get("strategy_tag", "") if parent_row else ""
        others = [t for t in earn_tools if t != parent_tag]
        return others[0] if others else earn_tools[0]

    def status_report(self) -> dict:
        agents = self.ledger.all_agents()
        total_balance = sum(self.ledger.balance(a["agent_id"]) for a in agents if a["status"] == "alive")
        alive = [a for a in agents if a["status"] == "alive"]
        dead = [a for a in agents if a["status"] == "dead"]
        return {
            "total_agents_ever": len(agents),
            "alive": len(alive),
            "dead": len(dead),
            "total_balance": round(total_balance, 2),
        }
