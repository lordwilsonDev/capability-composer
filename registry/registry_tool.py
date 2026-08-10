"""Capability registry tooling (blueprint §6/§17).

The registry is the machine-readable answer to "does this exist?" — the
anti-reinvention gate. `find_for` matches a required capability against ids,
names, purpose, and the capabilities each entry composes; a hit means REUSE,
a miss means the requirement is a genuine gap (Law 3).

CLI:
  registry_tool.py list
  registry_tool.py needs "<what i need>"     # anti-reinvention check
  registry_tool.py register <capability.json>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

REGISTRY = Path(__file__).resolve().parent / "registry.json"


def load_registry(path: Path = REGISTRY) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.0", "capabilities": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("capabilities"), list):
        raise ValueError(f"malformed registry: {path}")
    return data


def _text_of(cap: dict[str, Any]) -> str:
    """The searchable text of a registry entry. Missing keys become "" —
    never str(None), which would pollute matching text."""
    return " ".join(str(x) for x in (
        cap.get("id") or "", cap.get("name") or "", cap.get("purpose") or "",
        " ".join(cap.get("capabilities", [])),
        (cap.get("provider") or {}).get("name", ""),
    )).lower()


# Generic intent words never identify a capability — they must not block a
# reuse hit, nor manufacture one on their own.
_STOPWORDS = {
    "create", "build", "make", "agent", "system", "tool", "need", "want",
    "please", "help", "this", "that", "with", "agent", "from", "your",
}


def find_for(requirement: str, path: Path = REGISTRY) -> Optional[dict[str, Any]]:
    """Anti-reinvention lookup: the best registry hit for a requirement, or
    None = genuine gap. Exact id wins; else the first entry whose text
    contains every significant keyword (all-or-nothing, no false positives;
    stopwords filtered so intent verbs never block reuse)."""
    registry = load_registry(path)
    tokens = [t for t in requirement.lower().split()
              if len(t) >= 4 and t not in _STOPWORDS]
    for cap in registry["capabilities"]:
        if requirement.strip().lower() in str(cap.get("id", "")).lower():
            return cap
    for cap in registry["capabilities"]:
        text = _text_of(cap)
        if tokens and all(t in text for t in tokens):
            return cap
    return None


def lookup(capability_id: str, path: Path = REGISTRY) -> Optional[dict[str, Any]]:
    registry = load_registry(path)
    for cap in registry["capabilities"]:
        if cap.get("id") == capability_id:
            return cap
    return None


def _write_registry(registry: dict[str, Any], path: Path) -> None:
    """Atomic write (tmp + os.replace) — a crash never truncates the registry."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def register_capability(capability: dict[str, Any], path: Path = REGISTRY) -> bool:
    """Upsert by id — idempotent, never duplicates, atomic write. Returns True
    when new."""
    if not isinstance(capability.get("id"), str) or not capability["id"]:
        raise ValueError("capability requires an id")
    registry = load_registry(path)
    for i, cap in enumerate(registry["capabilities"]):
        if cap.get("id") == capability["id"]:
            registry["capabilities"][i] = capability
            _write_registry(registry, path)
            return False
    registry["capabilities"].append(capability)
    registry["capabilities"].sort(key=lambda c: c.get("id", ""))
    _write_registry(registry, path)
    return True


def _cmd_needs(requirement: str) -> int:
    hit = find_for(requirement)
    if hit:
        print(f"REUSE: {hit.get('id')} (v{hit.get('version', 'n/a')}) — already exists, do not rebuild")
        return 0
    print(f"GAP: no registered capability satisfies '{requirement}' — prove the gap, then build minimal (Law 3)")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    if not argv or argv[0] not in ("list", "needs", "register"):
        print(__doc__)
        return 2
    if argv[0] == "list":
        for cap in load_registry()["capabilities"]:
            print(f"  {cap.get('id')}  v{cap.get('version', 'n/a')}  [{cap.get('kind')}]")
        return 0
    if argv[0] == "needs":
        return _cmd_needs(" ".join(argv[1:]) or "?")
    if argv[0] == "register":
        if len(argv) < 2:
            print("usage: registry_tool.py register <capability.json>")
            return 2
        cap = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        new = register_capability(cap)
        print(f"{'registered' if new else 'updated'}: {cap['id']}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
