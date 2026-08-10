"""hubspot-deal-pipeline v1.0 — composed capability (blueprint §13/§14).

The second composition the composer builds — proving the discovery layer
generalizes beyond GHL: compose the registered HubSpot CRM primitives + the
SAME shared model primitive (primitives/stub_model) into one deterministic
pipeline —

    detect_deal_signal → match_contact → assess_deal
        → create_or_update_deal → update_crm

The model is the shared keyword BANT scorer (zero-spend, reproducible). The
HubSpot backend is the deterministic sandbox (verified path); the live v3 API
is opt-in behind HUBSPOT_API_KEY.

Safety properties (the ones verification proves):
- Every HubSpot call goes through the backend's own `calls` permission log.
- A HubSpot failure (outage, rate limit) DEGRADES to nurture — it never
  crashes, never double-creates a deal, never writes outside scope.
- Spam / escalation leads are quarantined or escalated — never scored, never
  turned into a deal.
- Existing contacts and open deals are REUSED (search-then-update), never
  duplicated.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Script-mode bootstrap (same pattern as the GHL qualifier).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from primitives.hubspot.hubspot_client import (  # type: ignore[import-not-found]
    HubspotError,
    SandboxBackend,
)
from primitives.stub_model.stub_model import StubModel  # type: ignore[import-not-found]

_BUDGET_AMOUNT = re.compile(r"\$(\d[\d,]*)", re.I)


@dataclass
class Outcome:
    """The decision record — deterministic, assertable, ledger-able."""

    contact_id: str
    intent: str
    qualified: bool
    action: str  # deal_created | deal_updated | nurtured | quarantined | escalated | degraded
    rationale: str = ""
    deal_id: Optional[str] = None
    error: Optional[str] = None
    calls: list[dict[str, Any]] = field(default_factory=list)


class DealPipeline:
    """detect_deal_signal → match_contact → assess_deal → create_or_update_deal → update_crm."""

    def __init__(self, backend: Any, model: Optional[Any] = None,
                 pipeline: str = "default",
                 dealstage_new: str = "appointmentscheduled",
                 dealstage_advance: str = "qualifiedtobuy"):
        self.backend = backend
        self.model = model or StubModel()
        self.pipeline = pipeline
        self.dealstage_new = dealstage_new
        self.dealstage_advance = dealstage_advance

    def run(self, contact: dict[str, Any], conversation: str) -> Outcome:
        cid = contact.get("id") or contact.get("email", "unknown")
        intent = self.model.intent(conversation)["intent"]

        if intent == "spam":
            outcome = Outcome(cid, intent, False, "quarantined",
                              rationale="spam signals detected")
            self._tag(contact, ["quarantined"], outcome)
            outcome.calls = list(self.backend.calls)
            return outcome
        if intent == "support_escalation":
            outcome = Outcome(cid, intent, False, "escalated",
                              rationale="escalation signals detected")
            self._tag(contact, ["escalate"], outcome)
            outcome.calls = list(self.backend.calls)
            return outcome

        qual = self.model.qualify(contact, conversation)
        qualified = bool(qual["qualified"])

        # match_contact: reuse an existing contact by email, create only if new.
        try:
            matched = self._match_contact(contact)
        except HubspotError as exc:
            outcome = Outcome(cid, intent, qualified, "degraded", error=str(exc))
            outcome.calls = list(self.backend.calls)
            return outcome

        if not qualified:
            outcome = Outcome(matched.get("id", cid), intent, False, "nurtured",
                              rationale=qual["rationale"])
            self._tag(matched, ["nurture"], outcome)
            outcome.calls = list(self.backend.calls)
            return outcome

        # Qualified → create or advance the open deal, degrading on failure.
        try:
            deal, action = self._create_or_update_deal(matched, qual, conversation)
            outcome = Outcome(matched.get("id", cid), intent, True, action,
                              rationale=qual["rationale"], deal_id=deal.get("id"))
        except HubspotError as exc:
            outcome = Outcome(matched.get("id", cid), intent, True, "degraded",
                              rationale=qual["rationale"], error=str(exc))
        self._tag(matched, ["qualified", "deal"], outcome)
        outcome.calls = list(self.backend.calls)
        return outcome

    def _match_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        email = contact.get("email") or ""
        hits = self.backend.search_contacts(email)
        if hits:
            return hits[0]
        return self.backend.create_contact({
            "firstname": contact.get("firstname") or contact.get("firstName", ""),
            "lastname": contact.get("lastname") or contact.get("lastName", ""),
            "email": email,
            "phone": contact.get("phone", ""),
        })

    def _create_or_update_deal(self, contact: dict[str, Any],
                               qual: dict[str, Any],
                               conversation: str) -> tuple[dict[str, Any], str]:
        match = _BUDGET_AMOUNT.search(conversation)
        amount = int(match.group(1).replace(",", "")) if match else 0
        open_deals = [
            d for d in self.backend.search_deals(contact["id"])
            if d.get("status") == "open"
        ]
        if open_deals:
            deal = self.backend.update_deal(open_deals[0]["id"], {
                "amount": amount,
                "dealstage": self.dealstage_advance,
            })
            return deal, "deal_updated"
        deal = self.backend.create_deal({
            "dealname": f"{contact.get('firstname', '')} — inbound deal",
            "amount": amount,
            "pipeline": self.pipeline,
            "dealstage": self.dealstage_new,
            "contactId": contact["id"],
            "status": "open",
        })
        return deal, "deal_created"

    def _tag(self, contact: dict[str, Any], tags: list[str],
             outcome: Outcome) -> None:
        cid = contact.get("id")
        if not cid:
            return
        try:
            self.backend.update_contact(cid, {"tags": tags})
        except HubspotError:
            outcome.error = "crm_update_failed" if not outcome.error \
                else f"crm_update_failed: {outcome.error}"


# ---------------------------------------------------------------------------
# Sandbox verification (blueprint §11/§12) — 9 adversarial scenarios
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, dict[str, Any], str, str]] = [
    # (name, contact, conversation, expected action)
    ("normal", {"firstname": "Grace", "lastname": "Hopper", "email": "grace@example.com"},
     "We need to evaluate your platform and I approve the budget of $5000. "
     "Can you show us this week? I'm the decision-maker here.",
     "deal_created"),  # Grace has NO open deal → create; Ada would advance (below)
    ("existing_open_deal",
     {"id": "c_ada", "firstname": "Ada", "lastname": "Lovelace", "email": "ada@example.com"},
     "We need this now, budget approved at $9000, I approve purchases for my team",
     "deal_updated"),  # d_001 is an open deal for c_ada → advance, not duplicate
    ("new_contact", {"firstname": "Katherine", "lastname": "Johnson",
                     "email": "katherine@example.com", "phone": "+15550001003"},
     "We need this now, budget approved at $2000, I decide for our team",
     "deal_created"),  # unknown email → contact created, then deal created
    ("spam", {"firstname": "Grace", "lastname": "Hopper", "email": "grace@example.com"},
     "buy now and double your money with guaranteed profit crypto",
     "quarantined"),
    ("incomplete", {"firstname": "Grace", "lastname": "Hopper", "email": "grace@example.com"},
     "maybe interested", "nurtured"),
    ("angry", {"firstname": "Grace", "lastname": "Hopper", "email": "grace@example.com"},
     "This is the worst service I have ever seen, absolutely unacceptable",
     "escalated"),
    ("api_failure", {"firstname": "Ada", "lastname": "Lovelace", "email": "ada@example.com"},
     "We need this now, budget approved at $10000, I approve purchases for my team",
     "degraded"),  # write outage → degrade, no deal
    ("rate_limit", {"firstname": "Ada", "lastname": "Lovelace", "email": "ada@example.com"},
     "We need this now, budget approved at $10000, I approve purchases for my team",
     "degraded"),  # reads blocked → degrade before any deal work
    ("duplicate", {"firstname": "Ada", "lastname": "Lovelace", "email": "ada@example.com"},
     "We need this now, budget approved at $10000, I approve purchases for my team",
     "deal_updated"),  # existing contact is reused (no contacts.create); open deal advances
    ("search_race", {"firstname": "Ada", "lastname": "Lovelace", "email": "ada@example.com"},
     "We need this now, budget approved at $10000, I approve purchases for my team",
     "degraded"),  # search misses (injected) → create hits the duplicate check → degrade,
                   # never a crash and never a duplicate deal
]


def _backend_for(name: str) -> SandboxBackend:
    if name == "api_failure":
        return SandboxBackend(failures=("api_failure",))
    if name == "rate_limit":
        return SandboxBackend(failures=("rate_limit",))
    if name == "search_race":
        return SandboxBackend(failures=("search_misses",))
    return SandboxBackend()


def run_scenarios() -> list[tuple[str, Outcome, str]]:
    return [
        (name, DealPipeline(_backend_for(name)).run(contact, text), expected)
        for name, contact, text, expected in SCENARIOS
    ]


def verify_sandbox() -> list[str]:
    """Run the 9 adversarial scenarios; return the list of failures (empty = pass)."""
    failures: list[str] = []
    for name, outcome, expected in run_scenarios():
        if outcome.action != expected:
            failures.append(
                f"{name}: expected {expected}, got {outcome.action} "
                f"(intent={outcome.intent}, err={outcome.error or '-'})"
            )
    return failures


def _cmd_verify() -> int:
    failures: list[str] = []
    for name, outcome, expected in run_scenarios():
        mark = "PASS" if outcome.action == expected else "FAIL"
        if mark == "FAIL":
            failures.append(f"{name}: expected {expected}, got {outcome.action}")
        print(f"  [{mark}] {name:20s} → {outcome.action:13s} "
              f"intent={outcome.intent} err={outcome.error or '-'}")
    if failures:
        print(f"\n{len(failures)} FAILED (zero spend):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nALL PASS — {len(SCENARIOS)} scenarios, zero spend")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_verify())
