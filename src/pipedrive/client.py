"""Pipedrive CRM client for Quota agents.

Exposes the same interface as the original AttioClient so all agents and tools
work without modification. Handles field-key resolution, enum label ↔ id
translation, and normalization of Pipedrive's response format into flat dicts.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# object_slug → Pipedrive REST endpoint segment
_SLUG_TO_ENDPOINT: dict[str, str] = {
    "companies": "organizations",
    "people": "persons",
    "deals": "deals",
}

# object_slug → field definitions endpoint
_FIELD_ENDPOINTS: dict[str, str] = {
    "companies": "organizationFields",
    "people": "personFields",
    "deals": "dealFields",
}


class PipedriveClient:
    """Async Pipedrive CRM client.

    Returns normalized flat dicts from all query/get methods so callers never
    need to deal with Pipedrive's custom field key hashes. Also transparently
    unwraps Attio-style nested write payloads so existing agent code that passes
    {"values": {"slug": [{"option": "val"}]}} continues to work unchanged.
    """

    def __init__(self, api_token: str):
        self.api_token = api_token
        self._http: httpx.AsyncClient | None = None
        # forward: object_slug → {friendly_name: {key, field_type, options: {label: id}}}
        self._fwd: dict[str, dict] = {}
        # reverse: object_slug → {key: {name, field_type, options: {id: label}}}
        self._rev: dict[str, dict] = {}

    # ── HTTP client ───────────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url="https://api.pipedrive.com/v1",
                params={"api_token": self.api_token},
                timeout=30.0,
            )
        return self._http

    # ── Field definition cache ────────────────────────────────────────────────

    async def _ensure_fields(self, object_slug: str) -> None:
        """Lazily fetch and cache field definitions for an object type."""
        if object_slug in self._fwd:
            return

        endpoint = _FIELD_ENDPOINTS.get(object_slug)
        if not endpoint:
            self._fwd[object_slug] = {}
            self._rev[object_slug] = {}
            return

        try:
            resp = await self._client().get(f"/{endpoint}", params={"limit": 500})
            resp.raise_for_status()
            fields = resp.json().get("data") or []

            fwd: dict[str, dict] = {}
            rev: dict[str, dict] = {}

            for f in fields:
                key = f.get("key", "")
                raw_name = f.get("name", "")
                field_type = f.get("field_type", "varchar")
                friendly = raw_name.lower().replace(" ", "_").replace("-", "_")

                opts_by_label: dict[str, int] = {}
                opts_by_id: dict[int, str] = {}
                for opt in f.get("options") or []:
                    oid = opt.get("id")
                    olabel = opt.get("label", "")
                    if oid is not None:
                        opts_by_label[olabel] = oid
                        opts_by_id[int(oid)] = olabel

                fwd[friendly] = {"key": key, "field_type": field_type, "options": opts_by_label}
                rev[key] = {"name": friendly, "field_type": field_type, "options": opts_by_id}

            self._fwd[object_slug] = fwd
            self._rev[object_slug] = rev
            logger.info("Pipedrive: cached %d fields for %s", len(fwd), object_slug)

        except Exception as e:
            logger.warning("Pipedrive: failed to fetch fields for %s: %s", object_slug, e)
            self._fwd[object_slug] = {}
            self._rev[object_slug] = {}

    # ── Record normalization ──────────────────────────────────────────────────

    def _normalize(self, object_slug: str, record: dict) -> dict:
        """Convert a raw Pipedrive record to a flat, human-readable dict."""
        if not record:
            return {}

        rev = self._rev.get(object_slug, {})

        out: dict[str, Any] = {
            "id": str(record.get("id", "")),
            "name": record.get("name", ""),
        }

        if object_slug == "people":
            emails = record.get("email") or []
            if emails and isinstance(emails, list):
                first = emails[0]
                out["email"] = first.get("value", "") if isinstance(first, dict) else first
            else:
                out["email"] = None
            out["job_title"] = record.get("job_title") or record.get("title")
            org = record.get("org_id")
            if isinstance(org, dict):
                out["org_id"] = str(org.get("value", ""))
            elif org:
                out["org_id"] = str(org)
            else:
                out["org_id"] = None

        if object_slug == "deals":
            org = record.get("org_id")
            if isinstance(org, dict):
                out["org_id"] = str(org.get("value", ""))
            stage = record.get("stage_id")
            out["stage"] = record.get("stage", {}).get("name") if isinstance(record.get("stage"), dict) else stage
            out["value"] = record.get("value")
            out["close_date"] = record.get("close_time") or record.get("expected_close_date")

        # Resolve custom field keys → friendly names
        for key, info in rev.items():
            if key not in record:
                continue
            val = record[key]
            if val is None:
                out[info["name"]] = None
                continue
            ft = info.get("field_type", "varchar")
            if ft in ("enum", "set"):
                label = info["options"].get(int(val)) if isinstance(val, (int, float)) else None
                out[info["name"]] = label or val
            elif ft in ("int", "double", "monetary"):
                try:
                    out[info["name"]] = int(val) if ft == "int" else float(val)
                except (TypeError, ValueError):
                    out[info["name"]] = val
            else:
                out[info["name"]] = val

        return out

    # ── Write payload preparation ─────────────────────────────────────────────

    def _prepare_write(self, object_slug: str, data: dict) -> dict:
        """Convert friendly field names → Pipedrive field keys, labels → option ids."""
        # Unwrap Attio-style nested payload if present
        if "values" in data and isinstance(data["values"], dict):
            flat: dict[str, Any] = {}
            for k, v in data["values"].items():
                if isinstance(v, list) and v:
                    first = v[0]
                    if "option" in first:
                        opt = first["option"]
                        flat[k] = opt.get("title") if isinstance(opt, dict) else opt
                    elif "status" in first:
                        st = first["status"]
                        flat[k] = st.get("title") if isinstance(st, dict) else st
                    elif "value" in first:
                        flat[k] = first["value"]
                    elif "domain" in first:
                        flat["website"] = "https://" + first["domain"]
                    else:
                        flat[k] = first
                else:
                    flat[k] = v
            data = flat

        fwd = self._fwd.get(object_slug, {})
        result: dict[str, Any] = {}

        for name, value in data.items():
            if name in fwd:
                info = fwd[name]
                key = info["key"]
                if value is None:
                    result[key] = None
                elif info.get("field_type") in ("enum", "set"):
                    opt_id = info["options"].get(str(value)) or info["options"].get(value)
                    result[key] = opt_id if opt_id is not None else value
                else:
                    result[key] = value
            else:
                result[name] = value

        return result

    # ── Public interface ──────────────────────────────────────────────────────

    async def query_records(
        self,
        object_slug: str,
        filter_: dict,
        limit: int = 25,
    ) -> dict:
        """Query records with optional filters. Returns {"data": [normalized_dict, ...]}."""
        await self._ensure_fields(object_slug)
        endpoint = _SLUG_TO_ENDPOINT.get(object_slug, object_slug)
        client = self._client()

        try:
            # ── Email search ─────────────────────────────────────────────────
            if "email_addresses" in filter_:
                resp = await client.get(f"/{endpoint}/search", params={
                    "term": filter_["email_addresses"],
                    "fields": "email",
                    "limit": min(limit, 10),
                })
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("items") or []
                records = []
                for item in items:
                    r = item.get("item", {})
                    if r.get("id"):
                        try:
                            d = await client.get(f"/{endpoint}/{r['id']}")
                            d.raise_for_status()
                            records.append(d.json().get("data", {}))
                        except Exception:
                            records.append(r)
                return {"data": [self._normalize(object_slug, r) for r in records]}

            # ── Name search ──────────────────────────────────────────────────
            if "name" in filter_ and object_slug in ("companies", "people"):
                resp = await client.get(f"/{endpoint}/search", params={
                    "term": filter_["name"],
                    "fields": "name",
                    "exact_match": "true",
                    "limit": min(limit, 10),
                })
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("items") or []
                records = []
                for item in items:
                    r = item.get("item", {})
                    if r.get("id"):
                        try:
                            d = await client.get(f"/{endpoint}/{r['id']}")
                            d.raise_for_status()
                            records.append(d.json().get("data", {}))
                        except Exception:
                            records.append(r)
                return {"data": [self._normalize(object_slug, r) for r in records]}

            # ── Domain search ────────────────────────────────────────────────
            if "domains" in filter_:
                resp = await client.get(f"/{endpoint}/search", params={
                    "term": filter_["domains"],
                    "limit": min(limit, 5),
                })
                resp.raise_for_status()
                items = resp.json().get("data", {}).get("items") or []
                records = [i.get("item", {}) for i in items if "item" in i]
                return {"data": [self._normalize(object_slug, r) for r in records]}

            # ── Relationship filter ──────────────────────────────────────────
            params: dict[str, Any] = {"limit": min(limit, 500)}

            org_id: str | None = None
            if "company" in filter_:
                cf = filter_["company"]
                if isinstance(cf, dict):
                    org_id = cf.get("target_record_id") or cf.get("record_id")
                elif isinstance(cf, str):
                    # Caller passed a company name string — look it up
                    nr = await self.query_records("companies", {"name": cf}, limit=1)
                    hits = nr.get("data", [])
                    if hits:
                        org_id = hits[0]["id"]
            elif "org_id" in filter_:
                org_id = str(filter_["org_id"])

            if org_id:
                params["org_id"] = int(org_id)

            # ── List all, then client-side filter ────────────────────────────
            resp = await client.get(f"/{endpoint}", params=params)
            resp.raise_for_status()
            raw = resp.json().get("data") or []
            normalized = [self._normalize(object_slug, r) for r in raw]

            # Apply remaining filters client-side (handles custom field values)
            skip_keys = {"company", "org_id", "name", "email_addresses", "domains"}
            client_filters = {k: v for k, v in filter_.items() if k not in skip_keys}

            if client_filters:
                def matches(rec: dict) -> bool:
                    for key, val in client_filters.items():
                        if isinstance(val, dict):
                            if "option" in val:
                                opt = val["option"]
                                target = opt.get("title") if isinstance(opt, dict) else opt
                            else:
                                continue
                        else:
                            target = val
                        if rec.get(key) != target:
                            return False
                    return True
                normalized = [r for r in normalized if matches(r)]

            return {"data": normalized[:limit]}

        except Exception as e:
            logger.error("Pipedrive.query_records failed (%s): %s", object_slug, e)
            return {"data": []}

    async def get_record(self, object_slug: str, record_id: str) -> dict:
        """Get a single record by ID. Returns {"data": normalized_dict}."""
        await self._ensure_fields(object_slug)
        endpoint = _SLUG_TO_ENDPOINT.get(object_slug, object_slug)
        try:
            resp = await self._client().get(f"/{endpoint}/{record_id}")
            resp.raise_for_status()
            return {"data": self._normalize(object_slug, resp.json().get("data", {}))}
        except Exception as e:
            logger.error("Pipedrive.get_record failed (%s/%s): %s", object_slug, record_id, e)
            return {"data": {}}

    async def update_record(self, object_slug: str, record_id: str, data: dict) -> dict:
        """Update a record. Accepts flat dicts or legacy Attio-style nested format."""
        await self._ensure_fields(object_slug)
        endpoint = _SLUG_TO_ENDPOINT.get(object_slug, object_slug)
        try:
            payload = self._prepare_write(object_slug, data)
            resp = await self._client().put(f"/{endpoint}/{record_id}", json=payload)
            resp.raise_for_status()
            return {"data": resp.json().get("data", {})}
        except Exception as e:
            logger.error("Pipedrive.update_record failed (%s/%s): %s", object_slug, record_id, e)
            raise

    async def create_record(self, object_slug: str, data: dict) -> dict:
        """Create a new record. Returns {"data": {"id": {"record_id": "..."}, ...}}."""
        await self._ensure_fields(object_slug)
        endpoint = _SLUG_TO_ENDPOINT.get(object_slug, object_slug)
        try:
            payload = self._prepare_write(object_slug, data)
            resp = await self._client().post(f"/{endpoint}", json=payload)
            resp.raise_for_status()
            result = resp.json().get("data", {})
            # Return in a format compatible with callers that expect {"id": {"record_id": "..."}}
            return {"data": {"id": {"record_id": str(result.get("id", ""))}, **result}}
        except Exception as e:
            logger.error("Pipedrive.create_record failed (%s): %s", object_slug, e)
            raise

    async def assert_record(
        self,
        object_slug: str,
        matching_attribute: str,
        data: dict,
    ) -> dict:
        """Create or update a record, matching on the given attribute (upsert)."""
        await self._ensure_fields(object_slug)

        # Extract the value we're matching on
        match_val: Any = None
        if "values" in data and isinstance(data["values"], dict):
            attr_vals = data["values"].get(matching_attribute, [])
            if attr_vals:
                first = attr_vals[0]
                if isinstance(first.get("option"), dict):
                    match_val = first["option"].get("title")
                elif "option" in first:
                    match_val = first["option"]
                else:
                    match_val = first.get("value")
        elif matching_attribute in data:
            match_val = data[matching_attribute]

        if match_val:
            existing = await self.query_records(object_slug, {matching_attribute: match_val}, limit=1)
            hits = existing.get("data", [])
            if hits:
                record_id = hits[0]["id"]
                return await self.update_record(object_slug, record_id, data)

        return await self.create_record(object_slug, data)

    async def create_note(
        self,
        parent_object: str,
        parent_record_id: str,
        title: str,
        content: str,
    ) -> dict:
        """Create a note linked to a record."""
        try:
            body = f"**{title}**\n\n{content}" if title else content
            payload: dict[str, Any] = {"content": body}

            if parent_object in ("companies", "organizations"):
                payload["org_id"] = int(parent_record_id)
            elif parent_object in ("people", "persons"):
                payload["person_id"] = int(parent_record_id)
            elif parent_object == "deals":
                payload["deal_id"] = int(parent_record_id)

            resp = await self._client().post("/notes", json=payload)
            resp.raise_for_status()
            note = resp.json().get("data", {})
            note_id = str(note.get("id", "unknown"))
            return {"data": {"id": {"note_id": note_id}, "title": title}}
        except Exception as e:
            logger.error("Pipedrive.create_note failed (%s/%s): %s", parent_object, parent_record_id, e)
            raise

    async def list_notes(self, parent_object: str, parent_record_id: str) -> dict:
        """List notes for a record. Returns {"data": [{"id", "title", "content"}, ...]}."""
        try:
            params: dict[str, Any] = {"limit": 50}
            if parent_object in ("companies", "organizations"):
                params["org_id"] = int(parent_record_id)
            elif parent_object in ("people", "persons"):
                params["person_id"] = int(parent_record_id)

            resp = await self._client().get("/notes", params=params)
            resp.raise_for_status()
            raw = resp.json().get("data") or []

            notes = []
            for n in raw:
                body = n.get("content", "")
                # Extract title from **Title** prefix written by create_note
                title = ""
                if body.startswith("**"):
                    end = body.find("**", 2)
                    if end > 2:
                        title = body[2:end]
                notes.append({"id": str(n.get("id", "")), "title": title, "content": body})

            return {"data": notes}
        except Exception as e:
            logger.error("Pipedrive.list_notes failed (%s/%s): %s", parent_object, parent_record_id, e)
            return {"data": []}

    async def create_task(
        self,
        content: str,
        deadline: str | None = None,
        linked_records: list | None = None,
        done: bool = False,
        activity_type: str = "task",
    ) -> dict:
        """Create an activity in Pipedrive. Set done=True to log a completed touch."""
        try:
            payload: dict[str, Any] = {
                "subject": content,
                "type": activity_type,
                "done": 1 if done else 0,
            }
            if deadline:
                payload["due_date"] = deadline[:10]

            if linked_records:
                for lr in linked_records:
                    obj = lr.get("target_object") or lr.get("object", "")
                    rid = lr.get("target_record_id") or lr.get("id", "")
                    if obj in ("companies", "organizations") and rid:
                        payload["org_id"] = int(rid)
                    elif obj in ("people", "persons") and rid:
                        payload["person_id"] = int(rid)

            resp = await self._client().post("/activities", json=payload)
            resp.raise_for_status()
            task = resp.json().get("data", {})
            return {"data": {"id": {"task_id": str(task.get("id", "unknown"))}}}
        except Exception as e:
            logger.error("Pipedrive.create_task failed: %s", e)
            raise

    async def query_activities(
        self,
        done: int = 0,
        activity_type: str | None = None,
        due_before: str | None = None,
        org_id: str | None = None,
        person_id: str | None = None,
        limit: int = 100,
    ) -> dict:
        """Query activities. Returns {"data": [activity_dict, ...]}."""
        try:
            params: dict[str, Any] = {"done": done, "limit": min(limit, 500)}
            if activity_type:
                params["type"] = activity_type
            if due_before:
                params["start_date"] = "2000-01-01"
                params["end_date"] = due_before

            if org_id:
                resp = await self._client().get(
                    f"/organizations/{org_id}/activities", params=params
                )
            elif person_id:
                resp = await self._client().get(
                    f"/persons/{person_id}/activities", params=params
                )
            else:
                resp = await self._client().get("/activities", params=params)

            resp.raise_for_status()
            return {"data": resp.json().get("data") or []}
        except Exception as e:
            logger.error("Pipedrive.query_activities failed: %s", e)
            return {"data": []}

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
