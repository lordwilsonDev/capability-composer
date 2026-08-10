"""Blueprint §21 — the fundamental primitive test, parametrized over EVERY
decomposable capability so the discovery layer's generalization is
machine-checked, not asserted once:

  1. first request → compose → sandbox-verify → register v1.0 (VERIFIED)
  2. the SAME request again → REUSE (Law 1), no rebuild
  3. a genuine gap → the compose aborts with a Law-3 report, nothing registers
  4. an undecomposable requirement → the gate fails honestly (exit 2)

The registry is redirected to a tmp copy so the real registry.json is never
mutated by tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import composer
from registry import registry_tool

ROOT = Path(__file__).resolve().parent.parent

# (requirement, skill_id, needs, gap_primitive)
REQUIREMENTS = [
    pytest.param(
        "create a ghl lead qualification agent",
        "skill:gohighlevel-lead-qualifier",
        {"ghl.contact.read", "ghl.contact.write", "ghl.calendar.read",
         "ghl.appointment.write", "llm.intent", "llm.qualify"},
        "ghl.contact.write",
        id="ghl-lead-qualifier",
    ),
    pytest.param(
        "create a hubspot deal pipeline agent",
        "skill:hubspot-deal-pipeline",
        {"hubspot.contact.read", "hubspot.contact.write", "hubspot.deal.read",
         "hubspot.deal.write", "llm.intent", "llm.qualify"},
        "hubspot.deal.write",
        id="hubspot-deal-pipeline",
    ),
]


def _pristine_seed() -> list[dict]:
    """The primitives-only seed, derived programmatically so a local §21 demo
    run (which registers skills into the working registry) can never flip
    these tests: skill entries are stripped regardless of ambient state."""
    return [
        cap for cap in registry_tool.load_registry(
            ROOT / "registry" / "registry.json"
        )["capabilities"]
        if cap.get("kind") != "skill"
    ]


SEEDED_IDS = [cap["id"] for cap in _pristine_seed()]

# Plain (requirement, skill_id) pairs derived from the single REQUIREMENTS
# source — for tests that iterate instead of parametrize.
REQUIREMENT_PAIRS = [(p.values[0], p.values[1]) for p in REQUIREMENTS]


@pytest.fixture()
def tmp_registry(tmp_path, monkeypatch):
    """A scratch registry seeded with the repo's primitives (never the skills)."""
    reg = tmp_path / "registry.json"
    seed = {"schema_version": "1.0", "capabilities": _pristine_seed()}
    reg.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    # redirect the composer's lookups/registration onto the scratch registry
    monkeypatch.setattr(composer, "find_for",
                        lambda req: registry_tool.find_for(req, reg))
    monkeypatch.setattr(composer, "register_capability",
                        lambda cap: registry_tool.register_capability(cap, reg))
    monkeypatch.setattr(composer, "load_registry",
                        lambda: registry_tool.load_registry(reg))
    return reg


@pytest.mark.parametrize("requirement,skill_id,needs,gap_primitive", REQUIREMENTS)
def test_first_request_composes_and_registers(tmp_registry, capsys,
                                              requirement, skill_id, needs,
                                              gap_primitive):
    assert skill_id not in SEEDED_IDS, \
        "the skill must NOT be pre-registered or the compose path is theater"
    assert composer.run(requirement) == 0
    out = capsys.readouterr().out
    assert "[7/7] register" in out
    assert "VERIFIED" in out
    entry = registry_tool.lookup(skill_id, tmp_registry)
    assert entry is not None
    assert entry["status"] == "VERIFIED"
    assert entry["version"] == "1.0"
    assert set(entry["capabilities"]) == needs


@pytest.mark.parametrize("requirement,skill_id,needs,gap_primitive", REQUIREMENTS)
def test_second_request_reuses_instead_of_rebuilding(tmp_registry, capsys,
                                                     requirement, skill_id,
                                                     needs, gap_primitive):
    assert composer.run(requirement) == 0
    first = capsys.readouterr().out
    assert "[7/7] register" in first
    # the same request again must hit REUSE, not the compose path
    assert composer.run(requirement) == 0
    out = capsys.readouterr().out
    assert "REUSE (Law 1)" in out
    assert skill_id in out
    assert "no build" in out
    assert "[7/7]" not in out  # never re-registered


@pytest.mark.parametrize("requirement,skill_id,needs,gap_primitive", REQUIREMENTS)
def test_registration_is_idempotent(tmp_registry, requirement, skill_id,
                                    needs, gap_primitive):
    assert composer.run(requirement) == 0
    assert composer.run(requirement) == 0
    registry = registry_tool.load_registry(tmp_registry)
    hits = [c for c in registry["capabilities"] if c["id"] == skill_id]
    assert len(hits) == 1


@pytest.mark.parametrize("requirement,skill_id,needs,gap_primitive", REQUIREMENTS)
def test_gap_aborts_with_law3_report(tmp_path, monkeypatch, capsys,
                                     requirement, skill_id, needs,
                                     gap_primitive):
    """A missing primitive proves the gap and stops before composition."""
    reg = tmp_path / "registry.json"
    seed = {"schema_version": "1.0",
            "capabilities": [c for c in _pristine_seed()
                              if c["id"] != gap_primitive]}
    reg.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(composer, "find_for",
                        lambda req: registry_tool.find_for(req, reg))
    monkeypatch.setattr(composer, "register_capability",
                        lambda cap: registry_tool.register_capability(cap, reg))
    assert composer.run(requirement) == 1
    out = capsys.readouterr().out
    assert "GAP (Law 3)" in out
    assert gap_primitive in out
    assert registry_tool.lookup(skill_id, reg) is None, \
        "nothing may be registered when the gap is open"


def test_undecomposable_requirement_fails_the_gate(tmp_registry, capsys):
    assert composer.run("launch a rocket to mars") == 2
    assert "GATE FAILED" in capsys.readouterr().out


def test_shared_model_primitive_gap_fails_both_capabilities(tmp_path, monkeypatch,
                                                            capsys):
    """The shared-node claim, machine-checked: removing the model primitive
    (llm.qualify) must open the SAME gap for EVERY capability that depends on
    it — the graph's shared node is not duplicated per skill."""
    reg = tmp_path / "registry.json"
    seed = {"schema_version": "1.0",
            "capabilities": [c for c in _pristine_seed()
                              if c["id"] != "llm.qualify"]}
    reg.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(composer, "find_for",
                        lambda req: registry_tool.find_for(req, reg))
    monkeypatch.setattr(composer, "register_capability",
                        lambda cap: registry_tool.register_capability(cap, reg))
    for requirement, skill_id in REQUIREMENT_PAIRS:
        assert composer.run(requirement) == 1, f"{skill_id}: gap must abort"
        out = capsys.readouterr().out
        assert "GAP (Law 3)" in out
        assert "llm.qualify" in out
        assert registry_tool.lookup(skill_id, reg) is None
