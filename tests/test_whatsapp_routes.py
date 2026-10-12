"""Smoke tests for WhatsApp FastAPI bridge."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from whatsapp_routes import IncomingMessageBody


def test_incoming_message_body_accepts_from_alias():
    body = IncomingMessageBody.model_validate(
        {"org_id": "00000000-0000-4000-8000-000000000001", "from": "919999999999@s.whatsapp.net", "text": "Hi"}
    )
    assert body.from_ == "919999999999@s.whatsapp.net"
    assert body.text == "Hi"


def test_incoming_message_body_rejects_missing_from():
    with pytest.raises(ValidationError):
        IncomingMessageBody.model_validate({"org_id": "x", "text": "Hi"})