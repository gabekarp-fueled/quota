"""Management API — auth, agents, objectives, key results, runs, dashboard.

All routes under /api/ — protected by JWT except /api/auth/login.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.db.models import Agent, KeyResult, Objective, Run
from src.db.session import get_db, get_db_optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["api"])

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_bearer = HTTPBearer(auto_error=False)

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _make_token() -> str:
    payload = {
        "sub": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def _verify_token(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if not credentials:
        raise HTTPException(401, "Not authenticated")
    try:
        return jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")


def require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    return _verify_token(credentials)


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


class AgentCreate(BaseModel):
    name: str
    display_name: str
    model: str = "claude-sonnet-4-20250514"
    batch_size: int = 10
    system_prompt: str = ""


class AgentUpdate(BaseModel):
    display_name: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    batch_size: int | None = None
    enabled: bool | None = None


class FileUpdate(BaseModel):
    content: str


class ObjectiveCreate(BaseModel):
    title: str
    description: str = ""
    target_date: str | None = None
    active: bool = True


class ObjectiveUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    target_date: str | None = None
    active: bool | None = None


class KeyResultCreate(BaseModel):
    objective_id: str
    title: str
    metric: str = ""
    target_value: float | None = None
    current_value: float = 0.0


class KeyResultUpdate(BaseModel):
    title: str | None = None
    metric: str | None = None
    target_value: float | None = None
    current_value: float | None = None


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def login(body: LoginRequest):
    if body.password != settings.dashboard_password:
        raise HTTPException(401, "Invalid password")
    return {"token": _make_token()}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(
    _: dict = Depends(require_auth),
    db: AsyncSession | None = Depends(get_db_optional),
):
    if not db:
        return {"error": "Database not configured", "db_available": False}

    runs_result = await db.execute(
        select(Run).order_by(desc(Run.started_at)).limit(20)
    )
    runs = runs_result.scalars().all()

    agents_result = await db.execute(select(Agent).order_by(Agent.name))
    agents = agents_result.scalars().all()

    obj_result = await db.execute(select(Objective).where(Objective.active == True))
    objectives = obj_result.scalars().all()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_runs_result = await db.execute(
        select(Run).where(Run.started_at >= today_start)
    )
    today_runs = today_runs_result.scalars().all()

    last_run_map: dict[str, Any] = {}
    for run in runs:
        if run.agent_name not in last_run_map:
            last_run_map[run.agent_name] = _run_dict(run)

    return {
        "db_available": True,
        "agents": [
            {
                "name": a.name,
                "display_name": a.display_name or _DISPLAY_NAME_FALLBACKS.get(a.name, a.name.title()),
                "model": a.model,
                "batch_size": a.batch_size,
                "enabled": a.enabled,
                "last_run": last_run_map.get(a.name),
            }
            for a in agents
        ],
        "recent_runs": [_run_dict(r) for r in runs],
        "stats": {
            "runs_today": len(today_runs),
            "errors_today": sum(1 for r in today_runs if r.status == "error"),
            "tokens_today": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in today_runs),
            "active_objectives": len(objectives),
        },
    }


# ── Pipeline summary ──────────────────────────────────────────────────────────

@router.get("/pipeline/summary")
async def pipeline_summary(
    request: Request,
    _: dict = Depends(require_auth),
):
    """Aggregate pipeline stats from Attio for the dashboard."""
    attio = getattr(request.app.state, "attio", None)
    if not attio or not settings.attio_api_token:
        return {"available": False}

    try:
        from src.agents.scout import _parse_company
        all_companies = []
        offset = 0
        page_size = 500
        while True:
            resp = await attio.query_records("companies", limit=page_size, offset=offset)
            records = resp.get("data", [])
            all_companies.extend([_parse_company(r) for r in records])
            if len(records) < page_size:
                break
            offset += page_size

        all_companies = [co for co in all_companies if co.get("account_tier") in ("Tier 1", "Tier 2", "Tier 3")]

        status_counts: dict[str, int] = {}
        for co in all_companies:
            s = co.get("outreach_status") or "Not Started"
            status_counts[s] = status_counts.get(s, 0) + 1

        tier_counts: dict[str, int] = {"Tier 1": 0, "Tier 2": 0, "Tier 3": 0}
        for co in all_companies:
            t = co.get("account_tier")
            if t in tier_counts:
                tier_counts[t] += 1

        total = len(all_companies)
        responded = status_counts.get("Responded", 0)
        meeting_booked = status_counts.get("Meeting Booked", 0)
        not_started = status_counts.get("Not Started", 0)
        disqualified = status_counts.get("Disqualified", 0)

        contacted = total - not_started - disqualified
        response_rate = round((responded + meeting_booked) / contacted * 100) if contacted > 0 else 0

        today = datetime.now(timezone.utc).date().isoformat()
        overdue = sum(
            1 for co in all_companies
            if co.get("outreach_status") == "Sequence Active"
            and co.get("next_touch_date")
            and str(co["next_touch_date"])[:10] < today
        )
        need_attention = responded + overdue

        return {
            "available": True,
            "total": total,
            "status_counts": status_counts,
            "tier_counts": tier_counts,
            "response_rate": response_rate,
            "meeting_booked": meeting_booked,
            "need_attention": need_attention,
            "need_attention_detail": {
                "responded_awaiting_followup": responded,
                "overdue_sequences": overdue,
            },
        }

    except Exception:
        logger.exception("Pipeline summary failed")
        return {"available": False}


# ── Agents ────────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents(
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Agent).order_by(Agent.name))
    return [_agent_dict(a) for a in result.scalars().all()]


@router.post("/agents", status_code=201)
async def create_agent(
    body: AgentCreate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    import re
    name = re.sub(r"[^a-z0-9_-]", "_", body.name.lower().strip())
    if not name:
        raise HTTPException(400, "Agent name must contain at least one valid character")
    existing = await db.execute(select(Agent).where(Agent.name == name))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Agent '{name}' already exists")
    agent = Agent(
        name=name,
        display_name=body.display_name.strip() or name.title(),
        model=body.model,
        batch_size=body.batch_size,
        system_prompt=body.system_prompt,
        enabled=True,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    logger.info("Agent '%s' created", name)
    return _agent_dict(agent)


@router.get("/agents/{name}")
async def get_agent(
    name: str,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent_or_404(db, name)
    return _agent_dict(agent)


@router.put("/agents/{name}")
async def update_agent(
    name: str,
    body: AgentUpdate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent_or_404(db, name)
    if body.display_name is not None:
        agent.display_name = body.display_name
    if body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
    if body.model is not None:
        agent.model = body.model
    if body.batch_size is not None:
        agent.batch_size = body.batch_size
    if body.enabled is not None:
        agent.enabled = body.enabled
    agent.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(agent)
    logger.info("Agent '%s' updated", name)
    return _agent_dict(agent)


@router.delete("/agents/{name}", status_code=204)
async def delete_agent(
    name: str,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete an agent and all FK-dependent rows (2-level cascade)."""
    from sqlalchemy import text as _text
    agent = await _get_agent_or_404(db, name)
    agent_id = agent.id

    fk1 = await db.execute(_text("""
        SELECT kcu.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.referential_constraints rc ON tc.constraint_name = rc.constraint_name
        JOIN information_schema.key_column_usage ccu ON ccu.constraint_name = rc.unique_constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = 'agents' AND ccu.column_name = 'id'
    """))
    l1_tables = [(r.table_name, r.column_name) for r in fk1]

    for tbl1, col1 in l1_tables:
        fk2 = await db.execute(_text(f"""
            SELECT kcu.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.referential_constraints rc ON tc.constraint_name = rc.constraint_name
            JOIN information_schema.key_column_usage ccu ON ccu.constraint_name = rc.unique_constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = '{tbl1}'
        """))
        for tbl2, col2 in fk2:
            await db.execute(_text(
                f"DELETE FROM {tbl2} WHERE {col2} IN "
                f"(SELECT id FROM {tbl1} WHERE {col1} = :aid)"
            ), {"aid": agent_id})
        await db.execute(_text(
            f"DELETE FROM {tbl1} WHERE {col1} = :aid"
        ), {"aid": agent_id})

    await db.delete(agent)
    await db.commit()
    logger.info("Agent '%s' (%s) deleted", name, agent_id)


# ── Agent Files ────────────────────────────────────────────────────────────────

@router.get("/agents/{name}/files")
async def list_agent_files(
    name: str,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _get_agent_or_404(db, name)
    files = []
    if _PROMPTS_DIR.exists():
        seen = set()
        for pattern in ["shared.md", f"{name}.md", f"{name}_*.md"]:
            for path in sorted(_PROMPTS_DIR.glob(pattern)):
                if path.name not in seen:
                    seen.add(path.name)
                    files.append({"filename": path.name, "content": path.read_text(encoding="utf-8")})
    return files


@router.put("/agents/{name}/files/{filename}")
async def update_agent_file(
    name: str,
    filename: str,
    body: FileUpdate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    if not re.match(r'^[a-zA-Z0-9_\-]+\.md$', filename):
        raise HTTPException(400, "Invalid filename")
    path = (_PROMPTS_DIR / filename).resolve()
    if not str(path).startswith(str(_PROMPTS_DIR.resolve())):
        raise HTTPException(400, "Invalid path")
    await _get_agent_or_404(db, name)
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    logger.info("Updated prompt file '%s' for agent '%s'", filename, name)

    is_main = filename in (f"{name}.md", "shared.md")
    if is_main:
        shared = (_PROMPTS_DIR / "shared.md")
        agent_file = (_PROMPTS_DIR / f"{name}.md")
        parts = []
        if shared.exists():
            parts.append(shared.read_text(encoding="utf-8").strip())
        if agent_file.exists():
            parts.append(agent_file.read_text(encoding="utf-8").strip())
        new_prompt = "\n\n---\n\n".join(parts)
        agent = await _get_agent_or_404(db, name)
        agent.system_prompt = new_prompt
        agent.updated_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("Rebuilt system_prompt for agent '%s' (%d chars)", name, len(new_prompt))

    return {"filename": filename, "content": body.content}


@router.post("/agents/{name}/files", status_code=201)
async def create_agent_file(
    name: str,
    body: FileUpdate,
    filename: str,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new prompt file. filename query param must start with {name}_."""
    if not re.match(r'^[a-zA-Z0-9_\-]+\.md$', filename):
        raise HTTPException(400, "Invalid filename")
    if not filename.startswith(f"{name}_"):
        raise HTTPException(400, f"New files must be named {name}_<suffix>.md")
    path = (_PROMPTS_DIR / filename).resolve()
    if not str(path).startswith(str(_PROMPTS_DIR.resolve())):
        raise HTTPException(400, "Invalid path")
    if path.exists():
        raise HTTPException(409, "File already exists — use PUT to update")
    await _get_agent_or_404(db, name)
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    logger.info("Created prompt file '%s' for agent '%s'", filename, name)
    return {"filename": filename, "content": body.content}


# ── Objectives ────────────────────────────────────────────────────────────────

@router.get("/objectives")
async def list_objectives(
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Objective)
        .options(selectinload(Objective.key_results))
        .order_by(desc(Objective.active), desc(Objective.created_at))
    )
    return [_obj_dict(o) for o in result.scalars().all()]


@router.post("/objectives", status_code=201)
async def create_objective(
    body: ObjectiveCreate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    obj = Objective(**body.model_dump())
    db.add(obj)
    await db.commit()
    obj = await _get_obj_or_404(db, str(obj.id))
    return _obj_dict(obj)


@router.put("/objectives/{obj_id}")
async def update_objective(
    obj_id: str,
    body: ObjectiveUpdate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    obj = await _get_obj_or_404(db, obj_id)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(obj, field, val)
    await db.commit()
    await db.refresh(obj)
    await db.refresh(obj, ["key_results"])
    return _obj_dict(obj)


@router.delete("/objectives/{obj_id}", status_code=204)
async def delete_objective(
    obj_id: str,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    obj = await _get_obj_or_404(db, obj_id)
    await db.delete(obj)
    await db.commit()


# ── Key Results ───────────────────────────────────────────────────────────────

@router.post("/key-results", status_code=201)
async def create_key_result(
    body: KeyResultCreate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    kr = KeyResult(**body.model_dump())
    db.add(kr)
    await db.commit()
    await db.refresh(kr)
    return _kr_dict(kr)


@router.put("/key-results/{kr_id}")
async def update_key_result(
    kr_id: str,
    body: KeyResultUpdate,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    kr = await _get_kr_or_404(db, kr_id)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(kr, field, val)
    await db.commit()
    await db.refresh(kr)
    return _kr_dict(kr)


@router.delete("/key-results/{kr_id}", status_code=204)
async def delete_key_result(
    kr_id: str,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    kr = await _get_kr_or_404(db, kr_id)
    await db.delete(kr)
    await db.commit()


# ── Runs ──────────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(
    agent: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    q = select(Run).order_by(desc(Run.started_at)).limit(min(limit, 200)).offset(offset)
    if agent:
        q = q.where(Run.agent_name == agent)
    result = await db.execute(q)
    return [_run_dict(r) for r in result.scalars().all()]


# ── Helper functions ──────────────────────────────────────────────────────────

async def _get_agent_or_404(db: AsyncSession, name: str) -> Agent:
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent '{name}' not found")
    return agent


async def _get_obj_or_404(db: AsyncSession, obj_id: str) -> Objective:
    result = await db.execute(
        select(Objective).where(Objective.id == obj_id).options(selectinload(Objective.key_results))
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, f"Objective {obj_id} not found")
    return obj


async def _get_kr_or_404(db: AsyncSession, kr_id: str) -> KeyResult:
    result = await db.execute(select(KeyResult).where(KeyResult.id == kr_id))
    kr = result.scalar_one_or_none()
    if not kr:
        raise HTTPException(404, f"Key result {kr_id} not found")
    return kr


def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


_DISPLAY_NAME_FALLBACKS = {
    "scout": "Scout", "outreach": "Outreach", "enablement": "Enablement",
    "channels": "Channels", "cro": "CRO", "inbox": "Inbox",
    "digest": "Digest", "followup": "Follow-Up",
}


def _agent_dict(a: Agent) -> dict:
    return {
        "name": a.name,
        "display_name": a.display_name or _DISPLAY_NAME_FALLBACKS.get(a.name, a.name.title()),
        "system_prompt": a.system_prompt,
        "model": a.model,
        "batch_size": a.batch_size,
        "enabled": a.enabled,
        "created_at": _fmt_dt(a.created_at),
        "updated_at": _fmt_dt(a.updated_at),
    }


def _obj_dict(o: Objective) -> dict:
    return {
        "id": o.id,
        "title": o.title,
        "description": o.description,
        "target_date": o.target_date,
        "active": o.active,
        "created_at": _fmt_dt(o.created_at),
        "key_results": [_kr_dict(kr) for kr in o.key_results],
    }


def _kr_dict(kr: KeyResult) -> dict:
    return {
        "id": kr.id,
        "objective_id": kr.objective_id,
        "title": kr.title,
        "metric": kr.metric,
        "target_value": kr.target_value,
        "current_value": kr.current_value,
        "created_at": _fmt_dt(kr.created_at),
    }


def _run_dict(r: Run) -> dict:
    return {
        "id": r.id,
        "agent_name": r.agent_name,
        "started_at": _fmt_dt(r.started_at),
        "completed_at": _fmt_dt(r.completed_at),
        "status": r.status,
        "turns": r.turns,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "summary": r.summary,
        "tools_used": r.tools_used or [],
        "focus": r.focus,
    }
