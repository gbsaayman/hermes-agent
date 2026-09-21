"""Tests for WhatsApp owner-message metadata and source-level text tagging.

The Node bridge sets ``fromOwner: true`` on inbound `fromMe` messages that
look owner-typed (linked-device send, not echoed from /send) when the
operator opts into ``WHATSAPP_FORWARD_OWNER_MESSAGES``.  These tests pin
the adapter's responsibility: lift that flag onto
``MessageEvent.metadata["whatsapp_from_owner"]``, prefix ``MessageEvent.text``
with ``[owner reply] ``, and otherwise leave metadata absent and text
unchanged.  The env-var gate itself lives in the bridge — the adapter just
trusts the payload.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from plugins.platforms.whatsapp.adapter import WhatsAppAdapter


@pytest.fixture(autouse=True)
def _whatsapp_open_optin(monkeypatch):
    """Opt into WhatsApp allow-all so ``dm_policy: open`` dispatch tests run.

    The adapter fails closed on ``open`` without an allow-all opt-in
    (SECURITY.md 2.6); these owner-DM tests set ``_dm_policy = "open"``.
    """
    monkeypatch.setenv("WHATSAPP_ALLOW_ALL_USERS", "true")


def _make_adapter():
    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = PlatformConfig(enabled=True)
    adapter._message_handler = AsyncMock()
    adapter._dm_policy = "open"
    adapter._allow_from = set()
    adapter._group_policy = "open"
    adapter._group_allow_from = set()
    adapter._mention_patterns = []
    adapter._free_response_chats = set()
    adapter._whatsapp_free_response_chats = lambda: set()
    return adapter


def _dm_payload(**overrides):
    payload = {
        "messageId": "M1",
        "chatId": "6281234567890@s.whatsapp.net",
        "senderId": "6281234567890@s.whatsapp.net",
        "senderName": "Customer",
        "chatName": "Customer",
        "isGroup": False,
        "body": "hi from the linked phone",
        "hasMedia": False,
        "mediaType": "",
        "mediaUrls": [],
        "mentionedIds": [],
        "quotedParticipant": "",
        "botIds": [],
        "timestamp": 0,
    }
    payload.update(overrides)
    return payload


def test_metadata_flag_set_when_payload_has_from_owner():
    adapter = _make_adapter()
    payload = _dm_payload(fromOwner=True)

    event = asyncio.run(adapter._build_message_event(payload))

    assert event is not None
    assert event.metadata.get("whatsapp_from_owner") is True
    assert event.source.role_authorized is True
    assert event.text.startswith("[owner reply] ")
    assert event.text == "[owner reply] hi from the linked phone"


def test_from_owner_does_not_double_prefix_when_already_tagged():
    adapter = _make_adapter()
    payload = _dm_payload(
        fromOwner=True,
        body="[owner reply] already tagged",
    )

    event = asyncio.run(adapter._build_message_event(payload))

    assert event is not None
    assert event.metadata.get("whatsapp_from_owner") is True
    assert event.source.role_authorized is True
    assert event.text == "[owner reply] already tagged"


def test_ordinary_message_is_not_role_authorized():
    adapter = _make_adapter()

    event = asyncio.run(adapter._build_message_event(_dm_payload()))

    assert event is not None
    assert event.metadata.get("whatsapp_from_owner") is None
    assert event.source.role_authorized is False


def test_owner_slash_command_is_not_prefixed():
    """Owner slash commands stay pristine: a prefix would hide the leading "/"
    from MessageEvent.is_command() and leak control traffic to the model."""
    adapter = _make_adapter()

    event = asyncio.run(adapter._build_message_event(_dm_payload(fromOwner=True, body="/new")))

    assert event is not None
    assert event.text == "/new"
    assert event.is_command() is True
    assert event.get_command() == "new"
    assert event.source.role_authorized is True
    assert event.metadata.get("whatsapp_from_owner") is True


def test_owner_slash_command_with_leading_whitespace_is_not_prefixed():
    adapter = _make_adapter()

    event = asyncio.run(adapter._build_message_event(_dm_payload(fromOwner=True, body="  /stop")))

    assert event is not None
    assert event.is_command() is True
    assert event.get_command() == "stop"
    assert event.metadata.get("whatsapp_from_owner") is True


