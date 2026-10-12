# WhatsApp Chat History

## Overview

Inbound/outbound WhatsApp messages are persisted to Supabase (`whatsapp_conversations`, `whatsapp_messages`) by the Node `messageDb.js` module inside the Baileys `messages.upsert` handler and the outbound `sendMessage` path.

The FastAPI app (`whatsapp_chat_routes.py`) exposes org-scoped inbox APIs. The dashboard chat UI lives at:

- `/tinglish-vk-realty/whatsapp/chat/{number_id}`
- `/whatsapp/chat/{number_id}` (alias)

`number_id` is the `whatsapp_sessions.id` UUID for the linked line.

## Pagination

**Conversations** — cursor-based, sorted `last_message_at DESC`:

- `GET /api/whatsapp/{number_id}/conversations?search=&cursor=`
- Cursor encodes `{ last_message_at, id }` (base64 JSON).
- Page size: 30.

**Messages** — cursor-based, sorted `created_at DESC` from DB, reversed per page for chronological display:

- `GET /api/whatsapp/{number_id}/conversations/{conversation_id}/messages?cursor=`
- Scroll to top loads older messages when `next_cursor` is present.
- Page size: 40.

## Realtime

Primary mechanism: **SSE** (`GET /api/whatsapp/{number_id}/events`).

1. Node ingests a message → `POST /api/whatsapp/internal/message-ingested`
2. FastAPI `whatsapp_events.publish()` fans out to subscribed SSE clients for that `org_id`
3. Dashboard `whatsapp-chat.js` uses `apiFetch` + `ReadableStream` (supports Bearer auth)

**Fallback:** 5s polling if SSE disconnects.

Supabase Realtime is enabled on both tables (migration v8) for optional direct subscriptions later.

## Outbound / anti-ban

All sends go through `sendPolicy.js` + `rateLimiter.js`:

| Kind | Delay | Notes |
|------|-------|-------|
| `reply_inbound` | 0.8–3.5s + typing | AI reply in active 24h thread |
| `thread_reply` | 2–8s + typing | Human agent in existing thread |
| `cold_outbound` | 30–90s + typing | New number; daily cap (default 250) |

Sends outside **09:00–21:00** (configurable `WHATSAPP_WORK_*`) are rejected.

## Setup

1. Run `sql/supabase_migration_v8_whatsapp_chat.sql` in Supabase SQL Editor (after v7).
2. Restart `whatsapp-service` and `ui_server`.
3. Open Connections → **Chats** on a connected number.

## Human takeover

Composer in the thread panel → `POST .../conversations/{id}/messages` → proxied to Baileys `send` with `sent_by=human_agent`.

**New number:** header **Message new number** → `POST .../conversations/start?contact_number=` (cold outbound pacing).