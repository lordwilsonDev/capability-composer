"""write_probe.py — the WRITE verification leg (dedicated sandbox accounts only).

The live probe (scripts/live_probe.py) proves the READ surface with one
read-only call per provider. This probe proves the WRITE surface — create,
read-back-verify, delete, verify-gone — against a DEDICATED SANDBOX account.

Safety invariants (structural, not aspirational):
- SANDBOX-ONLY KEYS. This probe reads `*_SANDBOX_API_KEY` env vars ONLY —
  never the production `*_API_KEY` vars. The production keys can be sitting
  in the environment and this probe still cannot touch production: the
  LiveBackends are constructed with the sandbox key explicitly, and a guard
  fails loud if a production key is set while its sandbox key is absent
  (that combination means "someone is about to think a prod-account write
  was a sandbox test").
- FULL ROUND TRIP ONLY. Evidence is written ONLY when the complete cycle
  succeeds: create -> get (read-back verify) -> delete -> get (verify gone).
  Any failure at any step writes CONTRADICTING evidence and exits non-zero
  (fail loud). A half-written record is a detectable incident, never a
  silent success.
- Never wired into CI (no sandbox keys there); CI stays zero-spend.
- Every write-probe round trip is idempotent-safe to re-run: the probe uses
  a fresh timestamped identity per run, so a re-run creates a new record
  and cleans it up.

Per-provider sandbox requirements (the operator must provision these):
  ghl      GHL_SANDBOX_API_KEY     a Location API key on a THROWAWAY sub-account
  hubspot  HUBSPOT_SANDBOX_API_KEY a Private App token on a THROWAWAY portal
  slack    SLACK_SANDBOX_API_KEY   a bot token whose app OWNS a test channel
           SLACK_SANDBOX_CHANNEL   the test channel id (chat.delete only works
                                   for the bot's own messages)

CLI:
  scripts/write_probe.py                      # round-trip every sandbox-keyed provider
  scripts/write_probe.py --providers ghl      # only GHL
  scripts/write_probe.py --dry-run            # show what WOULD run (no network)
  scripts/write_probe.py --self-test          # deterministic in-memory verification

Exit codes: 0 = all keyed providers round-tripped (or none had sandbox keys —
inert is not an incident); 1 = any provider failed any step (fail loud);
2 = bad arguments / a production key present without its sandbox key.
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
from typing import Any, Optional

# Script-mode bootstrap (repo root on sys.path for the `primitives` package).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from primitives.ghl.ghl_client import (  # type: ignore[import-not-found]
    LiveBackend as GhlLiveBackend,
)
from primitives.hubspot.hubspot_client import (  # type: ignore[import-not-found]
    LiveBackend as HubspotLiveBackend,
)
from primitives.slack.slack_client import (  # type: ignore[import-not-found]
    LiveBackend as SlackLiveBackend,
)

EVIDENCE_DIR = _REPO_ROOT / "evidence" / "write"
TOOLCHAIN = "capability-composer write-probe v1.0"

# The sandbox key for every provider, and its production counterpart. The
# write probe reads ONLY the sandbox vars; the production vars are checked
# for the refusal guard.
SANDBOX_KEY_ENV = {
    "ghl": "GHL_SANDBOX_API_KEY",
    "hubspot": "HUBSPOT_SANDBOX_API_KEY",
    "slack": "SLACK_SANDBOX_API_KEY",
}
PRODUCTION_KEY_ENV = {
    "ghl": "GHL_API_KEY",
    "hubspot": "HUBSPOT_API_KEY",
    "slack": "SLACK_API_KEY",
}

_SANDBOX_CONTACT = {
    "ghl": {"firstName": "WriteProbe", "lastName": "Sandbox",
            "email": None, "phone": "+15550009999"},
    "hubspot": {"firstname": "WriteProbe", "lastname": "Sandbox",
                "email": None, "phone": "+15550009999"},
}


def _timestamped_email(provider: str) -> str:
    return f"writeprobe.{provider}.{time.time_ns()}@sandbox.invalid"


# provider -> (backend factory with the sandbox key, round-trip runner)
def _build_ghl(key: str) -> Any:
    return GhlLiveBackend(api_key=key)


def _build_hubspot(key: str) -> Any:
    return HubspotLiveBackend(api_key=key)


def _build_slack(key: str, channel: str) -> Any:
    return SlackLiveBackend(token=key)


def _is_gone_error(exc: BaseException) -> bool:
    """True when an error means the resource no longer exists — the SUCCESS
    signal of the verify-gone leg.

    The sandbox backends raise '<id> not found'. The live backends surface
    gone differently per provider: HTTP 404 in the message ("GHL 404 on GET
    /contacts/x", "HubSpot 404 on ..."), Slack's ok:false convention
    (message_not_found / channel_not_found), and HubSpot's body text. All of
    those mean the delete worked — matching only the literal "not found"
    would mark a successful live cleanup as a FAIL.
    """
    text = str(exc).lower()
    markers = ("not found", "not_found", "does not exist", "no longer exists",
               "404")
    return any(m in text for m in markers)


def run_round_trip(provider: str, backend: Any, key: str) -> dict[str, Any]:
    """create -> get (read-back verify) -> delete -> get (verify gone).

    Returns the round-trip result dict; any exception becomes result FAIL
    with the error. The caller decides polarity from `result`.
    """
    started = time.monotonic()
    try:
        email = _timestamped_email(provider)
        if provider == "slack":
            channel = os.getenv("SLACK_SANDBOX_CHANNEL", "")
            if not channel:
                raise RuntimeError("SLACK_SANDBOX_CHANNEL not set — chat.delete "
                                   "needs the bot's own test channel")
            created = backend.post_message(channel, f"write-probe {time.time_ns()}")
            ts = created.get("ts")
            # read-back verify: the message must exist in history
            history = backend.history(channel, limit=50)
            if not any(m.get("ts") == ts for m in history):
                raise RuntimeError("read-back failed: posted message not in history")
            backend.delete_message(channel, ts)
            history = backend.history(channel, limit=50)
            if any(m.get("ts") == ts for m in history):
                raise RuntimeError("cleanup failed: deleted message still in history")
        else:
            payload = dict(_SANDBOX_CONTACT[provider])
            payload["email"] = email
            created = backend.create_contact(payload)
            cid = created.get("id")
            # read-back verify: the record must exist and carry the email
            fetched = backend.get_contact(cid)
            if (fetched.get("email") or "").lower() != email.lower():
                raise RuntimeError(f"read-back mismatch: expected {email}, "
                                   f"got {fetched.get('email')!r}")
            backend.delete_contact(cid)
            try:
                backend.get_contact(cid)
            except Exception as exc:  # noqa: BLE001 — gone is the success
                if not _is_gone_error(exc):
                    raise RuntimeError(f"cleanup verify failed: {exc}") from exc
            else:
                raise RuntimeError("cleanup failed: contact still fetchable "
                                   "after delete")
        return {
            "provider": provider,
            "result": "PASS",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": None,
            "email": email if provider != "slack" else None,
        }
    except Exception as exc:  # noqa: BLE001 — any live failure is a FAIL
        return {
            "provider": provider,
            "result": "FAIL",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": str(exc)[:300],
            "email": None,
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
    payload = dict(artifact)
    payload["artifact_hash"] = ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def write_evidence(provider: str, result: dict[str, Any],
                   git_head_value: str = "unknown",
                   evidence_dir: Path = EVIDENCE_DIR) -> Path:
    """Write the §8-shaped evidence artifact; returns its path."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    polarity = "SUPPORTING" if result["result"] == "PASS" else "CONTRADICTING"
    artifact = {
        "evidence_id": f"ev_write_{provider}_{time.time_ns()}",
        "subject_id": f"{provider}.live.write_roundtrip",
        "claim_id": f"{provider}.live.writes_work",
        "evidence_type": "write_probe",
        "polarity": polarity,
        "git_head": git_head_value,
        "artifact_hash": "",  # filled below — content-addressed
        "toolchain": TOOLCHAIN,
        "timestamp": ts,
        "result": result["result"],
        "probe": {
            "provider": provider,
            "mode": "sandbox-write-roundtrip",
            "latency_ms": result["latency_ms"],
            "error": result["error"],
            "email": result.get("email"),
        },
        "provenance": {
            "execution": {"script": "scripts/write_probe.py", "mode": "sandbox"},
            "environment": {"toolchain": TOOLCHAIN,
                            "sandbox_keys_present": [provider]},
            "input": {"call": "create->verify->delete->verify-gone"},
            "verifier": {"name": "write-probe", "version": "1.0"},
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
    if "--self-test" in args:
        return _run_self_test()
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    providers = list(SANDBOX_KEY_ENV)
    if args and args[0] == "--providers":
        if len(args) < 2:
            print("usage: write_probe.py [--providers ghl [hubspot ...]] "
                  "[--dry-run] [--self-test]")
            return 2
        requested = args[1:]
        unknown = [p for p in requested if p not in SANDBOX_KEY_ENV]
        if unknown:
            print(f"unknown provider(s): {', '.join(unknown)} — "
                  f"valid: {', '.join(SANDBOX_KEY_ENV)}")
            return 2
        providers = requested

    print(f"write probe — {len(providers)} provider(s): {', '.join(providers)} "
          "(sandbox-account write round trip, create->verify->delete)")

    # Structural safety: a production key set WITHOUT its sandbox key is a
    # refusal condition — it means prod credentials are present and the
    # operator could mistake a prod write for a sandbox test.
    refused = False
    for provider in providers:
        if os.getenv(PRODUCTION_KEY_ENV[provider]) and not os.getenv(SANDBOX_KEY_ENV[provider]):
            print(f"  [REFUSE]  {provider:8s} — {PRODUCTION_KEY_ENV[provider]} is set "
                   f"but {SANDBOX_KEY_ENV[provider]} is not; the write probe only "
                   f"touches dedicated sandbox accounts. Unset the prod key or set "
                   f"the sandbox key first.")
            refused = True
    if refused:
        return 2

    failures = 0
    for provider in providers:
        key = os.getenv(SANDBOX_KEY_ENV[provider], "")
        if not key:
            print(f"  [SKIP] {provider:8s} — {SANDBOX_KEY_ENV[provider]} not set "
                  f"(sandbox write verification requires a dedicated sandbox account)")
            continue
        if dry_run:
            endpoint = ("chat.postMessage+chat.delete" if provider == "slack"
                        else "contacts create+get+delete")
            print(f"  [DRY]  {provider:8s} — would run {endpoint} on the sandbox account")
            continue
        backend = {
            "ghl": _build_ghl(key),
            "hubspot": _build_hubspot(key),
            "slack": _build_slack(key, os.getenv("SLACK_SANDBOX_CHANNEL", "")),
        }[provider]
        result = run_round_trip(provider, backend, key)
        path = write_evidence(provider, result, git_head(), EVIDENCE_DIR)
        mark = "PASS" if result["result"] == "PASS" else "FAIL"
        if result["result"] != "PASS":
            failures += 1
        try:
            shown = path.relative_to(_REPO_ROOT)
        except ValueError:
            shown = path
        print(f"  [{mark}]  {provider:8s} → create->verify->delete "
              f"latency={result['latency_ms']}ms  evidence={shown}")
        if result["error"]:
            print(f"         error: {result['error']}")

    if failures:
        print(f"\n{failures} provider(s) FAILED — the write surface needs attention "
              f"(evidence written with polarity CONTRADICTING)")
        return 1
    print("\nall sandbox-keyed providers round-tripped (or none had keys — "
          "inert; write verification requires a dedicated sandbox account)")
    return 0


def _run_self_test() -> int:
    """Deterministic in-memory verification of the round-trip runner against
    fake sandbox backends (zero network)."""
    import tempfile

    class _FakeContactBackend:
        """Create/get/delete contact like the GHL/HubSpot sandbox stores."""

        def __init__(self, fail_delete=False):
            self._records: dict[str, dict] = {}
            self.fail_delete = fail_delete

        def create_contact(self, payload):
            cid = f"c_{len(self._records) + 1}"
            record = dict(payload, id=cid)
            self._records[cid] = record
            return dict(record)

        def get_contact(self, cid):
            if cid not in self._records:
                raise RuntimeError(f"contact {cid} not found")
            return dict(self._records[cid])

        def delete_contact(self, cid):
            if cid not in self._records:
                raise RuntimeError(f"contact {cid} not found")
            if self.fail_delete:
                raise RuntimeError("delete refused (injected)")
            return self._records.pop(cid)

    class _FakeSlackBackend:
        def __init__(self):
            self._messages: list[dict] = []

        def post_message(self, channel, text):
            ts = f"{len(self._messages) + 1}.000"
            self._messages.append({"ts": ts, "channel": channel, "user": "U_BOT", "text": text})
            return {"ok": True, "ts": ts, "channel": channel}

        def history(self, channel, limit=50):
            return [dict(m) for m in self._messages]

        def delete_message(self, channel, ts):
            for i, m in enumerate(self._messages):
                if m["ts"] == ts:
                    return self._messages.pop(i)
            raise RuntimeError(f"message {ts} not found")

    with tempfile.TemporaryDirectory(prefix="write-probe-self-test-") as tmp:
        root = Path(tmp)

        # Contact round trip succeeds end-to-end.
        rt = run_round_trip("ghl", _FakeContactBackend(), "sandbox-key")
        assert rt["result"] == "PASS", rt
        assert rt["error"] is None, rt

        # HubSpot uses the same contact shape.
        rt_h = run_round_trip("hubspot", _FakeContactBackend(), "sandbox-key")
        assert rt_h["result"] == "PASS", rt_h

        # Slack round trip succeeds (post -> history verify -> delete -> gone).
        os.environ["SLACK_SANDBOX_CHANNEL"] = "C_SANDBOX_TEST"
        try:
            rt_s = run_round_trip("slack", _FakeSlackBackend(), "sandbox-key")
        finally:
            os.environ.pop("SLACK_SANDBOX_CHANNEL", None)
        assert rt_s["result"] == "PASS", rt_s

        # A failed delete (leave-behind) is a FAIL with the error surfaced.
        rt_f = run_round_trip("ghl", _FakeContactBackend(fail_delete=True), "k")
        assert rt_f["result"] == "FAIL", rt_f
        assert rt_f["error"] and "delete" in rt_f["error"], rt_f

        # LIVE-shape gone error (HTTP 404 in the message, not the literal
        # 'not found') must count as a SUCCESSFUL cleanup — the live backends
        # raise 'GHL 404 on GET /contacts/x'; matching only 'not found'
        # would falsely FAIL a real-API round trip.
        class _Live404Backend(_FakeContactBackend):
            def get_contact(self, cid):
                if cid not in self._records:
                    raise RuntimeError(f"GHL 404 on GET /contacts/{cid}: {cid}")
                return dict(self._records[cid])

        rt_404 = run_round_trip("ghl", _Live404Backend(), "k")
        assert rt_404["result"] == "PASS", rt_404
        assert rt_404["error"] is None, rt_404

        # HubSpot live shape: 'HubSpot 404 on GET /crm/v3/objects/contacts/x'.
        class _Hub404Backend(_FakeContactBackend):
            def get_contact(self, cid):
                if cid not in self._records:
                    raise RuntimeError(
                        f"HubSpot 404 on GET /crm/v3/objects/contacts/{cid}: "
                        f"{cid} did not exist")
                return dict(self._records[cid])

        rt_h404 = run_round_trip("hubspot", _Hub404Backend(), "k")
        assert rt_h404["result"] == "PASS", rt_h404

        # A NON-gone live error on the VERIFY-GONE get (500) still fails loud
        # — never a false PASS. Read-back uses the first get; the 500 fires
        # on the second get (after delete), so it must reach the cleanup leg.
        class _ServerErrorBackend(_FakeContactBackend):
            def __init__(self):
                super().__init__()
                self._gets = 0

            def get_contact(self, cid):
                self._gets += 1
                if self._gets == 2:
                    raise RuntimeError(f"GHL 500 on GET /contacts/{cid}: internal")
                return super().get_contact(cid)

        rt_500 = run_round_trip("ghl", _ServerErrorBackend(), "k")
        assert rt_500["result"] == "FAIL", rt_500
        assert "cleanup verify failed" in rt_500["error"], rt_500

        # Evidence polarity follows the result.
        path_ok = write_evidence("ghl", rt, "deadbeef", root)
        path_fail = write_evidence("hubspot", rt_f, "deadbeef", root)
        ok_art = json.loads(path_ok.read_text())
        fail_art = json.loads(path_fail.read_text())
        assert ok_art["polarity"] == "SUPPORTING", ok_art
        assert ok_art["claim_id"] == "ghl.live.writes_work", ok_art
        assert ok_art["subject_id"] == "ghl.live.write_roundtrip", ok_art
        assert fail_art["polarity"] == "CONTRADICTING", fail_art
        assert fail_art["claim_id"] == "hubspot.live.writes_work", fail_art
        assert fail_art["probe"]["error"], fail_art
        assert ok_art["artifact_hash"] == _artifact_hash(ok_art)
        assert ok_art["evidence_id"] != fail_art["evidence_id"]

    print("self-test: PASS (contact + slack round trips, read-back verify, "
          "delete cleanup verify, leave-behind fail-loud, §8 evidence polarity)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
