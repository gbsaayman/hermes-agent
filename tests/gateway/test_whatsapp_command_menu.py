"""WhatsApp interactive command menu (Phase 2): numbered menu + quote-reply selection.

Pins the adapter-local UX: ``/menu`` sends a numbered list; a quote-reply to that
menu message carrying a bare number is rewritten to the mapped slash command and
re-dispatched through the normal gateway path. Bare numbers in normal conversation
(no quote) must never trigger commands.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from plugins.platforms.whatsapp.adapter import WhatsAppAdapter, SendResult


@pytest.fixture(autouse=True)
def _whatsapp_open_optin(monkeypatch):
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
    from collections import OrderedDict
    adapter._command_menus = OrderedDict()
    adapter._chat_last_menu = {}
    adapter._command_menu_ttl = 24 * 3600.0
    adapter._menu_bare_window = 120.0
    return adapter


def _event(text, reply_to=None, chat_id="6281234567890@s.whatsapp.net"):
    source = SimpleNamespace(
        platform=Platform.WHATSAPP, chat_id=chat_id, chat_type="dm",
        user_id=chat_id, user_name="Owner", role_authorized=True,
    )
    return MessageEvent(
        text=text, message_type=MagicMock(), source=source,
        reply_to_message_id=reply_to, message_id="inbound-1",
    )


def test_menu_request_detected():
    adapter = _make_adapter()
    assert adapter._is_command_menu_request(_event("/menu")) is True
    assert adapter._is_command_menu_request(_event("  /MENU  ")) is True
    assert adapter._is_command_menu_request(_event("/menu now")) is False
    assert adapter._is_command_menu_request(_event("/commands")) is False
    assert adapter._is_command_menu_request(_event("menu")) is False


def test_menu_lists_stable_commands_with_tokens():
    adapter = _make_adapter()
    text, mapping = adapter._build_whatsapp_command_menu()
    assert mapping["1"] == "/new"
    assert mapping["6"] == "/restart"
    assert "Reply to this message with a number" in text
    assert "🆕" in text


def test_quote_reply_number_selects_command():
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", "6281234567890@s.whatsapp.net", mapping)

    cmd = adapter._pop_command_menu_selection(_event("2", reply_to="menu-msg-1"))

    assert cmd == "/status"


def test_selection_is_single_use():
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", "6281234567890@s.whatsapp.net", mapping)

    assert adapter._pop_command_menu_selection(_event("1", reply_to="menu-msg-1")) == "/new"
    assert adapter._pop_command_menu_selection(_event("1", reply_to="menu-msg-1")) is None


def test_bare_number_without_quote_is_ignored():
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", "6281234567890@s.whatsapp.net", mapping)

    assert adapter._pop_command_menu_selection(_event("2")) is None


def test_number_out_of_range_and_text_ignored():
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", "6281234567890@s.whatsapp.net", mapping)

    assert adapter._pop_command_menu_selection(_event("99", reply_to="menu-msg-1")) is None
    assert adapter._pop_command_menu_selection(_event("yes please", reply_to="menu-msg-1")) is None


def test_owner_prefixed_menu_request_and_selection():
    """Owner DMs carry the [owner reply] prefix; menu interactions must see through it."""
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", "6281234567890@s.whatsapp.net", mapping)

    assert adapter._is_command_menu_request(_event("[owner reply] /menu")) is True
    sel = adapter._pop_command_menu_selection(_event("[owner reply] 2", reply_to="menu-msg-1"))
    assert sel == "/status"


def test_menu_selection_blocked_from_other_chat():
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", "6281234567890@s.whatsapp.net", mapping)

    other = _event("2", reply_to="menu-msg-1", chat_id="9999999999@s.whatsapp.net")
    assert adapter._pop_command_menu_selection(other) is None


def test_menu_dispatch_rewrites_event_and_forwards():
    adapter = _make_adapter()
    handled = []

    async def fake_handle(event):
        handled.append(event.text)

    adapter.handle_message = fake_handle
    ev = _event("2", reply_to="menu-msg-1")

    asyncio.run(adapter._dispatch_menu_command(ev, "/status"))

    assert handled == ["/status"]


def test_send_command_menu_registers_mapping():
    adapter = _make_adapter()
    send_calls = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        send_calls.append((chat_id, content))
        return SendResult(success=True, message_id="sent-menu-1")

    adapter.send = fake_send
    ev = _event("/menu")

    asyncio.run(adapter._send_command_menu(ev))

    assert len(send_calls) == 1
    assert "Hermes command menu" in send_calls[0][1]
    assert "sent-menu-1" in adapter._command_menus
    assert adapter._command_menus["sent-menu-1"]["mapping"]["1"] == "/new"


def _make_adapter_with_live_menu(chat_id="6281234567890@s.whatsapp.net", age=0.0):
    adapter = _make_adapter()
    _, mapping = adapter._build_whatsapp_command_menu()
    adapter._remember_command_menu("menu-msg-1", chat_id, mapping)
    if age:
        menu = adapter._command_menus["menu-msg-1"]
        menu["ts"] = time.time() - age
    return adapter


def test_bare_number_selects_from_latest_chat_menu():
    """The user flow that actually happens: send /menu, then just send "2"."""
    adapter = _make_adapter_with_live_menu()

    cmd = adapter._pop_chat_menu_selection(_event("2"))

    assert cmd == "/status"


def test_bare_number_with_owner_prefix_selects():
    adapter = _make_adapter_with_live_menu()

    assert adapter._pop_chat_menu_selection(_event("[owner reply] 2")) == "/status"


def test_bare_number_outside_window_ignored():
    adapter = _make_adapter_with_live_menu(age=600.0)
    adapter._menu_bare_window = 120.0

    assert adapter._pop_chat_menu_selection(_event("2")) is None


def test_bare_number_inside_window_selects():
    adapter = _make_adapter_with_live_menu(age=60.0)
    adapter._menu_bare_window = 120.0

    assert adapter._pop_chat_menu_selection(_event("2")) == "/status"


def test_bare_number_selection_is_single_use():
    adapter = _make_adapter_with_live_menu()

    assert adapter._pop_chat_menu_selection(_event("1")) == "/new"
    assert adapter._pop_chat_menu_selection(_event("2")) is None  # menu consumed


def test_bare_number_from_other_chat_ignored():
    adapter = _make_adapter_with_live_menu()

    other = _event("2", chat_id="9999999999@s.whatsapp.net")
    assert adapter._pop_chat_menu_selection(other) is None


def test_non_number_text_is_not_a_selection():
    adapter = _make_adapter_with_live_menu()

    assert adapter._pop_chat_menu_selection(_event("what's 2+2?")) is None
    assert adapter._pop_chat_menu_selection(_event("/status")) is None


def test_no_live_menu_no_selection():
    adapter = _make_adapter()

    assert adapter._pop_chat_menu_selection(_event("2")) is None
