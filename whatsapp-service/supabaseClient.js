const { createClient } = require("@supabase/supabase-js");

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
const SUPABASE_ANON_KEY = process.env.SUPABASE_KEY;

if (!SUPABASE_URL || (!SUPABASE_SERVICE_ROLE_KEY && !SUPABASE_ANON_KEY)) {
  throw new Error(
    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set — whatsapp-service " +
      "needs the service_role key because whatsapp_sessions is RLS-locked."
  );
}

if (!SUPABASE_SERVICE_ROLE_KEY) {
  console.warn(
    "[whatsapp-service] SUPABASE_SERVICE_ROLE_KEY is not set — falling back to SUPABASE_KEY. " +
      "Writes to whatsapp_sessions will fail unless you add the service_role key to .env."
  );
}

const supabase = createClient(
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY || SUPABASE_ANON_KEY,
  { auth: { persistSession: false } }
);

module.exports = { supabase };
