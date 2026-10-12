# Platform Compiler

> `/platform` dashboard tab — drop a KB, get a live agent

## The Flow

1. **Create a client** → enter ID, industry, language, persona
2. **Drop KB files** → brochures, FAQs, price sheets, URLs
3. **⚙ Compile** → Gemini 3.1 Flash generates the packs
4. **Review** → check FAQs, properties, market facts for hallucination
5. **Approve** → install into the live engine

## Access It

Dashboard → **Platform**

Or directly: `http://localhost:8002/platform`

## Step by Step

### 1. Create Client

Fill in:
- **Client ID:** `apex-insurance` (lowercase, hyphen ok)
- **Industry:** real_estate / finance / insurance / healthcare / ecommerce
- **Language:** tinglish / hte / english
- **Persona:** name, role, company

Click **Create cartridge**.

### 2. Upload Knowledge Base

Any format: `.txt` `.md` `.pdf` `.docx` `.csv` `.xlsx` `.json` `.html` or HTTP URLs.

- Drag-drop files, or type URLs in `client.yaml`
- Platform ingests, chunks, and extracts facts

### 3. Compile

Click **⚙ Compile (Gemini 3.1 Flash)**.

Watch the progress bar:
- ingest (10%)
- extract (30%)
- synthesize (65%)
- validate (75%)
- critic (90%)
- bake (100%)

Takes 1–2 min for a typical KB.

### 4. Review

Platform shows:
- **Validation:** syntax check, grounding (every number must be in the KB)
- **Critic:** hallucination detection, tone check
- **Artifacts:** the generated FAQs, properties, market facts, persona

Tabs let you preview each section.

### 5. Approve & Apply

Click **Approve & Make LIVE**.

This:
- Installs packs into `data/`
- Backs up old packs to `data/backups/<timestamp>/`
- Hot-reloads the config
- Agent is live with the new KB

## Failure Modes

### Validation Blocked: "UNGROUNDED NUMBER"

A price or statistic in the generated content wasn't in your KB. Fix:
- Add the fact to the KB, or
- Recompile (don't include that fact)

### Critic Blocking: "Hallucination Detected"

Gemini invented a claim. Fix:
- Drop new KB sources that clarify, or
- Recompile

## Related
- [Quickstart](../01-quickstart)
- [KB Compiler (dev)](../../dev/09-kb-compiler)
