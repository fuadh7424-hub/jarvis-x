"""Phase 1 HTTP routes."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "jarvis-x"}
