# Quickstart

> **Time:** 5 minutes

## Start the stack

\`\`\`bash
docker-compose up -d
# or: python3 ui_server.py (with Redis running)
\`\`\`

## Log in

Visit `http://localhost:8002/login` (any email in dev mode).

## Make a test call

Dashboard → **Leads** → **+New Test Call** → pick a language → let it ring.

## Check the transcript

Dashboard → **Leads** → (select the call) → scroll to transcript.

## See caller memory

Dashboard → **Leads** → (select caller) → scroll to "Prior Call History".

Next time this caller dials, they'll hear: *"meeru yesterday call chesaru kada, Kokapet 3BHK gurinchi matladam…"*

## Related
- [Dashboard Guide](../02-dashboard-guide)
- [Platform Compiler](../06-platform-compiler)
