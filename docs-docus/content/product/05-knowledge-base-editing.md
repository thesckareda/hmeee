# Knowledge Base Editing

> Add properties, FAQs, market facts, objection stories

## Format: Real Estate (Properties)

Drop properties into `data/tinglish_inventory.json`:

\`\`\`json
[
  {
    "listing_id": "aparna_zenon_kokapet",
    "name": "Aparna Zenon",
    "builder": "Aparna Constructions",
    "location": "Kokapet, Hyderabad",
    "micro_market": "Kokapet / Neopolis",
    "configurations": ["3 BHK", "4 BHK"],
    "sizes_sqft": { "3_bhk": "1,950 - 2,250", "4_bhk": "2,900 - 3,300" },
    "price_range": "₹2.10 Cr - ₹3.95 Cr",
    "price_per_sqft": "₹10,500 - ₹12,800",
    "possession": "June 2027",
    "rera_id": "P02400001234",
    "amenities": ["Clubhouse 38,000 sq ft", "Pool", "Courts"],
    "usp": "Only Kokapet project with ORR ramp — 8 min to Financial District in peak traffic. Corner plot, 70% units east-facing.",
    "units_left": 14,
    "offer_expiry": "31 July 2026"
  }
]
\`\`\`

### The USP Field (Humanization Rule)

**The one thing the agent says about the project.** Write it like a senior agent on the phone:
- ✅ *"Only Kokapet project with direct ORR ramp — 8 minutes to Financial District even in peak traffic."*
- ✅ *"Corner land parcel, so 70% units are east-facing."*
- ❌ *"Luxurious world-class living experience."* (adjectives banned)
- ❌ *"Best value in the market."* (subjective, not grounded)

**Every detail must be verifiable.** Numbers, proper nouns, measurable facts only.

### The Honesty Contract

**`units_left` and `offer_expiry`:** ONLY if real.

- ✅ `"units_left": 14` — actual available units, updated regularly
- ✅ `"offer_expiry": "31 July 2026"` — real deadline
- ❌ `"units_left": 100` — made-up to create fake urgency (banned)
- ❌ null (omit if not known)

## Format: FAQs

\`\`\`json
{
  "faq": [
    {
      "id": "brokerage_fee",
      "category": "company",
      "questions": [
        "do you charge brokerage",
        "what is your commission",
        "fees kitna hai",
        "brokerage entha",
        "charges emaina",
        "koi commission dena padega",
        "aapki fees kya hai",
        "service charge unda"
      ],
      "answer": "Buyers nunchi ZERO brokerage teeskuntam — builder nunchi matrame commission vastundi. Meeku transparency."
    }
  ],
  "hot_facts": [
    "Founded 2015 by Vikram Rao",
    "Zero brokerage for buyers",
    "RERA A0240001111",
    "45 person team",
    "Office at Gachibowli"
  ]
}
\`\`\`

**Paraphrase rule:** 8–15 question variants per FAQ. Mix English, code-switch, and short lazy versions ("fees?", "commission?"). One well-paraphrased entry beats ten sparse ones.

## Format: Market & Objections

\`\`\`json
{
  "micro_markets": [
    {
      "name": "Kokapet",
      "price_per_sqft": "₹9,500 - ₹12,000",
      "appreciation_5yr": "42%",
      "usp": "Direct ORR access, 15 min to airport",
      "key_projects": ["Aparna Zenon", "Godrej Centercourt"]
    }
  ],
  "objection_scripts": {
    "price_too_high": "Rates lo ye range e market standard andi — ORR ramp exclusive idi.",
    "need_to_think": "Ardham ayindi andi. Budget lo chuddam, call cheyandi.",
    "other_builder_cheaper": "Builder reputation + location = value difference.",
    "location_far": "ORR means 8 minutes to Financial District. Where else e aata?",
    "will_wait_for_rates_to_fall": "5-year appreciation 42% — delay costs more than EMI difference.",
    "not_ready_to_move": "Site check matramata. Possession 2027, plenty time."
  },
  "objection_stories": {
    "price_too_high": "Kokapet buyers in 2020 at ₹7.5k/sqft now at ₹10.5k/sqft. That's 40% gain in 5 years.",
    "location_far": "Direct ORR ramp cuts 30 min off the old route. Clients moved because of that access.",
    ...
  }
}
\`\`\`

## Related
- [Platform Compiler](../06-platform-compiler) — automate this with Gemini
- [Knowledge Retrieval (dev)](../../dev/06-knowledge-retrieval)
