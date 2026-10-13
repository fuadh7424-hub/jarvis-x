"""Planning boundary reserved for a later phase."""
from app.agent.state import AgentState


class Planner:
    def plan(self, state: AgentState) -> AgentState:
        """Return unchanged state; autonomous planning is not implemented in Phase 1."""
        return state

