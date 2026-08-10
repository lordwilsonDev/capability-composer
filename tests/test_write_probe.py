"""The write probe's own tests — fake sandbox backends only, ZERO network.

This pins the WRITE-verification contract: the round trip is create ->
read-back verify -> delete -> verify-gone; evidence is only written for a
completed cycle (SUPPORTING) or a recorded failure (CONTRADICTING); the
probe is inert without sandbox keys; and — the structural safety core — a
PRODUCTION key present without its sandbox key is a REFUSAL (exit 2), so
the write probe can never be pointed at a real account by accident.
"""

from __future__ import annotations

import json

import scripts.write_probe as wprobe
from scripts.write_probe import (
    PRODUCTION_KEY_ENV,
    SANDBOX_KEY_ENV,
    main,
    run_round_trip,
    write_evidence,
)


class _FakeContactBackend:
    """Create/get/delete contact like the GHL/HubSpot sandbox stores."""

    def __init__(self, fail_delete=False, fail_readback=False):
        self._records: dict[str, dict] = {}
        self.fail_delete = fail_delete
        self.fail_readback = fail_readback

    def create_contact(self, payload):
        cid = f"c_{len(self._records) + 1}"
        record = dict(payload, id=cid)
        self._records[cid] = record
        return dict(record)

    def get_contact(self, cid):
        if cid not in self._records:
            raise RuntimeError(f"contact {cid} not found")
        if self.fail_readback:
            raise RuntimeError("read-back failed (injected)")
        return dict(self._records[cid])

    def delete_contact(self, cid):
        if cid not in self._records:
            raise RuntimeError(f"contact {cid} not found")
        if self.fail_delete:
            raise RuntimeError("delete refused (injected)")
        return self._records.pop(cid)


class _FakeSlackBackend:
    def __init__(self, fail_delete=False):
        self._messages: list[dict] = []
        self.fail_delete = fail_delete

    def post_message(self, channel, text):
        ts = f"{len(self._messages) + 1}.000"
        self._messages.append({"ts": ts, "channel": channel,
                               "user": "U_BOT", "text": text})
        return {"ok": True, "ts": ts, "channel": channel}

    def history(self, channel, limit=50):
        return [dict(m) for m in self._messages]

    def delete_message(self, channel, ts):
        if self.fail_delete:
            raise RuntimeError("chat.delete refused (injected)")
        for i, m in enumerate(self._messages):
            if m["ts"] == ts:
                return self._messages.pop(i)
        raise RuntimeError(f"message {ts} not found")


def test_contact_round_trip_succeeds():
    rt = run_round_trip("ghl", _FakeContactBackend(), "sandbox-key")
    assert rt["result"] == "PASS"
    assert rt["error"] is None
    assert rt["email"] and "@sandbox.invalid" in rt["email"]


def test_readback_mismatch_fails_loud():
    class _BadReadback(_FakeContactBackend):
        def get_contact(self, cid):
            return {"id": cid, "email": "wrong@example.com"}  # mismatch

    rt = run_round_trip("ghl", _BadReadback(), "k")
    assert rt["result"] == "FAIL"
    assert "read-back mismatch" in rt["error"]


def test_leave_behind_delete_fails_loud():
    rt = run_round_trip("hubspot", _FakeContactBackend(fail_delete=True), "k")
    assert rt["result"] == "FAIL"
    assert rt["error"] and "delete" in rt["error"]


def test_slack_round_trip_succeeds(monkeypatch):
    monkeypatch.setenv("SLACK_SANDBOX_CHANNEL", "C_TEST")
    rt = run_round_trip("slack", _FakeSlackBackend(), "sandbox-key")
    assert rt["result"] == "PASS"


def test_slack_without_channel_fails_loud(monkeypatch):
    monkeypatch.delenv("SLACK_SANDBOX_CHANNEL", raising=False)
    rt = run_round_trip("slack", _FakeSlackBackend(), "sandbox-key")
    assert rt["result"] == "FAIL"
    assert "SLACK_SANDBOX_CHANNEL" in rt["error"]


def test_evidence_polarity_follows_result(tmp_path):
    ok = run_round_trip("ghl", _FakeContactBackend(), "k")
    bad = run_round_trip("ghl", _FakeContactBackend(fail_delete=True), "k")
    p_ok = write_evidence("ghl", ok, "deadbeef", tmp_path)
    p_bad = write_evidence("ghl", bad, "deadbeef", tmp_path)
    ok_art = json.loads(p_ok.read_text())
    bad_art = json.loads(p_bad.read_text())
    assert ok_art["polarity"] == "SUPPORTING"
    assert ok_art["claim_id"] == "ghl.live.writes_work"
    assert ok_art["evidence_type"] == "write_probe"
    assert bad_art["polarity"] == "CONTRADICTING"
    assert ok_art["artifact_hash"] == wprobe._artifact_hash(ok_art)
    # §8 provenance layers present
    for layer in ("execution", "environment", "input", "verifier", "dependency"):
        assert layer in ok_art["provenance"]


def test_main_without_sandbox_keys_is_inert(tmp_path, monkeypatch, capsys):
    for var in SANDBOX_KEY_ENV.values():
        monkeypatch.delenv(var, raising=False)
    for var in PRODUCTION_KEY_ENV.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wprobe, "EVIDENCE_DIR", tmp_path)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.count("[SKIP]") == 3
    assert "inert" in out
    assert list(tmp_path.iterdir()) == [], "no sandbox keys → nothing attempted"


def test_main_dry_run_never_contacts_network_or_disk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GHL_SANDBOX_API_KEY", "test-key")
    monkeypatch.setattr(wprobe, "EVIDENCE_DIR", tmp_path)
    assert main(["--providers", "ghl", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[DRY]  ghl" in out
    assert "would run" in out
    assert list(tmp_path.iterdir()) == [], "dry-run writes nothing"


def test_production_key_without_sandbox_key_refuses(tmp_path, monkeypatch, capsys):
    """The structural safety core: a prod key present without its sandbox key
    is a REFUSAL — the write probe must never run against a real account."""
    monkeypatch.setenv("GHL_API_KEY", "prod-key")
    monkeypatch.delenv("GHL_SANDBOX_API_KEY", raising=False)
    for var in PRODUCTION_KEY_ENV.values():
        if var != "GHL_API_KEY":
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wprobe, "EVIDENCE_DIR", tmp_path)
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "[REFUSE]  ghl" in out
    assert "sandbox" in out
    assert list(tmp_path.iterdir()) == [], "refusal must write nothing"


def test_main_unknown_provider_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wprobe, "EVIDENCE_DIR", tmp_path)
    assert main(["--providers", "bogus"]) == 2
    out = capsys.readouterr().out
    assert "unknown provider(s): bogus" in out
    assert "valid: ghl, hubspot, slack" in out


def test_main_fails_loud_on_round_trip_failure(tmp_path, monkeypatch, capsys):
    """A keyed provider whose round trip fails exits 1 AND emits CONTRADICTING
    evidence — fail loud, never silent."""
    monkeypatch.setenv("GHL_SANDBOX_API_KEY", "sandbox-key")
    monkeypatch.delenv("HUBSPOT_SANDBOX_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_SANDBOX_API_KEY", raising=False)
    for var in PRODUCTION_KEY_ENV.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wprobe, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(wprobe, "_build_ghl", lambda key: _FakeContactBackend(fail_delete=True))
    assert main(["--providers", "ghl"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL]  ghl" in out
    files = list(tmp_path.iterdir())
    assert len(files) == 1, "a failing round trip must still emit evidence"
    artifact = json.loads(files[0].read_text(encoding="utf-8"))
    assert artifact["polarity"] == "CONTRADICTING"
