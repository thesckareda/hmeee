const { supabase } = require("./supabaseClient");

const PYTHON_APP_URL = process.env.PYTHON_APP_URL || "http://tinglish-vk-realty:8000";
const INTERNAL_API_SECRET = process.env.WHATSAPP_INTERNAL_SECRET || "";

/** Extract E.164-ish digits from a Baileys JID (91999…@s.whatsapp.net). */
function jidToContact(jid) {
  if (!jid) return "";
  const local = String(jid).split("@")[0] || "";
  const digits = local.replace(/\D/g, "");
  return digits || local;
}

function previewText(content, messageType = "text") {
  const text = String(content || "").trim();
  if (text) return text.length > 120 ? `${text.slice(0, 117)}…` : text;
  if (messageType === "image") return "[Image]";
  if (messageType === "audio") return "[Audio]";
  if (messageType === "document") return "[Document]";
  return "[Message]";
}

async function getSessionByOrgId(orgId) {
  const { data, error } = await supabase
    .from("whatsapp_sessions")
    .select("id, org_id, phone_number, status")
    .eq("org_id", orgId)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function findExistingMessage(waMessageId) {
  if (!waMessageId) return null;
  const { data, error } = await supabase
    .from("whatsapp_messages")
    .select("id, conversation_id")
    .eq("wa_message_id", waMessageId)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function upsertConversation({
  orgId,
  whatsappNumberId,
  contactNumber,
  contactName,
  preview,
  messageAt,
  incrementUnread,
}) {
  const now = messageAt || new Date().toISOString();

  const { data: existing, error: fetchErr } = await supabase
    .from("whatsapp_conversations")
    .select("id, unread_count")
    .eq("whatsapp_number_id", whatsappNumberId)
    .eq("contact_number", contactNumber)
    .maybeSingle();
  if (fetchErr) throw fetchErr;

  const unread = incrementUnread
    ? (existing?.unread_count || 0) + 1
    : 0;

  const row = {
    org_id: orgId,
    whatsapp_number_id: whatsappNumberId,
    contact_number: contactNumber,
    contact_name: contactName || existing?.contact_name || null,
    last_message_preview: preview,
    last_message_at: now,
    unread_count: unread,
    updated_at: now,
  };

  const { data, error } = await supabase
    .from("whatsapp_conversations")
    .upsert(row, { onConflict: "whatsapp_number_id,contact_number" })
    .select("id, org_id, unread_count, last_message_at")
    .single();
  if (error) throw error;
  return data;
}

async function insertMessage({
  conversationId,
  direction,
  messageType,
  content,
  mediaUrl,
  sentBy,
  status,
  waMessageId,
  createdAt,
}) {
  const payload = {
    conversation_id: conversationId,
    direction,
    message_type: messageType || "text",
    content: content || null,
    media_url: mediaUrl || null,
    sent_by: sentBy,
    status: status || "sent",
    wa_message_id: waMessageId || null,
    created_at: createdAt || new Date().toISOString(),
  };

  const { data, error } = await supabase
    .from("whatsapp_messages")
    .insert(payload)
    .select("id, conversation_id, direction, sent_by, created_at")
    .single();
  if (error) {
    // Unique violation on wa_message_id — treat as dedup success
    if (error.code === "23505" && waMessageId) {
      const existing = await findExistingMessage(waMessageId);
      return existing;
    }
    throw error;
  }
  return data;
}

async function notifyPythonIngested(event) {
  try {
    await fetch(`${PYTHON_APP_URL}/api/whatsapp/internal/message-ingested`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Secret": INTERNAL_API_SECRET,
      },
      body: JSON.stringify(event),
    });
  } catch (err) {
    console.error("[whatsapp] failed to notify message-ingested:", err);
  }
}

/**
 * Persist an inbound or outbound message and update the conversation row.
 * Deduplicates on wa_message_id when Baileys retries/reconnects.
 */
async function ingestMessage({
  orgId,
  whatsappNumberId,
  contactJid,
  contactName,
  direction,
  messageType = "text",
  content,
  mediaUrl,
  sentBy,
  status = "sent",
  waMessageId,
  ts,
}) {
  if (waMessageId) {
    const dup = await findExistingMessage(waMessageId);
    if (dup) return { deduped: true, message: dup };
  }

  const contactNumber = jidToContact(contactJid);
  if (!contactNumber) throw new Error("contactJid required for ingest");

  const messageAt = ts ? new Date(ts).toISOString() : new Date().toISOString();
  const preview = previewText(content, messageType);
  const isInbound = direction === "inbound";

  const conversation = await upsertConversation({
    orgId,
    whatsappNumberId,
    contactNumber,
    contactName,
    preview,
    messageAt,
    incrementUnread: isInbound,
  });

  const message = await insertMessage({
    conversationId: conversation.id,
    direction,
    messageType,
    content,
    mediaUrl,
    sentBy: sentBy || (isInbound ? "contact" : "assistant"),
    status,
    waMessageId,
    createdAt: messageAt,
  });

  await notifyPythonIngested({
    org_id: orgId,
    whatsapp_number_id: whatsappNumberId,
    conversation_id: conversation.id,
    message_id: message?.id,
    direction,
    contact_number: contactNumber,
    sent_by: sentBy || (isInbound ? "contact" : "assistant"),
    preview,
    last_message_at: messageAt,
    unread_count: conversation.unread_count,
  });

  return { deduped: false, conversation, message };
}

module.exports = {
  jidToContact,
  previewText,
  getSessionByOrgId,
  ingestMessage,
  findExistingMessage,
};