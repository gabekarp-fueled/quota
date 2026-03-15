"""Health check endpoints."""

from fastapi import APIRouter, Request

from src.scheduler import get_scheduler_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/admin/scheduler")
async def scheduler_status(request: Request):
    """Return the current status of all scheduler tasks."""
    return {
        "scheduler": get_scheduler_status(),
        "attio": bool(getattr(request.app.state, "attio", None)),
        "claude": bool(getattr(request.app.state, "claude", None)),
        "email": bool(getattr(request.app.state, "email", None)),
        "inbox": bool(getattr(request.app.state, "inbox", None)),
        "slack": bool(getattr(request.app.state, "slack", None)),
        "apollo": bool(getattr(request.app.state, "apollo", None)),
        "fullenrich": bool(getattr(request.app.state, "fullenrich", None)),
        "db_available": getattr(request.app.state, "db_available", False),
    }
