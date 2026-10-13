import pytest
from pydantic import BaseModel

from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.registry import ToolRegistry


class SampleInput(BaseModel):
    text: str


class SampleTool(Tool[SampleInput]):
    name = "sample"
    description = "A test-only tool"
    input_model = SampleInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: SampleInput) -> dict[str, object]:
        return {"text": input_data.text}


def test_tool_registration():
    registry = ToolRegistry()
    tool = SampleTool()
    registry.register(tool)
    assert registry.get("sample") is tool
    with pytest.raises(ValueError):
        registry.register(tool)
