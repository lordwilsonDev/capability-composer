"""Capability Composer — the §21 loop, as a deterministic CLI (blueprint).

    composer.py run "<what i want>"   # the full loop
    composer.py status                # registry + verified skills

The loop enforces the five laws constitutionally:

  1. REUSE  — anti-reinvention gate first: `find_for` the requirement. A hit
             means "already exists, do not rebuild" (exit 0, no build).
  2. DECOMPOSE — requirement → the capability ids it needs (deterministic).
  3. DISCOVER — find each needed capability in the registry. Missing = GAP.
  4. PROVE THE GAP — a missing capability aborts the compose with a Law-3
             report (exit 1). We do NOT build primitives here; that is
             `system-connector`'s job, and it must be justified.
  5. COMPOSE — all primitives present → the composition plan (the skill).
  6. VERIFY BEFORE PROMOTE — run the sandbox scenarios; any failure aborts
             BEFORE registration (Law 4). Zero-spend, reproducible.
  7. REGISTER — upsert the verified skill into the registry (v1.0).
  8. COMPOUND — the next identical request hits REUSE, not the build path.

Stdlib-only. Deterministic. No network. Mirrors the constellation ethos:
probabilistic intelligence proposes, deterministic infrastructure decides.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Optional

from registry.registry_tool import (  # type: ignore[import-not-found]
    find_for,
    load_registry,
    register_capability,
)
from skills.ghl_hubspot_router.router import (  # type: ignore[import-not-found]
    verify_sandbox as verify_ghl_hubspot_router,
)
from skills.gohighlevel_lead_qualifier.qualifier import (  # type: ignore[import-not-found]
    verify_sandbox as verify_ghl_qualifier,
)
from skills.hubspot_deal_pipeline.pipeline import (  # type: ignore[import-not-found]
    verify_sandbox as verify_hubspot_pipeline,
)
from skills.slack_triage.triage import (  # type: ignore[import-not-found]
    verify_sandbox as verify_slack_triage,
)

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# The decomposer — requirement → capability ids (deterministic, keyword-gated)
# ---------------------------------------------------------------------------

# A requirement matches a decomposer when EVERY significant keyword appears.
# Each decomposer expands into the exact capability set the composition needs.
DECOMPOSERS: list[dict[str, Any]] = [
    # ORDER = SPECIFICITY: a cross-connector spec requires keywords from TWO
    # providers, so it is checked before the single-provider specs (all
    # keywords must be present for a match, so the more specific gate wins).
    {
        "name": "ghl-hubspot-router",
        "keywords": ["ghl", "hubspot"],
        "skill_id": "skill:ghl-hubspot-router",
        "skill_name": "ghl-hubspot-router",
        "version": "1.0",
        "purpose": "Route qualified GHL leads into HubSpot deals — match or create the HubSpot contact, create or advance the deal, confirm the sync back in GHL.",
        "path": "skills/ghl_hubspot_router",
        "inputs": ["leads"],
        "dependencies": ["GoHighLevel", "HubSpot", "local_model"],
        "permissions": {"requires": [
            "contact.read", "contact.write",  # GHL
            "crm.contacts.read", "crm.contacts.write",  # HubSpot
            "crm.deals.read", "crm.deals.write",  # HubSpot
        ]},
        "workflow": ["receive_leads", "assess", "verify_in_ghl", "route_to_hubspot", "confirm_in_ghl"],
        "verification": ["sandbox scenarios", "adversarial inputs", "merged permission log"],
        "needs": [
            "ghl.contact.read", "ghl.contact.write",
            "llm.intent", "llm.qualify",
            "hubspot.contact.read", "hubspot.contact.write",
            "hubspot.deal.read", "hubspot.deal.write",
        ],
    },
    {
        "name": "ghl-lead-qualifier",
        "keywords": ["ghl", "lead"],
        "skill_id": "skill:gohighlevel-lead-qualifier",
        "skill_name": "gohighlevel-lead-qualifier",
        "version": "1.0",
        "purpose": "Qualification of inbound GHL leads — route qualified leads toward appointment booking.",
        "path": "skills/gohighlevel_lead_qualifier",
        "inputs": ["contact", "conversation"],
        "dependencies": ["GoHighLevel", "local_model"],
        "permissions": {"requires": ["contact.read", "contact.write", "calendar.read", "appointment.create"]},
        "workflow": ["detect_intent", "qualify", "determine_readiness", "book_or_nurture", "update_crm"],
        "verification": ["sandbox scenarios", "adversarial inputs", "permission log"],
        "needs": [
            "ghl.contact.read", "ghl.contact.write", "ghl.calendar.read",
            "ghl.appointment.write", "llm.intent", "llm.qualify",
        ],
    },
    {
        "name": "slack-triage",
        "keywords": ["slack"],
        "skill_id": "skill:slack-triage",
        "skill_name": "slack-triage",
        "version": "1.0",
        "purpose": "Triage Slack messages — detect intent via the shared model, reply with a canned answer, escalate support, quarantine spam; degrade honestly on Slack failures.",
        "path": "skills/slack_triage",
        "inputs": ["message"],
        "dependencies": ["Slack", "local_model"],
        "permissions": {"requires": ["channels.read", "users.read", "message.write"]},
        "workflow": ["verify_channel", "resolve_user", "detect_intent", "decide", "reply_or_quarantine"],
        "verification": ["sandbox scenarios", "adversarial inputs", "permission log"],
        "needs": [
            "slack.channel.read", "slack.user.read", "slack.message.write",
            "llm.intent",
        ],
    },
    {
        "name": "hubspot-deal-pipeline",
        "keywords": ["hubspot", "deal"],
        "skill_id": "skill:hubspot-deal-pipeline",
        "skill_name": "hubspot-deal-pipeline",
        "version": "1.0",
        "purpose": "Track and update the HubSpot deal pipeline from inbound signals — match contacts, score deal fit, create or advance open deals.",
        "path": "skills/hubspot_deal_pipeline",
        "inputs": ["contact", "conversation"],
        "dependencies": ["HubSpot", "local_model"],
        "permissions": {"requires": ["crm.contacts.read", "crm.contacts.write", "crm.deals.read", "crm.deals.write"]},
        "workflow": ["detect_deal_signal", "match_contact", "assess_deal", "create_or_update_deal", "update_crm"],
        "verification": ["sandbox scenarios", "adversarial inputs", "permission log"],
        "needs": [
            "hubspot.contact.read", "hubspot.contact.write", "hubspot.deal.read",
            "hubspot.deal.write", "llm.intent", "llm.qualify",
        ],
    },
]


# Each composed skill brings its own sandbox verification (blueprint §12); the
# loop dispatches to the verifier of the skill being composed. A missing entry
# fails the gate — no skill is promoted without its own scenarios passing.
VERIFIERS: dict[str, Callable[[], list[str]]] = {
    "gohighlevel-lead-qualifier": verify_ghl_qualifier,
    "hubspot-deal-pipeline": verify_hubspot_pipeline,
    "ghl-hubspot-router": verify_ghl_hubspot_router,
    "slack-triage": verify_slack_triage,
}


def decompose(requirement: str) -> Optional[dict[str, Any]]:
    for spec in DECOMPOSERS:
        if all(k in requirement.lower() for k in spec["keywords"]):
            return spec
    # No decomposer matched — fail the gate honestly (Law 1 still applies).
    return None


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _discover(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (found_ids, missing_ids) for the spec's `needs`."""
    found, missing = [], []
    for cid in spec["needs"]:
        (found if find_for(cid) else missing).append(cid)
    return found, missing


def _skill_entry(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec["skill_id"],
        "kind": "skill",
        "name": spec["skill_name"],
        "version": spec["version"],
        "path": spec["path"],
        "purpose": spec["purpose"],
        "inputs": spec["inputs"],
        "capabilities": list(spec["needs"]),
        "dependencies": spec["dependencies"],
        "permissions": spec["permissions"],
        "workflow": spec["workflow"],
        "verification": spec["verification"],
        "composable": True,
        "status": "VERIFIED",
    }


def run(requirement: str) -> int:
    print(f"requirement: {requirement}\n")

    # 1. REUSE — the anti-reinvention gate.
    hit = find_for(requirement)
    if hit:
        print(f"[1/7] REUSE (Law 1): {hit.get('id')} v{hit.get('version', 'n/a')} "
              "already registered — no build.")
        print(f"      status={hit.get('status')} — the composition was verified once; "
              "this request is served by the catalog, not a rebuild.")
        return 0

    # 2. DECOMPOSE.
    spec = decompose(requirement)
    if spec is None:
        print("[1/7] GATE FAILED: no decomposer matches this requirement. "
              "Define the capability decomposition before composing (Law 2).")
        return 2
    print(f"[2/7] decompose: {spec['skill_name']} needs "
          f"{len(spec['needs'])} capabilities -> {', '.join(spec['needs'])}")

    # 3/4. DISCOVER + PROVE THE GAP.
    found, missing = _discover(spec)
    print(f"[3/7] discover: {len(found)}/{len(spec['needs'])} found in the registry")
    for cid in found:
        print(f"        ✓ {cid}")
    if missing:
        print("[4/7] GAP (Law 3): missing primitives — prove the gap, then build "
              "them minimally (system-connector) before composing:")
        for cid in missing:
            print(f"        ✗ {cid}")
        return 1
    print("[4/7] gap check: none — every required primitive is registered (Law 3 satisfied)")

    # 5. COMPOSE.
    print(f"[5/7] compose: {spec['skill_name']} = "
          f"{' + '.join(spec['needs'])} -> {spec['path']}/")
    skill_md = ROOT / spec["path"] / "SKILL.md"
    if not skill_md.exists():
        print(f"        ✗ composition artifact missing: {skill_md} — cannot promote")
        return 1

    # 6. VERIFY BEFORE PROMOTE.
    verifier = VERIFIERS.get(spec["skill_name"])
    if verifier is None:
        print("[6/7] GATE FAILED: no sandbox verifier registered for "
              f"{spec['skill_name']} — cannot promote (Law 4).")
        return 1
    print(f"[6/7] verify: running {spec['skill_name']}'s sandbox scenarios (zero spend)...")
    failures = verifier()
    if failures:
        print(f"        ✗ {len(failures)} scenario(s) failed — NOT registered (Law 4):")
        for f in failures:
            print(f"          - {f}")
        return 1
    print(f"        ✓ {len(failures) == 0 and 'all sandbox scenarios pass'}")

    # 7. REGISTER.
    registered = register_capability(_skill_entry(spec))
    print(f"[7/7] register: {'new' if registered else 'updated'} "
          f"{spec['skill_id']} v{spec['version']} (status=VERIFIED)")
    print(f"\ncomposition promoted: {spec['skill_name']} v{spec['version']}")
    print("re-requesting the same capability will now hit REUSE (Law 1) "
          "— the compounding primitive.")
    return 0


def status() -> int:
    registry = load_registry()
    print(f"capability registry: {len(registry['capabilities'])} entries")
    for cap in registry["capabilities"]:
        status_ = cap.get("status", "-")
        kind = cap.get("kind", "?")
        print(f"  {cap.get('id'):35s} [{kind:8s}] {status_}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    if not argv or argv[0] not in ("run", "status"):
        print(__doc__)
        return 2
    if argv[0] == "status":
        return status()
    return run(" ".join(argv[1:]) or "?")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
