"""Self-tests for the Slack connector primitive (sandbox path)."""

from __future__ import annotations

import pytest

from primitives.slack.slack_client import SandboxBackend, SlackError


def test_fixture_reads():
    sb = SandboxBackend()
    assert len(sb.list_channels()) == 3
    assert len(sb.list_users()) == 2
    assert sb.get_channel("C_SUPPORT")["name"] == "support"
    assert sb.get_user("U_ALICE")["real_name"] == "Alice Anderson"
    assert len(sb.history("C_SUPPORT")) == 1


def test_list_channels_respects_limit():
    sb = SandboxBackend()
    assert len(sb.list_channels(limit=2)) == 2


def test_post_message_appears_in_history():
    sb = SandboxBackend()
    posted = sb.post_message("C_SUPPORT", "hello there")
    assert posted["ok"] is True
    assert posted["ts"]
    history = sb.history("C_SUPPORT")
    assert history[-1]["text"] == "hello there"
    assert history[-1]["user"] == "U_BOT"


def test_non_member_channel_raises():
    sb = SandboxBackend()
    with pytest.raises(SlackError, match="not a member"):
        sb.get_channel("C_LEGAL")
    with pytest.raises(SlackError, match="not_in_channel"):
        sb.post_message("C_LEGAL", "hello")


def test_unknown_channel_and_user_raise():
    sb = SandboxBackend()
    with pytest.raises(SlackError, match="not found"):
        sb.get_channel("C_NOPE")
    with pytest.raises(SlackError, match="not found"):
        sb.get_user("U_NOPE")


def test_failure_injection():
    sb = SandboxBackend(failures=("api_failure",))
    with pytest.raises(SlackError, match="outage"):
        sb.post_message("C_SUPPORT", "x")
    rl = SandboxBackend(failures=("rate_limit",))
    with pytest.raises(SlackError, match="rate limit"):
        rl.list_channels()


def test_permission_log_records_every_call():
    sb = SandboxBackend()
    sb.get_channel("C_SUPPORT")
    sb.get_user("U_ALICE")
    sb.post_message("C_SUPPORT", "hi")
    ops = [c["op"] for c in sb.calls]
    assert ops == ["channels.get", "users.get", "chat.postMessage"]
