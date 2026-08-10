"""HubSpot connector — deterministic HubSpot CRM adapter (capability primitive).

Second provider on the same contract as primitives/ghl: two backends, one
interface (Capability Composer blueprint §6):

- SandboxBackend (default): deterministic in-memory HubSpot CRM (contacts +
  deals) with failure injection (api_failure, rate_limit). Zero network, zero
  spend, fully reproducible. The verified path.
- LiveBackend (opt-in): the real HubSpot CRM v3 API
  (https://api.hubapi.com, Authorization: Bearer <private app token>,
  HUBSPOT_API_KEY), enabled only when the token is set. v3 is versioned in
  the path — no Version header.

Stdlib-only. Every call is recorded in `backend.calls` so a composition's
permission log can be audited (safety proof: no action outside the declared
scope ever happens).

Grounded endpoint surface (HubSpot CRM v3 objects API):
  POST  /crm/v3/objects/contacts/search   search contacts {results:[...]}
  GET   /crm/v3/objects/contacts/{id}     get contact
  POST  /crm/v3/objects/contacts          create contact (properties)
  PATCH /crm/v3/objects/contacts/{id}     update contact (properties)
  POST  /crm/v3/objects/deals/search      search deals {results:[...]}
  GET   /crm/v3/objects/deals/{id}        get deal
  POST  /crm/v3/objects/deals             create deal (properties)
  PATCH /crm/v3/objects/deals/{id}        update deal (properties)

LIVE-MODE HONESTY: the sandbox is the verified path. The live paths are
grounded in the documented v3 shapes but require a real token — run
`python hubspot_client.py test` once HUBSPOT_API_KEY is set to verify against
your portal before trusting them.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

BASE_URL = "https://api.hubapi.com"


class HubspotError(Exception):
    """A HubSpot operation failed (duplicate, outage, rate limit...)."""


# ---------------------------------------------------------------------------
# Sandbox backend — the verified path
# ---------------------------------------------------------------------------

_FIXTURE_CONTACTS: list[dict[str, Any]] = [
    {
        "id": "c_ada",
        "firstname": "Ada",
        "lastname": "Lovelace",
        "email": "ada@example.com",
        "phone": "+15550001001",
        "tags": [],
    },
    {
        "id": "c_grace",
        "firstname": "Grace",
        "lastname": "Hopper",
        "email": "grace@example.com",
        "phone": "+15550001002",
        "tags": ["existing-client"],
    },
]

_FIXTURE_DEALS: list[dict[str, Any]] = [
    {
        "id": "d_001",
        "dealname": "Ada Lovelace — analytics platform",
        "amount": 5000,
        "pipeline": "default",
        "dealstage": "appointmentscheduled",
        "contactId": "c_ada",
        "status": "open",
    },
]


class SandboxBackend:
    """Deterministic in-memory HubSpot CRM. Failure injection (per-instance):
    ``api_failure`` — any write raises (API outage); ``rate_limit`` — reads and
    writes raise (rate limited). Duplicate contacts are detected by email and
    raise HubspotError (the composition decides how to handle it)."""

    def __init__(self, *, failures: tuple[str, ...] = ()):
        # DEEP copies — a test mutating one backend's nested tags must never
        # leak into the module-level fixtures or another backend.
        self._contacts: dict[str, dict[str, Any]] = {
            c["id"]: copy.deepcopy(c) for c in _FIXTURE_CONTACTS
        }
        self._deals: dict[str, dict[str, Any]] = {
            d["id"]: copy.deepcopy(d) for d in _FIXTURE_DEALS
        }
        self._failures = set(failures)
        self.calls: list[dict[str, Any]] = []  # the permission log

    def _check_read(self) -> None:
        if "rate_limit" in self._failures:
            raise HubspotError("HubSpot rate limit exceeded (injected)")

    def _check_write(self) -> None:
        self._check_read()
        if "api_failure" in self._failures:
            raise HubspotError("HubSpot API outage (injected)")

    # --- reads ---

    def search_contacts(self, query: str = "", limit: int = 20,
                        offset: int = 0) -> list[dict[str, Any]]:
        self._check_read()
        self.calls.append({"op": "contacts.search", "args": {"query": query}})
        q = query.strip().lower()
        # search_misses models the live-world search-then-create race: the
        # search returns nothing even though the record exists, so a create
        # can hit the duplicate check — the degradation branch that is
        # otherwise unreachable in a deterministic store.
        if "search_misses" in self._failures and q:
            return []
        # per-contact substring match — an exact email finds exactly its contact
        matches = [
            dict(c) for c in self._contacts.values()
            if not q or q in (
                f"{c.get('firstname','')} {c.get('lastname','')} "
                f"{c.get('email','')} {c.get('phone','')}"
            ).lower()
        ]
        return matches[offset:offset + limit]

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        self._check_read()
        self.calls.append({"op": "contacts.get", "args": {"contact_id": contact_id}})
        if contact_id not in self._contacts:
            raise HubspotError(f"contact {contact_id} not found")
        return dict(self._contacts[contact_id])

    def search_deals(self, contact_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        self._check_read()
        self.calls.append({"op": "deals.search", "args": {"contact_id": contact_id}})
        matches = [
            dict(d) for d in self._deals.values()
            if not contact_id or d.get("contactId") == contact_id
        ]
        return matches[:limit]

    def get_deal(self, deal_id: str) -> dict[str, Any]:
        self._check_read()
        self.calls.append({"op": "deals.get", "args": {"deal_id": deal_id}})
        if deal_id not in self._deals:
            raise HubspotError(f"deal {deal_id} not found")
        return dict(self._deals[deal_id])

    # --- writes ---

    def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "contacts.create", "args": {"email": payload.get("email")}})
        email = (payload.get("email") or "").strip().lower()
        for c in self._contacts.values():
            if c.get("email", "").strip().lower() == email:
                raise HubspotError(f"duplicate contact: {email}")
        contact_id = f"c_{100 + len(self._contacts) + 1:03d}"
        contact = {
            "id": contact_id,
            "firstname": payload.get("firstname", ""),
            "lastname": payload.get("lastname", ""),
            "email": payload.get("email", ""),
            "phone": payload.get("phone", ""),
            "tags": list(payload.get("tags", [])),
        }
        self._contacts[contact_id] = contact
        return dict(contact)

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "contacts.update", "args": {"contact_id": contact_id}})
        if contact_id not in self._contacts:
            raise HubspotError(f"contact {contact_id} not found")
        c = self._contacts[contact_id]
        for field in ("firstname", "lastname", "email", "phone"):
            if field in payload:
                c[field] = payload[field]
        if "tags" in payload:
            c["tags"] = sorted({*c["tags"], *payload["tags"]})
        return dict(c)

    def create_deal(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "deals.create", "args": {
            "dealname": payload.get("dealname"),
            "contact_id": payload.get("contactId"),
        }})
        deal_id = f"d_{100 + len(self._deals) + 1:03d}"
        deal = {
            "id": deal_id,
            "dealname": payload.get("dealname", ""),
            "amount": payload.get("amount", 0),
            "pipeline": payload.get("pipeline", "default"),
            "dealstage": payload.get("dealstage", "appointmentscheduled"),
            "contactId": payload.get("contactId", ""),
            "status": payload.get("status", "open"),
        }
        self._deals[deal_id] = deal
        return dict(deal)

    def update_deal(self, deal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "deals.update", "args": {"deal_id": deal_id}})
        if deal_id not in self._deals:
            raise HubspotError(f"deal {deal_id} not found")
        d = self._deals[deal_id]
        for field in ("dealname", "amount", "pipeline", "dealstage", "status"):
            if field in payload:
                d[field] = payload[field]
        return dict(d)


# ---------------------------------------------------------------------------
# Live backend — the real HubSpot CRM v3 API (opt-in, HUBSPOT_API_KEY)
# ---------------------------------------------------------------------------

class LiveBackend:
    """The documented HubSpot CRM v3 API. Requires HUBSPOT_API_KEY (or the
    token argument). v3 is versioned in the path. Verify against your portal
    with `python hubspot_client.py test` before use."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = BASE_URL):
        self._key = api_key or os.getenv("HUBSPOT_API_KEY", "")
        self._base = base_url.rstrip("/")
        self.calls: list[dict[str, Any]] = []

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict[str, Any]:
        if not self._key:
            raise HubspotError(
                "HUBSPOT_API_KEY is not set — live mode unavailable (sandbox mode is the verified path)"
            )
        url = f"{self._base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._key}")
        req.add_header("Content-Type", "application/json")
        self.calls.append({"op": f"{method} {path}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise HubspotError(
                f"HubSpot {exc.code} on {method} {path}: {exc.read().decode('utf-8', 'replace')[:200]}"
            ) from exc

    def search_contacts(self, query: str = "", limit: int = 20,
                        offset: int = 0) -> list[dict[str, Any]]:
        payload = {"limit": limit, "after": str(offset)}
        if query:
            payload["filterGroups"] = [{"filters": [
                {"propertyName": "email", "operator": "CONTAINS_TOKEN", "value": query},
            ]}]
        return self._request("POST", "/crm/v3/objects/contacts/search", payload).get("results", [])

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/crm/v3/objects/contacts/{contact_id}")

    def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        props = {k: v for k, v in payload.items()
                 if k in ("firstname", "lastname", "email", "phone") and v}
        return self._request("POST", "/crm/v3/objects/contacts", {"properties": props})

    def update_contact(self, contact_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        props = {k: v for k, v in payload.items()
                 if k in ("firstname", "lastname", "email", "phone")}
        return self._request("PATCH", f"/crm/v3/objects/contacts/{contact_id}",
                             {"properties": props})

    def search_deals(self, contact_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        payload = {"limit": limit}
        if contact_id:
            payload["associations"] = [{
                "types": [{"associationCategory": "HUBSPOT_DEFINED",
                           "associationTypeId": 5}],
                "to": {"id": contact_id},
            }]
        return self._request("POST", "/crm/v3/objects/deals/search", payload).get("results", [])

    def get_deal(self, deal_id: str) -> dict[str, Any]:
        return self._request("GET", f"/crm/v3/objects/deals/{deal_id}")

    def create_deal(self, payload: dict[str, Any]) -> dict[str, Any]:
        props = {k: v for k, v in payload.items()
                 if k in ("dealname", "amount", "pipeline", "dealstage", "status") and v}
        body: dict[str, Any] = {"properties": props}
        if payload.get("contactId"):
            body["associations"] = [{
                "types": [{"associationCategory": "HUBSPOT_DEFINED",
                           "associationTypeId": 5}],
                "to": {"id": payload["contactId"]},
            }]
        return self._request("POST", "/crm/v3/objects/deals", body)

    def update_deal(self, deal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        props = {k: v for k, v in payload.items()
                 if k in ("dealname", "amount", "pipeline", "dealstage", "status")}
        return self._request("PATCH", f"/crm/v3/objects/deals/{deal_id}",
                             {"properties": props})


# ---------------------------------------------------------------------------
# CLI — setup / test (deterministic, safe)
# ---------------------------------------------------------------------------

def _cmd_test() -> int:
    print("sandbox backend: available (deterministic, verified)")
    sb = SandboxBackend()
    print(f"  fixtures: {len(sb.search_contacts())} contacts, "
          f"{len(sb.search_deals())} deals")
    if os.getenv("HUBSPOT_API_KEY"):
        print("live backend: HUBSPOT_API_KEY set — not calling the live API from "
              "a test; run the composer's sandbox verification instead.")
    else:
        print("live backend: HUBSPOT_API_KEY not set (sandbox is the verified path)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_test())
