# Knowledge Retrieval (Packs & FAQ Matching)

**Source files:** `knowledge_pack.py`, semantic matching in `knowledge_system/`

## Knowledge Packs

Each client has packs in `data/`:
- `company_knowledge/tinglish.json` — FAQ + hot facts
- `tinglish_inventory.json` — properties/items
- `tinglish_market_competitor.json` — market + objection scripts

## FAQ Matching

1. **Ingest:** questions → tokenize, normalize
2. **Lookup:** caller's transcript → tokenize
3. **Score:** token overlap (Jaccard), threshold 0.45 (confident at 0.70)
4. **Return:** best-match answer

Token sets are **precomputed at load** (REDIS plan §3.5) — no per-turn re-tokenization.

## Objection Stories

**Contract:** Every number must exist in source corpus.

Example:
- KB says: "Kokapet 5-year appreciation 42%"
- Story: "Kokapet buyers in 2020 at ₹7.5k/sqft now ₹10.5k/sqft — that's 40% gain"
- ✅ Grounded (42% ≈ 40%, both from KB)

## Related
- [Knowledge Base Editing (product)](../product/05-knowledge-base-editing)
- [KB Compiler](./09-kb-compiler)
