"""Research tools for the CRO agent — lets users trigger Scout research on demand.

These tools allow the CRO (via conversational @CRO mentions or heartbeat) to:
- Create a new account in Attio CRM
- Run Scout research inline on any account and get back a brief
"""

import logging
from typing import Any

import anthropic

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)


def register_research_tools(
    registry: ToolRegistry,
    attio,
    claude_client: anthropic.AsyncAnthropic | None,
    scout_prompt: str = "",
    scout_model: str = "claude-sonnet-4-6",
    scout_batch_size: int = 3,
    apollo=None,
    fullenrich=None,
) -> None:
    """Register CRO research tools: create accounts and run Scout inline."""

    # ── attio_create_company ──────────────────────────────────────────────

    async def attio_create_company(
        name: str,
        segment: str = "",
        tier: str = "Tier 3",
        website: str | None = None,
        notes: str | None = None,
    ) -> Any:
        """Create a new company account in Attio CRM."""
        from src.agents.scout import _parse_company
        # First check if it already exists
        try:
            existing = await attio.query_records(
                object_slug="companies",
                filter_={"name": name},
                limit=1,
            )
            if existing.get("data"):
                existing_co = _parse_company(existing["data"][0])
                return {
                    "status": "already_exists",
                    "id": existing_co["id"],
                    "name": existing_co["name"],
                    "message": f"{name} already exists in Attio (ID: {existing_co['id']})",
                }
        except Exception:
            pass  # Proceed to create if lookup fails

        # Build the record
        values: dict = {
            "name": [{"value": name}],
            "account_tier": [{"option": {"title": tier}}],
            "outreach_status": [{"option": {"title": "Not Started"}}],
        }
        if segment:
            values["segment"] = [{"option": {"title": segment}}]
        if website:
            values["domains"] = [{"domain": website.lstrip("https://").lstrip("http://").rstrip("/")}]

        try:
            result = await attio.assert_record(
                object_slug="companies",
                matching_attribute="name",
                data={"values": values},
            )
            record = result.get("data", {})
            record_id = record.get("id", {})
            if isinstance(record_id, dict):
                record_id = record_id.get("record_id", "unknown")

            # Add initial notes if provided
            if notes and record_id and record_id != "unknown":
                await attio.create_note(
                    parent_object="companies",
                    parent_record_id=record_id,
                    title=f"Initial Notes — {name}",
                    content=notes,
                )

            return {
                "status": "created",
                "id": record_id,
                "name": name,
                "tier": tier,
                "segment": segment,
                "message": f"{name} added to Attio as {tier}. Ready for Scout research.",
            }
        except Exception as e:
            logger.error("Failed to create company %s: %s", name, e)
            return {"status": "error", "error": str(e)}

    registry.register(
        name="attio_create_company",
        description=(
            "Create a new company account in Attio CRM. "
            "Use this when you want to add a prospect that doesn't exist yet. "
            "After creating, call run_scout_research to immediately research them."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Company name (e.g., 'Acme Corp')"},
                "segment": {
                    "type": "string",
                    "description": "Market segment — configure your segments in your CRM and prompts.",
                },
                "tier": {
                    "type": "string",
                    "description": "Account tier: Tier 1, Tier 2, or Tier 3. Default Tier 3.",
                    "default": "Tier 3",
                },
                "website": {"type": "string", "description": "Optional company website URL."},
                "notes": {"type": "string", "description": "Optional initial notes or context about why this account is interesting."},
            },
            "required": ["name"],
        },
        handler=attio_create_company,
    )

    # ── run_scout_research ────────────────────────────────────────────────

    async def run_scout_research(company_name: str, reason: str = "") -> Any:
        """Run the Scout agent on a specific company and return a research brief."""
        if not claude_client:
            return {"status": "error", "error": "Claude client not available"}

        from src.agents.scout import _parse_company
        # Find the company in Attio
        try:
            result = await attio.query_records(
                object_slug="companies",
                filter_={"name": company_name},
                limit=1,
            )
            records = result.get("data", [])
            if not records:
                return {
                    "status": "not_found",
                    "message": (
                        f"{company_name} not found in Attio. "
                        f"Use attio_create_company first to add them."
                    ),
                }
        except Exception as e:
            return {"status": "error", "error": f"Attio lookup failed: {e}"}

        company = _parse_company(records[0])
        company_id = company["id"]

        # If a reason was provided, add it as a context note
        if reason:
            try:
                await attio.create_note(
                    parent_object="companies",
                    parent_record_id=company_id,
                    title="Research Request",
                    content=f"Research requested via CRO.\nReason: {reason}",
                )
            except Exception:
                pass

        # Run the Scout agent inline on this one account
        logger.info("run_scout_research: launching Scout inline for %s", company_name)
        try:
            from src.agents.scout import ScoutAgent
            from src.tools.attio_tools import register_attio_tools
            from src.claude.tools import ToolRegistry

            tool_registry = ToolRegistry()
            register_attio_tools(tool_registry, attio)
            if apollo:
                try:
                    from src.tools.apollo_tools import register_apollo_tools
                    from src.config import settings
                    register_apollo_tools(
                        tool_registry, apollo, attio, settings.apollo_credits_per_heartbeat,
                        fullenrich=fullenrich,
                    )
                except ImportError:
                    pass

            agent = ScoutAgent(
                attio=attio,
                claude_client=claude_client,
                system_prompt=scout_prompt,
                tool_registry=tool_registry,
                model=scout_model,
                batch_size=1,
            )

            result = await agent.run_for_company(company)
            return {
                "status": "completed",
                "company": company_name,
                "brief": result,
                "message": f"Scout research complete for {company_name}. Brief saved to Attio.",
            }
        except AttributeError:
            logger.warning(
                "ScoutAgent.run_for_company not available — requesting full Scout run"
            )
            return {
                "status": "queued",
                "company": company_name,
                "message": (
                    f"{company_name} is in Attio and will be researched on the next Scout heartbeat. "
                    f"Trigger POST /agents/scout/heartbeat to run now."
                ),
            }
        except Exception as e:
            logger.error("run_scout_research failed for %s: %s", company_name, e)
            return {"status": "error", "error": str(e)}

    registry.register(
        name="run_scout_research",
        description=(
            "Run the Scout research agent on a specific company and return a brief. "
            "Use this when asked to 'research [Company]' or 'add [Company] to the pipeline'. "
            "The company must already exist in Attio — use attio_create_company first if not. "
            "Returns a research brief with company intel, key contacts, and recommended approach."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Exact company name as it appears in (or should appear in) Attio.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason or context for the research request.",
                },
            },
            "required": ["company_name"],
        },
        handler=run_scout_research,
    )
