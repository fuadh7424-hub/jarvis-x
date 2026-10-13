"""A deterministic, read-only demonstration tool."""
from pydantic import BaseModel, Field

from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class TextStatisticsInput(BaseModel):
    text: str = Field(max_length=10_000)


class TextStatisticsTool(Tool[TextStatisticsInput]):
    """Return simple statistics for supplied text without accessing external state."""

    name = "text_statistics"
    description = "Count characters, words, and lines in supplied text."
    input_model = TextStatisticsInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: TextStatisticsInput) -> dict[str, object]:
        return {
            "character_count": len(input_data.text),
            "word_count": len(input_data.text.split()),
            "line_count": len(input_data.text.splitlines()) or (1 if input_data.text else 0),
        }
