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

        # Move funds: parent keeps reserve + its share, child gets its share.
        self.ledger.record_entry(parent.agent_id, "expense", each_side, "split", child_id, None)
        self.ledger.register_agent(child_id, parent_id=parent.agent_id, balance_at_last_split=each_side)
        self.ledger.record_entry(child_id, "income", each_side, "split", parent.agent_id, None)

        self.ledger.update_split_point(parent.agent_id, parent.balance())

        child = Agent(self.ledger, child_id, root_agent_id=self.root_agent_id)
        self.agents[child_id] = child
        return child

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
