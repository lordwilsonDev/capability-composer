"""Live verification probe — one real, READ-ONLY call per provider.

The opt-in verification leg, cleanly separated from the zero-spend sandbox:

  SANDBOX path (verified, CI-safe, zero cost)  — composer.py + the skills'
  verify CLIs: deterministic in-memory backends, no network, no keys.

  LIVE path (THIS probe, opt-in) — run explicitly with credentials set.
  For every provider that has a key it makes EXACTLY ONE read-only call
  against the real API and writes a §8-shaped ledger evidence artifact to
  evidence/live/ proving the documented endpoint surface actually works
  against the real account.

Safety invariants:
- READ-ONLY endpoints only (search/list/get). Never creates, updates, books,
  or deletes anything on a real account. Write verification belongs to a
  manually-audited test on a dedicated sandbox account — deliberately out of
  scope here.
- A provider WITHOUT a key is SKIPPED (nothing attempted, never a failure).
- A provider WITH a key that fails its probe exits non-zero (fail loud).
- NO keys at all → exit 0, inert, no evidence written: the sandbox remains
  the verified path; this probe only upgrades trust when run.
- Never wired into CI (no keys there); CI stays zero-spend.

CLI:
  scripts/live_probe.py                      # probe every provider with a key
  scripts/live_probe.py --providers ghl      # probe only GHL
  scripts/live_probe.py --dry-run            # show what WOULD be probed (no network)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Script-mode bootstrap (repo root on sys.path for the `primitives` package).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from primitives.ghl.ghl_client import (
    LiveBackend as GhlLiveBackend,  # type: ignore[import-not-found]
)
from primitives.hubspot.hubspot_client import (
    LiveBackend as HubspotLiveBackend,  # type: ignore[import-not-found]
)

EVIDENCE_DIR = _REPO_ROOT / "evidence" / "live"
TOOLCHAIN = "capability-composer live-probe v1.0"

# provider -> (key env var, backend factory, one-call description)
PROVIDERS: dict[str, dict[str, Any]] = {
    "ghl": {
        "key_env": "GHL_API_KEY",
        "build": lambda: GhlLiveBackend(),
        "call": lambda b: b.search_contacts(limit=1),
        "endpoint": "POST /contacts/search (v3) — list 1 contact",
        "subject": "ghl.live.contacts.search",
        "claim": "ghl.live.reads_work",
    },
    "hubspot": {
        "key_env": "HUBSPOT_API_KEY",
        "build": lambda: HubspotLiveBackend(),
        "call": lambda b: b.search_contacts(limit=1),
        "endpoint": "POST /crm/v3/objects/contacts/search — list 1 contact",
        "subject": "hubspot.live.contacts.search",
        "claim": "hubspot.live.reads_work",
    },
}


def probe_provider(provider: str, backend: Any,
                   call: Callable[[Any], Any]) -> dict[str, Any]:
    """Make the ONE read-only call; return the probe result dict."""
    started = time.monotonic()
    try:
        call(backend)
        return {
            "provider": provider,
            "result": "PASS",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — any live failure is a FAIL
        return {
            "provider": provider,
            "result": "FAIL",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": str(exc)[:300],
        }


def git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _artifact_hash(artifact: dict[str, Any]) -> str:
    """Content-addressed: sha256 over the artifact with the hash field blanked."""
    payload = dict(artifact)
    payload["artifact_hash"] = ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def write_evidence(provider: str, probe: dict[str, Any],
                   git_head_value: str = "unknown",
                   evidence_dir: Path = EVIDENCE_DIR) -> Path:
    """Write the §8-shaped evidence artifact; returns its path."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifact = {
        "evidence_id": f"ev_live_{provider}_{time.time_ns()}",
        "subject_id": PROVIDERS[provider]["subject"],
        "claim_id": PROVIDERS[provider]["claim"],
        "evidence_type": "live_probe",
        "polarity": "SUPPORTING" if probe["result"] == "PASS" else "CONTRADICTING",
        "git_head": git_head_value,
        "artifact_hash": "",  # filled below — content-addressed
        "toolchain": TOOLCHAIN,
        "timestamp": ts,
        "result": probe["result"],
        "probe": {
            "provider": provider,
            "endpoint": PROVIDERS[provider]["endpoint"],
            "latency_ms": probe["latency_ms"],
            "error": probe["error"],
        },
        "provenance": {
            "execution": {"script": "scripts/live_probe.py", "mode": "live"},
            "environment": {"toolchain": TOOLCHAIN, "keys_present": [provider]},
            "input": {"call": "read-only", "limit": 1},
            "verifier": {"name": "live-probe", "version": "1.0"},
            "dependency": {},
        },
        "freshness": "FRESH",
    }
    artifact["artifact_hash"] = _artifact_hash(artifact)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{artifact['evidence_id']}.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    providers = list(PROVIDERS)
    if args and args[0] == "--providers":
        if len(args) < 2:
            print("usage: live_probe.py [--providers ghl [hubspot ...]] [--dry-run]")
            return 2
        requested = args[1:]
        unknown = [p for p in requested if p not in PROVIDERS]
        if unknown:
            print(f"unknown provider(s): {', '.join(unknown)} — "
                  f"valid: {', '.join(PROVIDERS)}")
            return 2
        providers = requested

    print(f"live probe — {len(providers)} provider(s): "
          f"{', '.join(providers)} (read-only, one call each)")

    failures = 0
    for provider in providers:
        spec = PROVIDERS[provider]
        if not os.getenv(spec["key_env"]):
            print(f"  [SKIP] {provider:8s} — {spec['key_env']} not set (sandbox stays the verified path)")
            continue
        if dry_run:
            print(f"  [DRY]  {provider:8s} — would call {spec['endpoint']}")
            continue
        backend = spec["build"]()
        probe = probe_provider(provider, backend, spec["call"])
        path = write_evidence(provider, probe, git_head(), EVIDENCE_DIR)
        mark = "PASS" if probe["result"] == "PASS" else "FAIL"
        if probe["result"] != "PASS":
            failures += 1
        try:
            shown = path.relative_to(_REPO_ROOT)
        except ValueError:
            shown = path
        print(f"  [{mark}]  {provider:8s} → {spec['endpoint']}")
        print(f"         latency={probe['latency_ms']}ms  evidence={shown}")
        if probe["error"]:
            print(f"         error: {probe['error']}")

    if failures:
        print(f"\n{failures} provider(s) FAILED — credentials or endpoint surface "
              f"need attention (evidence written with polarity CONTRADICTING)")
        return 1
    print("\nall keyed providers verified (or none had keys — probe is inert "
          "by design; run with credentials to upgrade trust)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
