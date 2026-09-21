"""WhatsApp processing-lifecycle reactions (👀 → ✅/❌) + inbound-reaction intake filter.

The adapter opts into the base-adapter processing hooks (mirroring the Discord
adapter): 👀 when processing starts, swapped for ✅/❌ on completion (or removed
on CANCELLED) — each posted to the bridge ``/react`` endpoint, default-disabled
behind ``extra.reactions``. Inbound user reactions are dropped at intake so the
agent never sees "[Reaction: …]" as a prompt.
"""

import asyncio
from collections import OrderedDict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType, ProcessingOutcome
from gateway.session import SessionSource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_adapter(extra=None):
    """Create a WhatsAppAdapter with test attributes (bypass __init__)."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = MagicMock()
    adapter.config.extra = dict(extra or {})
    adapter._dm_policy = "pairing"
    adapter._allow_from = set()
    adapter._group_policy = "open"
    adapter._group_allow_from = set()
    adapter._mention_patterns = []
    adapter.gateway_runner = None
    adapter._owner_profile = None
    adapter._reaction_keys = OrderedDict()  # real __init__ owns this; __new__ bypass sets it
    adapter._http_session = MagicMock()  # truthy so _post_reaction reaches _bridge_req
    return adapter


def _dm_event(message_id="msg-1", raw_message=None):
    return MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.WHATSAPP, chat_id="15550001111@s.whatsapp.net", chat_type="dm"),
        message_id=message_id,
        raw_message=raw_message,
    )


def _reaction_bridge_data():
    """Bridge payload shape for an inbound user reaction (mediaType 'reaction')."""
    return {
        "messageId": "reaction-1",
        "chatId": "15550001111@s.whatsapp.net",
        "senderId": "15550001111@s.whatsapp.net",
        "senderName": "Gerhard",
        "isGroup": False,
        "hasMedia": False,
        "mediaType": "reaction",
        "nativeType": "reactionMessage",
        "body": "[Reaction: 👍 to 3EB0B430B7DE4EB1BEA1]",
        "nativeMetadata": {
            "reaction": {"text": "👍", "messageId": "3EB0B430B7DE4EB1BEA1",
                         "remoteJid": "15550001111@s.whatsapp.net", "participant": ""},
        },
        "readReceiptKey": {"remoteJid": "15550001111@s.whatsapp.net", "id": "reaction-1",
                           "participant": "15550001111@s.whatsapp.net", "fromMe": False},
        "timestamp": 1757650000,
    }


def _text_bridge_data():
    data = _reaction_bridge_data()
    data.update({"messageId": "text-1", "mediaType": "", "nativeType": "",
                 "body": "hello agent", "nativeMetadata": {}})
    return data


class _AsyncCM:
    """Minimal async context manager returning a fixed value."""

    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


def _base_complete_hook(adapter, event, outcome):
    """Invoke the INHERITED BasePlatformAdapter.on_processing_complete."""
    from gateway.platforms.base import BasePlatformAdapter

    return BasePlatformAdapter.on_processing_complete(adapter, event, outcome)


# ---------------------------------------------------------------------------
# Inbound-reaction intake filter
# ---------------------------------------------------------------------------

class TestInboundReactionFilter:
    def test_reaction_event_is_dropped_at_intake(self):
        adapter = _make_adapter()
        assert asyncio.run(adapter._build_message_event(_reaction_bridge_data())) is None

    def test_normal_text_event_still_builds(self):
        adapter = _make_adapter()
        event = asyncio.run(adapter._build_message_event(_text_bridge_data()))
        assert event is not None
        assert event.text == "hello agent"

    def test_outbound_reaction_emojis_are_configured(self):
        """The adapter opts into the base complete-hook swap via class emoji attrs."""
        from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

        assert WhatsAppAdapter._OK_EMOJI == "✅"
        assert WhatsAppAdapter._FAIL_EMOJI == "❌"
        assert WhatsAppAdapter._ACK_EMOJI == "👀"


# ---------------------------------------------------------------------------
# Reactions config gate
# ---------------------------------------------------------------------------

class TestReactionsEnabled:
    def test_default_disabled(self):
        assert _make_adapter()._reactions_enabled() is False

    @pytest.mark.parametrize("value", [True, "true", "1", "yes", "on"])
    def test_enabled_values(self, value):
        assert _make_adapter({"reactions": value})._reactions_enabled() is True

    @pytest.mark.parametrize("value", [False, "false", "0", "no", "off", "banana"])
    def test_disabled_values(self, value):
        assert _make_adapter({"reactions": value})._reactions_enabled() is False


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

class TestProcessingLifecycle:
    def test_on_processing_start_reacts_eyes_and_remembers_key(self):
        adapter = _make_adapter({"reactions": True})
        adapter._add_reaction = AsyncMock()
        adapter._remove_reaction = AsyncMock()
        key = {"remoteJid": "15550001111@s.whatsapp.net", "id": "msg-1",
               "participant": "15550001111@s.whatsapp.net", "fromMe": False}
        event = _dm_event(raw_message={"readReceiptKey": key})

        asyncio.run(adapter.on_processing_start(event))

        adapter._add_reaction.assert_awaited_once_with("15550001111@s.whatsapp.net", "msg-1", "👀")
        adapter._remove_reaction.assert_not_awaited()
        assert adapter._reaction_key_for("msg-1", "15550001111@s.whatsapp.net") == key

    def test_on_processing_start_disabled_is_noop(self):
        adapter = _make_adapter()
        adapter._add_reaction = AsyncMock()
        asyncio.run(adapter.on_processing_start(_dm_event()))
        adapter._add_reaction.assert_not_awaited()

    def test_base_complete_hook_swaps_eyes_for_ok(self):
        """With the emoji attrs set, the INHERITED complete-hook does remove→add ✅."""
        adapter = _make_adapter({"reactions": True})
        adapter._add_reaction = AsyncMock()
        adapter._remove_reaction = AsyncMock()

        asyncio.run(_base_complete_hook(adapter, _dm_event(), ProcessingOutcome.SUCCESS))

        adapter._remove_reaction.assert_awaited_once_with("15550001111@s.whatsapp.net", "msg-1")
        adapter._add_reaction.assert_awaited_once_with("15550001111@s.whatsapp.net", "msg-1", "✅")

    def test_base_complete_hook_fail_adds_cross(self):
        adapter = _make_adapter({"reactions": True})
        adapter._add_reaction = AsyncMock()
        adapter._remove_reaction = AsyncMock()

        asyncio.run(_base_complete_hook(adapter, _dm_event(), ProcessingOutcome.FAILURE))

        adapter._add_reaction.assert_awaited_once_with("15550001111@s.whatsapp.net", "msg-1", "❌")

    def test_base_complete_hook_cancelled_only_removes(self):
        adapter = _make_adapter({"reactions": True})
        adapter._add_reaction = AsyncMock()
        adapter._remove_reaction = AsyncMock()

        asyncio.run(_base_complete_hook(adapter, _dm_event(), ProcessingOutcome.CANCELLED))

        adapter._remove_reaction.assert_awaited_once()
        adapter._add_reaction.assert_not_awaited()

    def test_base_complete_hook_disabled_is_noop(self):
        adapter = _make_adapter()
        adapter._add_reaction = AsyncMock()
        adapter._remove_reaction = AsyncMock()

        asyncio.run(_base_complete_hook(adapter, _dm_event(), ProcessingOutcome.SUCCESS))

        adapter._remove_reaction.assert_not_awaited()
        adapter._add_reaction.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bridge /react plumbing
# ---------------------------------------------------------------------------

class TestReactionBridgePosts:
    def _patch_bridge_req(self, adapter, status=200):
        captured = {}

        resp = MagicMock()
        resp.status = status

        def fake_bridge_req(method, path, timeout, json=None):
            captured["method"], captured["path"], captured["json"] = method, path, json
            return _AsyncCM(resp)

        adapter._bridge_req = fake_bridge_req
        return captured

    @pytest.mark.asyncio
    async def test_add_reaction_posts_full_key(self):
        adapter = _make_adapter({"reactions": True})
        captured = self._patch_bridge_req(adapter)
        key = {"remoteJid": "15550001111@s.whatsapp.net", "id": "msg-1",
               "participant": "15550001111@s.whatsapp.net", "fromMe": False}
        adapter._remember_reaction_key("msg-1", key)

        await adapter._add_reaction("15550001111@s.whatsapp.net", "msg-1", "👀")

        assert captured["path"] == "react"
        assert captured["json"] == {"chatId": "15550001111@s.whatsapp.net", "key": key, "emoji": "👀"}

    @pytest.mark.asyncio
    async def test_remove_reaction_posts_empty_emoji(self):
        adapter = _make_adapter({"reactions": True})
        captured = self._patch_bridge_req(adapter)

        await adapter._remove_reaction("15550001111@s.whatsapp.net", "msg-1")

        # DM fallback key reconstruction when nothing was remembered.
        assert captured["json"]["key"]["id"] == "msg-1"
        assert captured["json"]["key"]["remoteJid"] == "15550001111@s.whatsapp.net"
        assert captured["json"]["emoji"] == ""

    @pytest.mark.asyncio
    async def test_http_failure_does_not_raise(self):
        adapter = _make_adapter({"reactions": True})
        self._patch_bridge_req(adapter, status=503)

        await adapter._add_reaction("15550001111@s.whatsapp.net", "msg-1", "👀")

    @pytest.mark.asyncio
    async def test_reaction_key_cache_is_bounded(self):
        adapter = _make_adapter({"reactions": True})
        for i in range(600):
            adapter._remember_reaction_key(f"m{i}", {"id": f"m{i}", "remoteJid": "x"})
        assert len(adapter._reaction_keys) <= 512
        assert "m599" in adapter._reaction_keys  # newest kept


# ---------------------------------------------------------------------------
# YAML config seeding
# ---------------------------------------------------------------------------

class TestYamlConfigSeeding:
    def test_reactions_seeded_into_extra(self):
        from plugins.platforms.whatsapp.adapter import _apply_yaml_config

        seeded = _apply_yaml_config({}, {"reactions": True})
        assert seeded == {"reactions": True}

    def test_reactions_absent_seeds_nothing(self):
        from plugins.platforms.whatsapp.adapter import _apply_yaml_config

        assert _apply_yaml_config({}, {"dm_policy": "allowlist"}) == {"dm_policy": "allowlist"}
