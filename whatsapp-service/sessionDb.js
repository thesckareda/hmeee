const { supabase } = require("./supabaseClient");

async function upsertSession(orgId, patch) {
  const { error } = await supabase.from("whatsapp_sessions").upsert(
    {
      org_id: orgId,
      ...patch,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "org_id" }
  );
  if (error) throw error;
}

async function getSession(orgId) {
  const { data, error } = await supabase
    .from("whatsapp_sessions")
    .select("id, org_id, phone_number, status, connected_at, expires_at")
    .eq("org_id", orgId)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function listResumableSessions() {
  const { data, error } = await supabase
    .from("whatsapp_sessions")
    .select("org_id, status, expires_at")
    .in("status", ["connected", "pending"]);
  if (error) throw error;
  const now = Date.now();
  return (data || []).filter((row) => {
    if (!row.expires_at) return row.status === "connected" || row.status === "pending";
    return new Date(row.expires_at).getTime() > now;
  });
}

module.exports = { upsertSession, getSession, listResumableSessions };