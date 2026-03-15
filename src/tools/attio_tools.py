"""Attio CRM tools for Claude agents.

These tools are registered with a ToolRegistry and called by Claude during agentic loops.
Each tool wraps an AttioClient method with proper error handling and string serialization.
"""

import json
import logging
from typing import Any

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)

# Attio Select field slugs — these require [{"option": "title"}] write format.
# All other field types (Text, Number, Date) use [{"value": ...}] format.
# Update _SELECT_SLUGS to match the actual select fields in your Attio workspace.
_SELECT_SLUGS = {"account_tier", "segment", "outreach_status", "channel_partner"}

# Select slugs on the People object (same write format, separate set for clarity).
_PEOPLE_SELECT_SLUGS = {"sequence_status"}


def register_attio_tools(registry: ToolRegistry, attio) -> None:
    """Register all Attio CRM tools with the given registry."""

    # ── attio_query_accounts ─────────────────────────────────────────────

    async def attio_query_accounts(
        filter: dict[str, Any] | None = None,
        limit: int = 25,
    ) -> Any:
        """Query company accounts from Attio CRM."""
        from src.agents.scout import _parse_company
        result = await attio.query_records(
            object_slug="companies",
            filter_=filter or {},
            limit=limit,
        )
        records = result.get("data", [])
        companies = [_parse_company(r) for r in records]
        return {"total": len(companies), "accounts": companies}

    registry.register(
        name="attio_query_accounts",
        description=(
            "Query company accounts from the Attio CRM with optional filters. "
            "Use this to find accounts by tier, segment, outreach status, or other attributes. "
            "Returns a list of account objects with all known CRM fields including name, "
            "account_tier, segment, and outreach_status."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "description": (
                        "Filter criteria as key-value pairs. Keys are Attio attribute slugs "
                        "(e.g., 'account_tier', 'segment', 'outreach_status'). "
                        "Values are the exact match values (e.g., 'Tier 1', 'Not Started')."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of accounts to return. Default 25.",
                    "default": 25,
                },
            },
        },
        handler=attio_query_accounts,
    )

    # ── attio_get_account_details ────────────────────────────────────────

    async def attio_get_account_details(record_id: str) -> Any:
        """Get full details for a specific account by record ID."""
        from src.agents.scout import _parse_company
        result = await attio.get_record("companies", record_id)
        record = result.get("data", result)
        return _parse_company(record)

    registry.register(
        name="attio_get_account_details",
        description=(
            "Get the full details of a specific company account from Attio CRM by its record ID. "
            "Returns all CRM fields including name, segment, tier, outreach status, and recent notes. "
            "Use this when you need deep detail on a single account."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "The Attio record ID for the company.",
                },
            },
            "required": ["record_id"],
        },
        handler=attio_get_account_details,
    )

    # ── attio_update_account ─────────────────────────────────────────────

    async def attio_update_account(record_id: str, attributes: dict[str, Any]) -> Any:
        """Update attributes on an account record."""
        # Attio v2: Select fields use {"option": "title"}, all others use {"value": ...}.
        values = {
            key: [{"option": val}] if key in _SELECT_SLUGS else [{"value": val}]
            for key, val in attributes.items()
        }
        result = await attio.update_record("companies", record_id, {"values": values})
        return {"status": "updated", "record_id": record_id, "updated_fields": list(attributes.keys())}

    registry.register(
        name="attio_update_account",
        description=(
            "Update one or more attributes on a company account in Attio CRM. "
            "Use this to enrich account data — e.g., updating custom fields, "
            "recent_news, or outreach_status. Provide the record_id and a dictionary of "
            "attribute slugs to their new values."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "The Attio record ID for the company to update.",
                },
                "attributes": {
                    "type": "object",
                    "description": (
                        "Key-value pairs of attributes to update. Keys are Attio attribute slugs. "
                        "Values are the new values to set."
                    ),
                },
            },
            "required": ["record_id", "attributes"],
        },
        handler=attio_update_account,
    )

    # ── attio_create_note ────────────────────────────────────────────────

    async def attio_create_note(record_id: str, title: str, content: str) -> Any:
        """Create a note on a company record."""
        result = await attio.create_note(
            parent_object="companies",
            parent_record_id=record_id,
            title=title,
            content=content,
        )
        note_id = result.get("data", {}).get("id", {}).get("note_id", "unknown")
        return {"status": "created", "note_id": note_id, "title": title}

    registry.register(
        name="attio_create_note",
        description=(
            "Create a note on a company account record in Attio CRM. "
            "Use this to save research briefs, email drafts, call prep briefs, "
            "or any structured content associated with an account. "
            "Notes are visible in the Attio UI on the account's timeline."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "The Attio record ID for the company.",
                },
                "title": {
                    "type": "string",
                    "description": "Title of the note (e.g., 'Research Brief: Acme Corp', 'DRAFT — Touch 1 Email: Acme').",
                },
                "content": {
                    "type": "string",
                    "description": "The body content of the note. Can be multi-line plain text.",
                },
            },
            "required": ["record_id", "title", "content"],
        },
        handler=attio_create_note,
    )

    # ── attio_create_task ────────────────────────────────────────────────

    async def attio_create_task(
        content: str,
        linked_record_id: str | None = None,
        deadline: str | None = None,
    ) -> Any:
        """Create a task in Attio, optionally linked to a company record."""
        linked_records = None
        if linked_record_id:
            linked_records = [
                {"target_object": "companies", "target_record_id": linked_record_id}
            ]
        result = await attio.create_task(
            content=content,
            deadline=deadline,
            linked_records=linked_records,
        )
        task_id = result.get("data", {}).get("id", {}).get("task_id", "unknown")
        return {"status": "created", "task_id": task_id, "content": content}

    registry.register(
        name="attio_create_task",
        description=(
            "Create a task in Attio CRM for human follow-up. "
            "Use this to request approval on Tier 1 outreach, "
            "flag accounts needing manual research, or schedule follow-up actions. "
            "Tasks appear in the Attio task list and can be linked to company records."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Task description (e.g., 'Approve outreach: Acme Corp — Touch 1', 'Call prep ready: Acme').",
                },
                "linked_record_id": {
                    "type": "string",
                    "description": "Optional Attio record ID to link the task to a company.",
                },
                "deadline": {
                    "type": "string",
                    "description": "Optional deadline in ISO 8601 format (e.g., '2026-03-15T17:00:00Z').",
                },
            },
            "required": ["content"],
        },
        handler=attio_create_task,
    )

    # ── attio_get_contacts ───────────────────────────────────────────────

    async def attio_get_contacts(company_name: str, limit: int = 10) -> Any:
        """Query people records associated with a company."""
        # Step 1: Look up the company record_id by name.
        company_result = await attio.query_records(
            object_slug="companies",
            filter_={"name": company_name},
            limit=1,
        )
        company_records = company_result.get("data", [])
        if not company_records:
            return {
                "total": 0,
                "contacts": [],
                "error": f"Company '{company_name}' not found in Attio",
            }
        company_record_id = company_records[0].get("id", {}).get("record_id", "")

        # Step 2: Query people linked to this company record.
        result = await attio.query_records(
            object_slug="people",
            filter_={"company": {"target_record_id": company_record_id}},
            limit=limit,
        )
        records = result.get("data", [])
        contacts = []
        for r in records:
            values = r.get("values", {})
            contacts.append({
                "id": r.get("id", {}).get("record_id", ""),
                "name": _extract(values, "name"),
                "email": _extract(values, "email_addresses"),
                "title": _extract(values, "job_title"),
                "persona": _extract(values, "persona"),
                "linkedin": _extract(values, "linkedin_url"),
                "sequence_status": _extract(values, "sequence_status"),
                "sequence_touch": _extract(values, "sequence_touch"),
                "last_touch_date": _extract(values, "last_touch_date"),
                "next_touch_date": _extract(values, "next_touch_date"),
            })
        return {"total": len(contacts), "contacts": contacts, "company_id": company_record_id}

    registry.register(
        name="attio_get_contacts",
        description=(
            "Query people/contacts associated with a company from Attio CRM. "
            "Returns contact records with name, email, job title, persona type, "
            "and LinkedIn URL. Use this to find the right person to target for outreach "
            "or to understand the buying committee at an account."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Name of the company to find contacts for.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum contacts to return. Default 10.",
                    "default": 10,
                },
            },
            "required": ["company_name"],
        },
        handler=attio_get_contacts,
    )

    # ── attio_update_contact ─────────────────────────────────────────────

    async def attio_update_contact(contact_record_id: str, attributes: dict[str, Any]) -> Any:
        """Update attributes on a People (contact) record."""
        values = {
            key: [{"option": val}] if key in _PEOPLE_SELECT_SLUGS else [{"value": val}]
            for key, val in attributes.items()
        }
        await attio.update_record("people", contact_record_id, {"values": values})
        return {"status": "updated", "contact_record_id": contact_record_id, "updated_fields": list(attributes.keys())}

    registry.register(
        name="attio_update_contact",
        description=(
            "Update one or more attributes on a contact (People) record in Attio CRM. "
            "Use this to update per-contact sequence state fields: sequence_status, "
            "sequence_touch, last_touch_date, next_touch_date. "
            "sequence_status accepts: 'Not Started', 'Active', 'Responded', 'Nurture', 'Disqualified'. "
            "Prefer calling sequence_advance instead — it handles all sequence state updates atomically."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "contact_record_id": {
                    "type": "string",
                    "description": "The Attio record ID for the People (contact) record.",
                },
                "attributes": {
                    "type": "object",
                    "description": (
                        "Key-value pairs of attributes to update. "
                        "Valid sequence fields: sequence_status (Select), sequence_touch (Number), "
                        "last_touch_date (Date, YYYY-MM-DD), next_touch_date (Date, YYYY-MM-DD)."
                    ),
                },
            },
            "required": ["contact_record_id", "attributes"],
        },
        handler=attio_update_contact,
    )

    # ── attio_query_contacts_due ─────────────────────────────────────────

    async def attio_query_contacts_due(as_of_date: str | None = None, limit: int = 20) -> Any:
        """Find contacts whose next touch is due on or before a given date."""
        from datetime import date as _date
        check_date = as_of_date or _date.today().isoformat()
        result = await attio.query_records(
            object_slug="people",
            filter_={
                "sequence_status": {"option": {"title": "Active"}},
            },
            limit=limit,
        )
        records = result.get("data", [])
        due_contacts = []
        for r in records:
            values = r.get("values", {})
            next_touch_date = _extract(values, "next_touch_date")
            if next_touch_date and next_touch_date <= check_date:
                company_refs = values.get("company", [])
                company_name = None
                if company_refs:
                    company_name = company_refs[0].get("target_record_id")

                due_contacts.append({
                    "id": r.get("id", {}).get("record_id", ""),
                    "name": _extract(values, "name"),
                    "email": _extract(values, "email_addresses"),
                    "title": _extract(values, "job_title"),
                    "sequence_touch": _extract(values, "sequence_touch"),
                    "next_touch_date": next_touch_date,
                    "company_id": company_name,
                })
        return {"total": len(due_contacts), "contacts_due": due_contacts, "as_of_date": check_date}

    registry.register(
        name="attio_query_contacts_due",
        description=(
            "Find all contacts (People records) whose next sequence touch is due on or before today. "
            "Returns contacts with sequence_status = 'Active' and next_touch_date <= today. "
            "Use this at the start of each Outreach heartbeat to find who needs their next email."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "as_of_date": {
                    "type": "string",
                    "description": "Date to check against in YYYY-MM-DD format. Defaults to today.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max contacts to return. Default 20.",
                    "default": 20,
                },
            },
        },
        handler=attio_query_contacts_due,
    )

    # ── attio_create_contact_note ─────────────────────────────────────────

    async def attio_create_contact_note(contact_record_id: str, title: str, content: str) -> Any:
        """Create a note on a People (contact) record."""
        result = await attio.create_note(
            parent_object="people",
            parent_record_id=contact_record_id,
            title=title,
            content=content,
        )
        note_id = result.get("data", {}).get("id", {}).get("note_id", "unknown")
        return {"status": "created", "note_id": note_id, "title": title}

    registry.register(
        name="attio_create_contact_note",
        description=(
            "Create a note on a contact (People) record in Attio CRM. "
            "Use this to log per-contact activity — email drafts, sent confirmations, "
            "or research notes specific to an individual contact. "
            "Notes appear on the contact's timeline in Attio."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "contact_record_id": {
                    "type": "string",
                    "description": "The Attio record ID for the People (contact) record.",
                },
                "title": {
                    "type": "string",
                    "description": "Note title (e.g., 'Touch 1 Sent: John Smith', 'DRAFT — Touch 2: Jane Doe').",
                },
                "content": {
                    "type": "string",
                    "description": "Note body content.",
                },
            },
            "required": ["contact_record_id", "title", "content"],
        },
        handler=attio_create_contact_note,
    )

    # ── attio_get_company_deals ──────────────────────────────────────────

    async def attio_get_company_deals(company_id: str) -> Any:
        """Query all deals linked to a company."""
        result = await attio.query_records(
            object_slug="deals",
            filter_={"associated_company": {"record_id": company_id}},
            limit=10,
        )
        records = result.get("data", [])
        deals = []
        for r in records:
            values = r.get("values", {})
            deals.append({
                "deal_id": r.get("id", {}).get("record_id", ""),
                "deal_name": _extract(values, "name"),
                "stage": _extract(values, "stage"),
                "value": _extract(values, "value"),
                "close_date": _extract(values, "close_date"),
            })
        return {"total": len(deals), "deals": deals, "company_id": company_id}

    registry.register(
        name="attio_get_company_deals",
        description=(
            "Get all deals linked to a company in Attio. "
            "Returns deal records with stage, value, and close date. "
            "Use this to check where a company is in the deal pipeline or before updating a deal stage."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The Attio record ID for the company.",
                },
            },
            "required": ["company_id"],
        },
        handler=attio_get_company_deals,
    )

    # ── attio_create_deal ────────────────────────────────────────────────

    async def attio_create_deal(
        company_id: str,
        deal_name: str,
        stage: str = "Discovery",
    ) -> Any:
        """Create a new deal in Attio linked to a company."""
        data = {
            "values": {
                "name": [{"value": deal_name}],
                "stage": [{"status": {"title": stage}}],
                "associated_company": [
                    {"target_object": "companies", "target_record_id": company_id}
                ],
            }
        }
        result = await attio.create_record("deals", data)
        deal_id = result.get("data", {}).get("id", {}).get("record_id", "unknown")
        return {"status": "created", "deal_id": deal_id, "deal_name": deal_name, "stage": stage}

    registry.register(
        name="attio_create_deal",
        description=(
            "Create a new deal in Attio's deal pipeline linked to a company. "
            "Use this when an account books a meeting and becomes a real opportunity. "
            "Set the stage to match the first stage in your deal pipeline."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The Attio record ID for the company to link this deal to.",
                },
                "deal_name": {
                    "type": "string",
                    "description": "Name for the deal (e.g., 'Acme Corp — Q2 2026').",
                },
                "stage": {
                    "type": "string",
                    "description": "Initial deal stage. Default: 'Discovery'.",
                    "default": "Discovery",
                },
            },
            "required": ["company_id", "deal_name"],
        },
        handler=attio_create_deal,
    )

    # ── attio_update_deal_stage ──────────────────────────────────────────

    async def attio_update_deal_stage(deal_id: str, stage: str) -> Any:
        """Update the stage on an existing deal."""
        data = {
            "values": {
                "stage": [{"status": {"title": stage}}],
            }
        }
        result = await attio.update_record("deals", deal_id, data)
        return {"status": "updated", "deal_id": deal_id, "new_stage": stage}

    registry.register(
        name="attio_update_deal_stage",
        description=(
            "Update the stage of an existing deal in Attio's pipeline. "
            "Get the deal_id first using attio_get_company_deals."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deal_id": {
                    "type": "string",
                    "description": "The Attio deal record ID (from attio_get_company_deals).",
                },
                "stage": {
                    "type": "string",
                    "description": "New deal stage title.",
                },
            },
            "required": ["deal_id", "stage"],
        },
        handler=attio_update_deal_stage,
    )


def _extract(values: dict, slug: str) -> Any:
    """Quick value extractor for Attio nested format."""
    attr_values = values.get(slug, [])
    if not attr_values:
        return None
    first = attr_values[0]
    if "option" in first:
        return first["option"].get("title")
    if "status" in first:
        return first["status"].get("title")
    if "email_address" in first:
        return first["email_address"]
    return first.get("value")
