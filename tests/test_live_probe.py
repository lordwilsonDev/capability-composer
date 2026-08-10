"""The live probe's own tests — fake live backends only, ZERO network.

The probe is opt-in (keys required) and its evidence contract is what these
tests pin: §8 shape, content-addressed hash, polarity semantics, inert
without keys, dry-run never touches the network or disk, fail-loud exits.
"""

from __future__ import annotations

import json

import scripts.live_probe as probe
from scripts.live_probe import (
    PROVIDERS,
    _artifact_hash,
    main,
    probe_provider,
    write_evidence,
)


class _OkBackend:
    def search_contacts(self, **kw):
        return [{"id": "c_1", "email": "ok@example.com"}]


class _FailBackend:
    def search_contacts(self, **kw):
        raise RuntimeError("boom: simulated live failure")


def _call(b):
    return b.search_contacts(limit=1)


def test_probe_success_result():
    p = probe_provider("ghl", _OkBackend(), _call)
    assert p["result"] == "PASS"
    assert p["latency_ms"] >= 0
    assert p["error"] is None


def test_probe_failure_result():
    p = probe_provider("ghl", _FailBackend(), _call)
    assert p["result"] == "FAIL"
    assert "boom" in p["error"]


def test_evidence_success_shape(tmp_path):
    p = probe_provider("ghl", _OkBackend(), _call)
    path = write_evidence("ghl", p, "deadbeef", tmp_path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["polarity"] == "SUPPORTING"
    assert artifact["result"] == "PASS"
    assert artifact["claim_id"] == "ghl.live.reads_work"
    assert artifact["subject_id"] == "ghl.live.contacts.search"
    assert artifact["git_head"] == "deadbeef"
    assert artifact["evidence_type"] == "live_probe"
    assert artifact["freshness"] == "FRESH"
    # §8 provenance layers present
    for layer in ("execution", "environment", "input", "verifier", "dependency"):
        assert layer in artifact["provenance"]
    # content-addressed: hash = sha256 of the artifact with the hash blanked
    assert artifact["artifact_hash"] == _artifact_hash(artifact)


def test_evidence_failure_polarity_is_contradicting(tmp_path):
    p = probe_provider("ghl", _FailBackend(), _call)
    path = write_evidence("ghl", p, "deadbeef", tmp_path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["polarity"] == "CONTRADICTING"
    assert artifact["result"] == "FAIL"
    assert "boom" in artifact["probe"]["error"]


def test_evidence_is_content_addressed(tmp_path, monkeypatch):
    # Freeze the clock so both writes are byte-identical inputs.
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    class _FrozenClock:
        def now(cls, tz=None):  # noqa: N805
            return _dt(2026, 1, 1, tzinfo=_tz.utc)

    monkeypatch.setattr(probe.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    monkeypatch.setattr(probe, "datetime", _FrozenClock)
    p = probe_provider("ghl", _OkBackend(), _call)
    a = json.loads(write_evidence("ghl", p, "deadbeef", tmp_path).read_text())
    b = json.loads(write_evidence("ghl", p, "deadbeef", tmp_path).read_text())
    # identical inputs → identical artifact, id, and hash — replay-idempotent
    assert a["artifact_hash"] == b["artifact_hash"]
    assert a["evidence_id"] == b["evidence_id"]
    assert a == b


def test_evidence_id_distinct_across_probes(tmp_path, monkeypatch):
    """Fresh probes get distinct evidence_ids — each run is a new observation."""
    clock = iter([1_700_000_000_000_000_000, 1_700_000_001_000_000_000])
    monkeypatch.setattr(probe.time, "time_ns", lambda: next(clock))
    p = probe_provider("ghl", _OkBackend(), _call)
    a = json.loads(write_evidence("ghl", p, "deadbeef", tmp_path).read_text())
    b = json.loads(write_evidence("ghl", p, "deadbeef", tmp_path).read_text())
    assert a["evidence_id"] != b["evidence_id"]


def test_main_without_keys_is_inert(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GHL_API_KEY", raising=False)
    monkeypatch.delenv("HUBSPOT_API_KEY", raising=False)
    monkeypatch.setattr(probe, "EVIDENCE_DIR", tmp_path)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.count("[SKIP]") == 2
    assert "inert" in out
    assert list(tmp_path.iterdir()) == [], "no keys → no evidence, nothing attempted"


def test_main_dry_run_never_contacts_network_or_disk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setenv("HUBSPOT_API_KEY", "test-key")
    monkeypatch.setattr(probe, "EVIDENCE_DIR", tmp_path)
    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("[DRY]") == 2
    assert "would call" in out
    assert list(tmp_path.iterdir()) == [], "dry-run writes nothing"


def test_main_provider_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.setattr(probe, "EVIDENCE_DIR", tmp_path)
    assert main(["--providers", "ghl", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[DRY]  ghl" in out
    assert "hubspot" not in out


def test_main_unknown_provider_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(probe, "EVIDENCE_DIR", tmp_path)
    assert main(["--providers", "bogus"]) == 2
    out = capsys.readouterr().out
    assert "unknown provider(s): bogus" in out
    assert "valid: ghl, hubspot" in out


def test_main_fails_loud_on_keyed_provider_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GHL_API_KEY", "test-key")
    monkeypatch.delenv("HUBSPOT_API_KEY", raising=False)
    monkeypatch.setattr(probe, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setitem(PROVIDERS["ghl"], "build", lambda: _FailBackend())
    assert main(["--providers", "ghl"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL]  ghl" in out
    files = list(tmp_path.iterdir())
    assert len(files) == 1, "a failing keyed provider must still emit evidence"
    artifact = json.loads(files[0].read_text(encoding="utf-8"))
    assert artifact["polarity"] == "CONTRADICTING"
