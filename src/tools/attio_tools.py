"""CRM tools for Claude agents — backed by Pipedrive via PipedriveClient.

Tool names are unchanged (attio_*) so existing agent prompts continue to work.
All tools receive pre-normalized flat dicts from PipedriveClient and return
the same shape as before, so agent behavior is unaffected.
"""

import logging
from typing import Any

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)


def register_attio_tools(registry: ToolRegistry, attio) -> None:
    """Register all CRM tools with the given registry."""

    # ── attio_query_accounts ─────────────────────────────────────────────

    async def attio_query_accounts(
        filter: dict[str, Any] | None = None,
        limit: int = 25,
    ) -> Any:
        """Query company accounts from the CRM."""
        from src.agents.scout import _parse_company
        result = await attio.query_records(
            object_slug="companies",
            filter_=filter or {},
            limit=limit,
        )
        companies = [_parse_company(r) for r in result.get("data", [])]
        return {"total": len(companies), "accounts": companies}

    registry.register(
        name="attio_query_accounts",
        description=(
            "Query company accounts from the CRM with optional filters. "
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
                        "Filter criteria as key-value pairs. Keys are field names "
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
        return _parse_company(result.get("data", result))

    registry.register(
        name="attio_get_account_details",
        description=(
            "Get the full details of a specific company account by its record ID. "
            "Returns all CRM fields including name, segment, tier, outreach status, and notes. "
            "Use this when you need deep detail on a single account."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "The CRM record ID for the company.",
                },
            },
            "required": ["record_id"],
        },
        handler=attio_get_account_details,
    )

    # ── attio_update_account ─────────────────────────────────────────────

    async def attio_update_account(record_id: str, attributes: dict[str, Any]) -> Any:
        """Update attributes on an account record."""
        await attio.update_record("companies", record_id, attributes)
        return {"status": "updated", "record_id": record_id, "updated_fields": list(attributes.keys())}

    registry.register(
        name="attio_update_account",
        description=(
            "Update one or more attributes on a company account in the CRM. "
            "Provide the record_id and a flat dictionary of field names to their new values. "
            "Example: {\"outreach_status\": \"Sequence Active\", \"account_tier\": \"Tier 1\"}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "The CRM record ID for the company to update.",
                },
                "attributes": {
                    "type": "object",
                    "description": "Key-value pairs of field names to their new values.",
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
            "Create a note on a company account record in the CRM. "
            "Use this to save research briefs, email drafts, call prep briefs, "
            "or any structured content associated with an account."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "The CRM record ID for the company.",
                },
                "title": {
                    "type": "string",
                    "description": "Title of the note (e.g., 'Research Brief: Acme Corp').",
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
        """Create a task in the CRM, optionally linked to a company record."""
        linked_records = None
        if linked_record_id:
            linked_records = [{"object": "organizations", "id": linked_record_id}]
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
            "Create a task in the CRM for human follow-up. "
            "Use this to request approval on Tier 1 outreach, "
            "flag accounts needing manual research, or schedule follow-up actions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Task description (e.g., 'Approve outreach: Acme Corp — Touch 1').",
                },
                "linked_record_id": {
                    "type": "string",
                    "description": "Optional record ID to link the task to a company.",
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
        """Query people/contacts associated with a company."""
        # Step 1: Look up the company to get its ID
        company_result = await attio.query_records(
            object_slug="companies",
            filter_={"name": company_name},
            limit=1,
        )
        companies = company_result.get("data", [])
        if not companies:
            return {
                "total": 0,
                "contacts": [],
                "error": f"Company '{company_name}' not found in CRM",
            }
        company_id = companies[0].get("id", "")

        # Step 2: Query contacts linked to this company
        result = await attio.query_records(
            object_slug="people",
            filter_={"org_id": company_id},
            limit=limit,
        )
        records = result.get("data", [])
        contacts = [
            {
                "id": r.get("id", ""),
                "name": r.get("name"),
                "email": r.get("email"),
                "title": r.get("job_title"),
                "persona": r.get("persona"),
                "linkedin": r.get("linkedin_url"),
                "sequence_status": r.get("sequence_status"),
                "sequence_touch": r.get("sequence_touch"),
                "last_touch_date": r.get("last_touch_date"),
                "next_touch_date": r.get("next_touch_date"),
            }
            for r in records
        ]
        return {"total": len(contacts), "contacts": contacts, "company_id": company_id}

    registry.register(
        name="attio_get_contacts",
        description=(
            "Query people/contacts associated with a company from the CRM. "
            "Returns contact records with name, email, job title, persona type, "
            "and LinkedIn URL. Use this to find the right person to target for outreach."
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
        await attio.update_record("people", contact_record_id, attributes)
        return {
            "status": "updated",
            "contact_record_id": contact_record_id,
            "updated_fields": list(attributes.keys()),
        }

    registry.register(
        name="attio_update_contact",
        description=(
            "Update one or more attributes on a contact (People) record in the CRM. "
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
                    "description": "The CRM record ID for the contact.",
                },
                "attributes": {
                    "type": "object",
                    "description": (
                        "Key-value pairs of attributes to update. "
                        "Valid sequence fields: sequence_status, sequence_touch, "
                        "last_touch_date (YYYY-MM-DD), next_touch_date (YYYY-MM-DD)."
                    ),
                },
            },
            "required": ["contact_record_id", "attributes"],
        },
        handler=attio_update_contact,
    )

    # ── attio_query_contacts_due ─────────────────────────────────────────

    async def attio_query_contacts_due(as_of_date: str | None = None, limit: int = 20) -> Any:
        """Find contacts with pending email activities due on or before a given date."""
        from datetime import date as _date
        check_date = as_of_date or _date.today().isoformat()
        result = await attio.query_activities(
            done=0,
            activity_type="email",
            due_before=check_date,
            limit=limit,
        )
        activities = result.get("data", [])
        due_contacts = []
        seen_person_ids: set = set()
        for act in activities:
            # Extract person_id (may be int or dict with "value" key)
            raw_pid = act.get("person_id")
            person_id = str(raw_pid.get("value") if isinstance(raw_pid, dict) else raw_pid or "")
            if not person_id or person_id in seen_person_ids:
                continue
            seen_person_ids.add(person_id)

            # Extract org_id
            raw_oid = act.get("org_id")
            org_id = str(raw_oid.get("value") if isinstance(raw_oid, dict) else raw_oid or "")

            # Extract email from the nested person object in the activity
            person_obj = act.get("person") or {}
            emails = person_obj.get("email") or []
            email = None
            for e in emails if isinstance(emails, list) else []:
                if isinstance(e, dict) and e.get("value"):
                    email = e["value"]
                    if e.get("primary"):
                        break

            # Infer touch number from the activity subject ("Touch N due")
            subject = act.get("subject", "")
            sequence_touch = None
            if "Touch" in subject:
                try:
                    sequence_touch = int(subject.split("Touch")[1].split()[0])
                except (IndexError, ValueError):
                    pass

            due_contacts.append({
                "id": person_id,
                "name": act.get("person_name") or person_obj.get("name"),
                "email": email,
                "company_id": org_id,
                "activity_id": str(act.get("id", "")),
                "sequence_touch": sequence_touch,
                "due_date": act.get("due_date"),
            })
        return {
            "total": len(due_contacts),
            "contacts_due": due_contacts,
            "as_of_date": check_date,
        }

    registry.register(
        name="attio_query_contacts_due",
        description=(
            "Find all contacts whose next sequence touch is due on or before today. "
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
            "Create a note on a contact (People) record in the CRM. "
            "Use this to log per-contact activity — email drafts, sent confirmations, "
            "or research notes specific to an individual contact."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "contact_record_id": {
                    "type": "string",
                    "description": "The CRM record ID for the contact.",
                },
                "title": {
                    "type": "string",
                    "description": "Note title (e.g., 'Touch 1 Sent: John Smith').",
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
            filter_={"org_id": company_id},
            limit=10,
        )
        records = result.get("data", [])
        deals = [
            {
                "deal_id": r.get("id", ""),
                "deal_name": r.get("name"),
                "stage": r.get("stage"),
                "value": r.get("value"),
                "close_date": r.get("close_date"),
            }
            for r in records
        ]
        return {"total": len(deals), "deals": deals, "company_id": company_id}

    registry.register(
        name="attio_get_company_deals",
        description=(
            "Get all deals linked to a company in the CRM. "
            "Returns deal records with stage, value, and close date. "
            "Use this to check where a company is in the deal pipeline."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The CRM record ID for the company.",
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
        """Create a new deal in the CRM linked to a company."""
        # NOTE: Pipedrive requires a stage_id (integer). If stage lookup fails,
        # the deal is created without a stage. Configure your pipeline stages in
        # Pipedrive and update the stage_id mapping as needed.
        payload: dict = {"title": deal_name, "org_id": int(company_id)}
        result = await attio.create_record("deals", payload)
        deal_id = result.get("data", {}).get("id", {}).get("record_id", "unknown")
        return {"status": "created", "deal_id": deal_id, "deal_name": deal_name, "stage": stage}

    registry.register(
        name="attio_create_deal",
        description=(
            "Create a new deal in the pipeline linked to a company. "
            "Use this when an account books a meeting and becomes a real opportunity."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "The CRM record ID for the company to link this deal to.",
                },
                "deal_name": {
                    "type": "string",
                    "description": "Name for the deal (e.g., 'Acme Corp — Q2 2026').",
                },
                "stage": {
                    "type": "string",
                    "description": "Initial deal stage name. Default: 'Discovery'.",
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
        # Pass stage as a friendly name — PipedriveClient will map to stage_id if configured
        await attio.update_record("deals", deal_id, {"stage": stage})
        return {"status": "updated", "deal_id": deal_id, "new_stage": stage}

    registry.register(
        name="attio_update_deal_stage",
        description=(
            "Update the stage of an existing deal in the pipeline. "
            "Get the deal_id first using attio_get_company_deals."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "deal_id": {
                    "type": "string",
                    "description": "The deal record ID (from attio_get_company_deals).",
                },
                "stage": {
                    "type": "string",
                    "description": "New deal stage name.",
                },
            },
            "required": ["deal_id", "stage"],
        },
        handler=attio_update_deal_stage,
    )
