"""Blueprint §21 — the fundamental primitive test.

  1. "create a GHL lead qualification agent" with all primitives present →
     compose → sandbox-verify → register v1.0 (status VERIFIED).
  2. The SAME request again → REUSE (Law 1), no rebuild, no re-verification
     of the compose path — served by the catalog.
  3. A genuine gap (missing primitive) → the compose aborts with a Law-3
     report. Nothing is registered.
  4. An undecomposable requirement → the gate fails honestly (exit 2).

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
REQUIREMENT = "create a ghl lead qualification agent"


def _pristine_seed() -> list[dict]:
    """The primitives-only seed, derived programmatically so a local §21 demo
    run (which registers the skill into the working registry) can never flip
    these tests: skill entries are stripped regardless of ambient state."""
    return [
        cap for cap in registry_tool.load_registry(
            ROOT / "registry" / "registry.json"
        )["capabilities"]
        if cap.get("kind") != "skill"
    ]


SEEDED_IDS = [cap["id"] for cap in _pristine_seed()]


@pytest.fixture()
def tmp_registry(tmp_path, monkeypatch):
    """A scratch registry seeded with the repo's primitives (never the skill)."""
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


def test_first_request_composes_and_registers(tmp_registry, capsys):
    assert "skill:gohighlevel-lead-qualifier" not in SEEDED_IDS, \
        "the skill must NOT be pre-registered or the compose path is theater"
    assert composer.run(REQUIREMENT) == 0
    out = capsys.readouterr().out
    assert "[7/7] register" in out
    assert "VERIFIED" in out
    entry = registry_tool.lookup("skill:gohighlevel-lead-qualifier", tmp_registry)
    assert entry is not None
    assert entry["status"] == "VERIFIED"
    assert entry["version"] == "1.0"
    assert set(entry["capabilities"]) == set(SEEDED_IDS)


def test_second_request_reuses_instead_of_rebuilding(tmp_registry, capsys):
    assert composer.run(REQUIREMENT) == 0
    first = capsys.readouterr().out
    assert "[7/7] register" in first
    # the same request again must hit REUSE, not the compose path
    assert composer.run(REQUIREMENT) == 0
    out = capsys.readouterr().out
    assert "REUSE (Law 1)" in out
    assert "skill:gohighlevel-lead-qualifier" in out
    assert "no build" in out
    assert "[7/7]" not in out  # never re-registered


def test_registration_is_idempotent(tmp_registry):
    assert composer.run(REQUIREMENT) == 0
    assert composer.run(REQUIREMENT) == 0
    registry = registry_tool.load_registry(tmp_registry)
    hits = [c for c in registry["capabilities"]
            if c["id"] == "skill:gohighlevel-lead-qualifier"]
    assert len(hits) == 1


def test_gap_aborts_with_law3_report(tmp_path, monkeypatch, capsys):
    """A missing primitive proves the gap and stops before composition."""
    reg = tmp_path / "registry.json"
    seed = {"schema_version": "1.0",
            "capabilities": [c for c in _pristine_seed()
                              if c["id"] != "llm.qualify"]}
    reg.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(composer, "find_for",
                        lambda req: registry_tool.find_for(req, reg))
    monkeypatch.setattr(composer, "register_capability",
                        lambda cap: registry_tool.register_capability(cap, reg))
    assert composer.run(REQUIREMENT) == 1
    out = capsys.readouterr().out
    assert "GAP (Law 3)" in out
    assert "llm.qualify" in out
    assert registry_tool.lookup("skill:gohighlevel-lead-qualifier", reg) is None, \
        "nothing may be registered when the gap is open"


def test_undecomposable_requirement_fails_the_gate(tmp_registry, capsys):
    assert composer.run("launch a rocket to mars") == 2
    assert "GATE FAILED" in capsys.readouterr().out
