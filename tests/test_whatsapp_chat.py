"""Tests for WhatsApp chat history helpers."""
from __future__ import annotations

from whatsapp_db import _decode_cursor, _digits_from_wa_id, _encode_cursor


def test_encode_decode_cursor_roundtrip():
    payload = {"last_message_at": "2026-07-05T10:00:00Z", "id": "abc-123"}
    token = _encode_cursor(payload)
    decoded = _decode_cursor(token)
    assert decoded == payload


def test_digits_from_wa_jid():
    assert _digits_from_wa_id("919876543210@s.whatsapp.net") == "919876543210"


def test_digits_from_plain_phone():
    assert _digits_from_wa_id("+919876543210").endswith("919876543210")