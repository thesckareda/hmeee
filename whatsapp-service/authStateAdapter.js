// Postgres-backed replacement for Baileys' useMultiFileAuthState.
//
// Baileys expects an AuthenticationState shaped as { creds, keys }, where `keys`
// implements SignalKeyStore (get/set). The default implementation persists both
// to individual files on disk — that doesn't survive container restarts and
// doesn't scale across multiple org sessions in one process, so this adapter
// persists the same data into the `whatsapp_sessions` row for the org instead,
// mirroring the shape (not the storage mechanism) of useMultiFileAuthState.
const { initAuthCreds, BufferJSON, proto, makeCacheableSignalKeyStore } = require("@whiskeysockets/baileys");
const { supabase } = require("./supabaseClient");

function serialize(value) {
  return JSON.parse(JSON.stringify(value, BufferJSON.replacer));
}

function deserialize(value) {
  return JSON.parse(JSON.stringify(value), BufferJSON.reviver);
}

async function loadSessionRow(orgId) {
  const { data, error } = await supabase
    .from("whatsapp_sessions")
    .select("creds_json, keys_json")
    .eq("org_id", orgId)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function usePostgresAuthState(orgId, logger) {
  const existing = await loadSessionRow(orgId);

  const creds =
    existing?.creds_json && Object.keys(existing.creds_json).length
      ? deserialize(existing.creds_json)
      : initAuthCreds();

  const keyData =
    existing?.keys_json && Object.keys(existing.keys_json).length
      ? deserialize(existing.keys_json)
      : {};

  async function persist() {
    const { error } = await supabase.from("whatsapp_sessions").upsert(
      {
        org_id: orgId,
        creds_json: serialize(creds),
        keys_json: serialize(keyData),
        updated_at: new Date().toISOString(),
      },
      { onConflict: "org_id" }
    );
    if (error) throw error;
  }

  const rawKeyStore = {
    get: async (type, ids) => {
      const result = {};
      for (const id of ids) {
        let value = keyData[type]?.[id];
        if (value !== undefined) {
          if (type === "app-state-sync-key") {
            value = proto.Message.AppStateSyncKeyData.fromObject(value);
          }
          result[id] = value;
        }
      }
      return result;
    },
    set: async (data) => {
      for (const type of Object.keys(data)) {
        keyData[type] = keyData[type] || {};
        for (const id of Object.keys(data[type])) {
          const value = data[type][id];
          if (value === null || value === undefined) {
            delete keyData[type][id];
          } else {
            keyData[type][id] = value;
          }
        }
      }
      await persist();
    },
  };

  return {
    state: {
      creds,
      // makeCacheableSignalKeyStore wraps our Postgres-backed store with an
      // in-memory cache so we're not round-tripping to Postgres on every
      // signal-key lookup within a session — same helper Baileys' own
      // multi-file adapter uses.
      keys: makeCacheableSignalKeyStore(rawKeyStore, logger),
    },
    // Baileys calls this on every `creds.update` event — persist immediately
    // so a mid-pairing crash/restart doesn't lose the session.
    saveCreds: persist,
  };
}

module.exports = { usePostgresAuthState };
