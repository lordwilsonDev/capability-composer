"""ghl-hubspot-router v1.0 — cross-connector composed capability (blueprint §13/§14).

The multi-provider edge proof: ONE workflow composing TWO connectors + the
SHARED model primitive —

    receive_leads → assess → verify_in_ghl → route_to_hubspot → confirm_in_ghl

Its input contract is the OUTPUT of the gohighlevel-lead-qualifier skill:
qualified GHL leads (contact + conversation). Skill #1 produces them; this
skill routes them into HubSpot. The capability graph now has real edges:
skill → skill, connector → connector, both sharing the llm.* model node.

Pipeline per lead:
1. receive_leads     — inbound (contact, conversation) pairs
2. assess            — llm.intent (spam/escalation quarantine) + llm.qualify (BANT gate)
3. verify_in_ghl     — ghl.contact.read: the lead must still exist upstream
4. route_to_hubspot  — hubspot.contact.read/write (match-or-create by email) +
                       hubspot.deal.read/write (create new or advance the open deal)
5. confirm_in_ghl    — ghl.contact.write: tag "synced-to-hubspot"

Failure semantics (the ones verification proves):
- A HubSpot failure leaves GHL untouched (no false confirmation) — "failed".
- A GHL confirm failure AFTER a successful route is "unconfirmed" — the deal
  exists in HubSpot, the sync is not confirmed; the partial state is recorded,
  never silently dropped.
- A missing/flagged lead is never routed.
- Spam is quarantined before any write. Nothing ever books twice: contacts
  and deals are search-then-update.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Script-mode bootstrap (same pattern as the other composed skills).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from primitives.ghl.ghl_client import (  # type: ignore[import-not-found]
    GhlError,
)
from primitives.ghl.ghl_client import (
    SandboxBackend as GhlSandboxBackend,
)
from primitives.hubspot.hubspot_client import (  # type: ignore[import-not-found]
    HubspotError,
)
from primitives.hubspot.hubspot_client import (
    SandboxBackend as HubspotSandboxBackend,
)
from primitives.stub_model.stub_model import StubModel  # type: ignore[import-not-found]

_SYNC_TAG = "synced-to-hubspot"


@dataclass
class LeadOutcome:
    """Per-lead decision record — deterministic, assertable, ledger-able."""

    lead_id: str
    action: str  # routed | advanced | skipped_unqualified | quarantined
                 #         | skipped_missing | failed | unconfirmed
    rationale: str = ""
    hubspot_contact_id: Optional[str] = None
    deal_id: Optional[str] = None
    error: Optional[str] = None


class LeadRouter:
    """receive_leads → assess → verify_in_ghl → route_to_hubspot → confirm_in_ghl."""

    def __init__(self, ghl: Any, hubspot: Any, model: Optional[Any] = None):
        self.ghl = ghl
        self.hubspot = hubspot
        self.model = model or StubModel()

    def route(self, leads: list[dict[str, Any]]) -> list[LeadOutcome]:
        """Route every lead; one outcome per lead, never a crash, never a
        duplicate contact/deal on either side."""
        return [self._route_one(lead) for lead in leads]

    # -- internals -----------------------------------------------------------

    def _route_one(self, lead: dict[str, Any]) -> LeadOutcome:
        lead_id = lead.get("id") or lead.get("email", "unknown")
        conversation = lead.get("conversation", "")

        intent = self.model.intent(conversation)["intent"]
        if intent == "spam":
            return LeadOutcome(lead_id, "quarantined", rationale="spam signals detected")
        if intent == "support_escalation":
            return LeadOutcome(lead_id, "skipped_unqualified",
                               rationale="escalation — handled upstream")

        qual = self.model.qualify(lead, conversation)
        if not qual["qualified"]:
            return LeadOutcome(lead_id, "skipped_unqualified", rationale=qual["rationale"])

        # verify_in_ghl — the lead must still exist upstream before any write
        try:
            self.ghl.get_contact(lead_id)
        except GhlError as exc:
            return LeadOutcome(lead_id, "skipped_missing", error=str(exc))

        # route_to_hubspot — match-or-create the contact, then create-or-advance
        try:
            hs_contact = self._match_or_create_contact(lead)
            deal, created = self._create_or_advance_deal(hs_contact, lead, conversation)
            routed_action = "routed" if created else "advanced"
        except HubspotError as exc:
            # the route failed → GHL is left untouched, no false confirmation
            return LeadOutcome(lead_id, "failed", error=str(exc))

        # confirm_in_ghl — tag the lead synced; a failure here is a partial
        # state (deal exists in HubSpot, sync unconfirmed) — recorded, never
        # silently dropped, never rolled back into a lie.
        try:
            self.ghl.update_contact(lead_id, {"tags": [_SYNC_TAG]})
            action = routed_action
            error = None
        except GhlError as exc:
            action = "unconfirmed"
            error = str(exc)

        return LeadOutcome(lead_id, action, rationale=qual["rationale"],
                           hubspot_contact_id=hs_contact.get("id"),
                           deal_id=deal.get("id"), error=error)

    def _match_or_create_contact(self, lead: dict[str, Any]) -> dict[str, Any]:
        email = lead.get("email") or ""
        hits = self.hubspot.search_contacts(email)
        if hits:
            return hits[0]
        return self.hubspot.create_contact({
            "firstname": lead.get("firstname") or lead.get("firstName", ""),
            "lastname": lead.get("lastname") or lead.get("lastName", ""),
            "email": email,
            "phone": lead.get("phone", ""),
        })

    def _create_or_advance_deal(self, hs_contact: dict[str, Any],
                                lead: dict[str, Any],
                                conversation: str) -> tuple[dict[str, Any], bool]:
        match = re.search(r"\$(\d[\d,]*)", conversation)
        amount = int(match.group(1).replace(",", "")) if match else 0
        open_deals = [
            d for d in self.hubspot.search_deals(hs_contact["id"])
            if d.get("status") == "open"
        ]
        if open_deals:
            deal = self.hubspot.update_deal(open_deals[0]["id"], {
                "amount": amount, "dealstage": "qualifiedtobuy",
            })
            return deal, False
        deal = self.hubspot.create_deal({
            "dealname": f"{lead.get('firstname', '')} — synced lead",
            "amount": amount,
            "pipeline": "default",
            "dealstage": "appointmentscheduled",
            "contactId": hs_contact["id"],
            "status": "open",
        })
        return deal, True


# ---------------------------------------------------------------------------
# Sandbox verification (blueprint §11/§12) — 8 adversarial scenarios
# ---------------------------------------------------------------------------

# (name, ghl backend, hubspot backend, leads, expected actions)
# Leads are (contact, conversation) — the OUTPUT contract of skill #1.
_QUALIFIED = ("We need this now, budget approved at $9000, I approve purchases "
              "for my team")
_SPAM = "buy now and double your money with guaranteed profit crypto"
_INCOMPLETE = "maybe interested"


def _lead(contact: dict[str, Any], conversation: str) -> dict[str, Any]:
    return {**contact, "conversation": conversation}


def _scenario_backends(name: str) -> tuple[GhlSandboxBackend, HubspotSandboxBackend]:
    # NOTE: match the FULL scenario names (they are the failure-injection keys)
    if name == "ghl_confirm_failure_unconfirmed":
        return GhlSandboxBackend(failures=("api_failure",)), HubspotSandboxBackend()
    if name == "hubspot_failure_failed":
        return GhlSandboxBackend(), HubspotSandboxBackend(failures=("api_failure",))
    return GhlSandboxBackend(), HubspotSandboxBackend()


SCENARIOS: list[tuple[str, list[dict[str, Any]], list[str]]] = [
    # (name, leads, expected actions)
    ("routes_new_lead_to_hubspot", [
        _lead({"id": "c_grace", "firstname": "Grace", "lastname": "Hopper",
               "email": "grace@example.com"}, _QUALIFIED),
    ], ["routed"]),  # grace exists in both CRMs but has no open deal → contact reused, deal created
    ("advances_existing_hubspot_deal", [
        _lead({"id": "c_ada", "firstname": "Ada", "lastname": "Lovelace",
               "email": "ada@example.com"}, _QUALIFIED),
    ], ["advanced"]),  # ada has open deal d_001 in HubSpot → advance, never duplicate
    ("creates_hubspot_contact_and_deal", [
        _lead({"id": "c_linus", "firstname": "Linus", "lastname": "Torvalds",
               "email": "linus@example.com"}, _QUALIFIED),
    ], ["routed"]),  # GHL-only lead (c_linus exists in GHL fixtures) → HubSpot
                     # contact created, then deal created — the multi-CRM edge
    ("skips_unqualified", [
        _lead({"id": "c_grace", "firstname": "Grace", "lastname": "Hopper",
               "email": "grace@example.com"}, _INCOMPLETE),
    ], ["skipped_unqualified"]),
    ("quarantines_spam", [
        _lead({"id": "c_ada", "firstname": "Ada", "lastname": "Lovelace",
               "email": "ada@example.com"}, _SPAM),
    ], ["quarantined"]),
    ("skips_missing_ghl_lead", [
        _lead({"id": "c_deleted", "firstname": "Gone", "email": "gone@example.com"},
              _QUALIFIED),
    ], ["skipped_missing"]),
    ("ghl_confirm_failure_unconfirmed", [
        _lead({"id": "c_ada", "firstname": "Ada", "lastname": "Lovelace",
               "email": "ada@example.com"}, _QUALIFIED),
    ], ["unconfirmed"]),  # route succeeded → GHL write outage → partial state, recorded
    ("hubspot_failure_failed", [
        _lead({"id": "c_grace", "firstname": "Grace", "lastname": "Hopper",
               "email": "grace@example.com"}, _QUALIFIED),
    ], ["failed"]),  # HubSpot outage → GHL untouched, no false confirmation
]


def run_scenarios() -> list[tuple[str, list[LeadOutcome], list[str]]]:
    results: list[tuple[str, list[LeadOutcome], list[str]]] = []
    for name, leads, expected in SCENARIOS:
        ghl, hubspot = _scenario_backends(name)
        outcomes = LeadRouter(ghl, hubspot).route(leads)
        results.append((name, outcomes, expected))
    return results


def verify_sandbox() -> list[str]:
    """Run the 8 adversarial scenarios; return failures (empty = pass)."""
    failures: list[str] = []
    for name, outcomes, expected in run_scenarios():
        got = [o.action for o in outcomes]
        if got != expected:
            failures.append(f"{name}: expected {expected}, got {got}")
    return failures


def _cmd_verify() -> int:
    # compute ONCE — the same run_scenarios() list feeds both the failure list
    # and the printed table (no double execution, no double-counted failures)
    results = run_scenarios()
    failures: list[str] = []
    for name, outcomes, expected in results:
        got = [o.action for o in outcomes]
        mark = "PASS" if got == expected else "FAIL"
        if mark == "FAIL":
            failures.append(f"{name}: expected {expected}, got {got}")
        detail = outcomes[0] if outcomes else None
        print(f"  [{mark}] {name:34s} → {got} "
              f"{'err=' + (detail.error or '-') if detail else ''}")
    if failures:
        print(f"\n{len(failures)} FAILED (zero spend):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nALL PASS — {len(SCENARIOS)} scenarios, zero spend")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_verify())
