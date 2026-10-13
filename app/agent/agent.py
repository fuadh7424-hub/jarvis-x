"""Minimal agent facade without model or operating-system access."""
from app.agent.state import AgentState
from app.agent.planner import Planner


class Agent:
    def __init__(self, planner: Planner | None = None) -> None:
        self.planner = planner or Planner()

    def prepare(self, goal: str) -> AgentState:
        state = AgentState(goal=goal)
        return self.planner.plan(state)

