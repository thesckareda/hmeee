"""In-process SSE fan-out for WhatsApp chat realtime updates."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("whatsapp_events")

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_lock = asyncio.Lock()


async def subscribe(org_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers[org_id].add(queue)
    return queue


async def unsubscribe(org_id: str, queue: asyncio.Queue) -> None:
    async with _lock:
        subs = _subscribers.get(org_id)
        if subs:
            subs.discard(queue)
            if not subs:
                _subscribers.pop(org_id, None)


def publish(org_id: str, event: dict[str, Any]) -> None:
    """Broadcast to all SSE listeners for an org (non-blocking)."""
    subs = _subscribers.get(org_id)
    if not subs:
        return
    for queue in list(subs):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.debug("whatsapp SSE queue full for org %s — dropping event", org_id)