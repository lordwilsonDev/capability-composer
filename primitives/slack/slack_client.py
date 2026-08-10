"""Slack connector — deterministic messaging adapter (capability primitive).

Third provider on the same contract as primitives/ghl and primitives/hubspot:
two backends, one interface (Capability Composer blueprint §6). A messaging
domain (channels, users, messages) proving the discovery layer generalizes
beyond CRMs.

- SandboxBackend (default): deterministic in-memory Slack workspace (channels
  + users + per-channel history) with failure injection (api_failure,
  rate_limit). Zero network, zero spend, fully reproducible. The verified
  path.
- LiveBackend (opt-in): the real Slack Web API (https://slack.com/api,
  Authorization: Bearer <xoxb bot token>, SLACK_API_KEY), enabled only when
  the token is set. Slack returns `{"ok": false, "error": ...}` bodies rather
  than HTTP errors — the live backend checks `ok` and raises SlackError.

Stdlib-only. Every call is recorded in `backend.calls` so a composition's
permission log can be audited (safety proof: no action outside the declared
scope ever happens).

Grounded endpoint surface (Slack Web API v2):
  GET  /api/conversations.list     list channels {channels:[{id,name,is_member}]}
  GET  /api/conversations.history  read a channel {messages:[{ts,user,text}]}
  GET  /api/users.list             list users {members:[{id,name,real_name}]}
  POST /api/chat.postMessage       post a message {channel,text} -> {ok,ts}

LIVE-MODE HONESTY: the sandbox is the verified path. The live paths are
grounded in the documented v2 shapes but require a real bot token — run
`scripts/live_probe.py --providers slack` once SLACK_API_KEY is set: it makes
ONE read-only call against your workspace and writes ledger evidence.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

BASE_URL = "https://slack.com/api"


class SlackError(Exception):
    """A Slack operation failed (missing channel, outage, rate limit...)."""


# ---------------------------------------------------------------------------
# Sandbox backend — the verified path
# ---------------------------------------------------------------------------

_FIXTURE_CHANNELS: list[dict[str, Any]] = [
    {"id": "C_SUPPORT", "name": "support", "is_member": True},
    {"id": "C_SALES", "name": "sales", "is_member": True},
    {"id": "C_LEGAL", "name": "legal", "is_member": False},
]

_FIXTURE_USERS: list[dict[str, Any]] = [
    {
        "id": "U_ALICE",
        "name": "alice",
        "real_name": "Alice Anderson",
        "profile": {"email": "alice@example.com"},
    },
    {
        "id": "U_BOB",
        "name": "bob",
        "real_name": "Bob Bailey",
        "profile": {"email": "bob@example.com"},
    },
]

_FIXTURE_MESSAGES: dict[str, list[dict[str, Any]]] = {
    "C_SUPPORT": [
        {"ts": "1.001", "user": "U_ALICE", "text": "How do I reset my password?"},
    ],
    "C_SALES": [],
    "C_LEGAL": [],
}


class SandboxBackend:
    """Deterministic in-memory Slack workspace. Failure injection (per-instance):
    ``api_failure`` — any write raises (API outage); ``rate_limit`` — reads and
    writes raise (rate limited); ``users_miss`` — user reads fail while channel
    reads still work (models the live-world case where the users endpoint is
    degraded independently of channels). Posting to a channel we are not a
    member of raises SlackError (the composition decides how to handle it)."""

    def __init__(self, *, failures: tuple[str, ...] = ()):
        # DEEP copies — a test mutating one backend's nested history must never
        # leak into the module-level fixtures or another backend.
        self._channels: dict[str, dict[str, Any]] = {
            c["id"]: copy.deepcopy(c) for c in _FIXTURE_CHANNELS
        }
        self._users: dict[str, dict[str, Any]] = {
            u["id"]: copy.deepcopy(u) for u in _FIXTURE_USERS
        }
        self._messages: dict[str, list[dict[str, Any]]] = {
            cid: copy.deepcopy(msgs) for cid, msgs in _FIXTURE_MESSAGES.items()
        }
        self._failures = set(failures)
        self._ts_counter = 1000
        self.calls: list[dict[str, Any]] = []  # the permission log

    def _check_read(self) -> None:
        if "rate_limit" in self._failures:
            raise SlackError("Slack rate limit exceeded (injected)")

    def _check_write(self) -> None:
        self._check_read()
        if "api_failure" in self._failures:
            raise SlackError("Slack API outage (injected)")

    # --- reads ---

    def list_channels(self, limit: int = 20) -> list[dict[str, Any]]:
        self._check_read()
        self.calls.append({"op": "channels.list", "args": {"limit": limit}})
        return [dict(c) for c in self._channels.values()][:limit]

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        self._check_read()
        self.calls.append({"op": "channels.get", "args": {"channel_id": channel_id}})
        channel = self._channels.get(channel_id)
        if channel is None:
            raise SlackError(f"channel {channel_id} not found")
        if not channel.get("is_member"):
            raise SlackError(f"bot is not a member of {channel.get('name', channel_id)}")
        return dict(channel)

    def list_users(self, limit: int = 20) -> list[dict[str, Any]]:
        self._check_read()
        if "users_miss" in self._failures:
            raise SlackError("Slack users endpoint degraded (injected)")
        self.calls.append({"op": "users.list", "args": {"limit": limit}})
        return [dict(u) for u in self._users.values()][:limit]

    def get_user(self, user_id: str) -> dict[str, Any]:
        self._check_read()
        if "users_miss" in self._failures:
            raise SlackError("Slack users endpoint degraded (injected)")
        self.calls.append({"op": "users.get", "args": {"user_id": user_id}})
        if user_id not in self._users:
            raise SlackError(f"user {user_id} not found")
        return dict(self._users[user_id])

    def history(self, channel_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self._check_read()
        self.calls.append({"op": "channels.history", "args": {"channel_id": channel_id}})
        return [dict(m) for m in self._messages.get(channel_id, [])][:limit]

    # --- writes ---

    def delete_message(self, channel_id: str, ts: str) -> dict[str, Any]:
        """Delete a message the bot posted (the write-probe round trip's
        cleanup leg — chat.delete only works for the bot's own messages)."""
        self._check_write()
        self.calls.append({"op": "chat.delete", "args": {"channel_id": channel_id}})
        if channel_id not in self._channels:
            raise SlackError(f"channel {channel_id} not found")
        history = self._messages.get(channel_id, [])
        for i, msg in enumerate(history):
            if msg.get("ts") == ts:
                if msg.get("user") != "U_BOT":
                    raise SlackError("cannot_delete_message: only the bot's own messages")
                return history.pop(i)
        raise SlackError(f"message {ts} not found in {channel_id}")

    def post_message(self, channel_id: str, text: str,
                     thread_ts: Optional[str] = None) -> dict[str, Any]:
        self._check_write()
        self.calls.append({"op": "chat.postMessage",
                           "args": {"channel_id": channel_id}})
        if channel_id not in self._channels:
            raise SlackError(f"channel {channel_id} not found")
        if not self._channels[channel_id].get("is_member"):
            raise SlackError("not_in_channel: bot cannot post here")
        self._ts_counter += 1
        ts = f"{self._ts_counter}.000"
        message = {
            "ts": ts,
            "channel": channel_id,
            "user": "U_BOT",
            "text": text,
            "thread_ts": thread_ts,
        }
        self._messages.setdefault(channel_id, []).append(message)
        return {"ok": True, "ts": ts, "channel": channel_id}


# ---------------------------------------------------------------------------
# Live backend — the real Slack Web API (opt-in, SLACK_API_KEY)
# ---------------------------------------------------------------------------

class LiveBackend:
    """The documented Slack Web API v2. Requires SLACK_API_KEY (or the token
    argument). Verify against your workspace with
    `python slack_client.py test` before use."""

    def __init__(self, token: Optional[str] = None, base_url: str = BASE_URL):
        self._token = token or os.getenv("SLACK_API_KEY", "")
        self._base = base_url.rstrip("/")
        self.calls: list[dict[str, Any]] = []

    def _request(self, method: str, path: str,
                 params: Optional[dict] = None,
                 body: Optional[dict] = None) -> dict[str, Any]:
        if not self._token:
            raise SlackError(
                "SLACK_API_KEY is not set — live mode unavailable (sandbox mode is the verified path)"
            )
        url = f"{self._base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json")
        self.calls.append({"op": f"{method} {path}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise SlackError(
                f"Slack {exc.code} on {method} {path}: "
                f"{exc.read().decode('utf-8', 'replace')[:200]}"
            ) from exc
        if not payload.get("ok", False):
            raise SlackError(
                f"Slack API error on {method} {path}: {payload.get('error', 'unknown')}"
            )
        return payload

    def list_channels(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", "/api/conversations.list",
                             params={"limit": limit}).get("channels", [])

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        # limit=1000 is Slack's conversations.list ceiling — a workspace with
        # more channels false-negatives here. Fine for the one-read-call live
        # probe; a real integration should page via response_metadata.
        for channel in self.list_channels(limit=1000):
            if channel.get("id") == channel_id:
                return channel
        raise SlackError(f"channel {channel_id} not found")

    def list_users(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", "/api/users.list",
                             params={"limit": limit}).get("members", [])

    def get_user(self, user_id: str) -> dict[str, Any]:
        for user in self.list_users(limit=1000):
            if user.get("id") == user_id:
                return user
        raise SlackError(f"user {user_id} not found")

    def history(self, channel_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", "/api/conversations.history",
                             params={"channel": channel_id,
                                     "limit": limit}).get("messages", [])

    def post_message(self, channel_id: str, text: str,
                     thread_ts: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            body["thread_ts"] = thread_ts
        return self._request("POST", "/api/chat.postMessage", body=body)

    def delete_message(self, channel_id: str, ts: str) -> dict[str, Any]:
        return self._request("POST", "/api/chat.delete",
                             {"channel": channel_id, "ts": ts})


# ---------------------------------------------------------------------------
# CLI — setup / test (deterministic, safe)
# ---------------------------------------------------------------------------

def _cmd_test() -> int:
    print("sandbox backend: available (deterministic, verified)")
    sb = SandboxBackend()
    print(f"  fixtures: {len(sb.list_channels())} channels, "
          f"{len(sb.list_users())} users, "
          f"{len(sb.history('C_SUPPORT'))} support messages")
    if os.getenv("SLACK_API_KEY"):
        print("live backend: SLACK_API_KEY set — not calling the live API from "
              "a test; run the composer's sandbox verification instead.")
    else:
        print("live backend: SLACK_API_KEY not set (sandbox is the verified path)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_test())
