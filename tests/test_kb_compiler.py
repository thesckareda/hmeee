"""KB compiler end-to-end — fake Gemini, real pipeline, real validators."""
from __future__ import annotations

import asyncio
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

KB_TEXT = """Apex Homes is a Hyderabad real-estate company founded in 2015 by Vikram Rao.
RERA registration A0240001111. Office at Gachibowli. Team of 45 consultants.
We charge zero brokerage to buyers; the builder pays our commission.

Project: Apex Meadows, Tellapur. 2 BHK and 3 BHK apartments.
Price range ₹85 lakh to ₹1.4 Cr. Possession March 2027. RERA P0240009999.
Only 12 units left in the 3 BHK configuration. Clubhouse 20,000 sq ft.
Tellapur prices appreciated 42% over the last 5 years.
"""

FAKE_ARTIFACTS = {
    "facts": {"facts": [
        {"claim": "Apex Homes founded in 2015 by Vikram Rao", "value": "2015", "source_ref": "facts.txt#0"},
        {"claim": "Zero brokerage for buyers", "value": "", "source_ref": "facts.txt#0"},
        {"claim": "Apex Meadows priced ₹85 lakh to ₹1.4 Cr", "value": "₹85 lakh - ₹1.4 Cr", "source_ref": "facts.txt#0"},
        {"claim": "12 units left in 3 BHK", "value": "12", "source_ref": "facts.txt#0"},
        {"claim": "Tellapur 5-year appreciation 42%", "value": "42%", "source_ref": "facts.txt#0"},
    ]},
    "company": {
        "faq": [{
            "id": "brokerage_fee", "category": "company",
            "questions": ["do you charge brokerage", "commission kitna", "fees entha",
                          "brokerage undha", "what are your charges"],
            "answer": "Buyers ki zero brokerage andi — builder nunchi matrame commission vastundi.",
        }, {
            "id": "who_founded", "category": "company",
            "questions": ["who founded apex", "founder evaru", "company owner kaun",
                          "apex homes kab bana", "who started the company"],
            "answer": "Apex Homes ni 2015 lo Vikram Rao garu start chesaru andi.",
        }],
        "hot_facts": ["Founded 2015 by Vikram Rao", "Zero brokerage for buyers",
                      "RERA A0240001111"],
    },
    "inventory": {"properties": [{
        "listing_id": "apex_meadows_tellapur", "name": "Apex Meadows",
        "builder": "Apex Homes", "location": "Tellapur, Hyderabad",
        "micro_market": "Tellapur", "configurations": ["2 BHK", "3 BHK"],
        "price_range": "₹85 lakh - ₹1.4 Cr", "possession": "March 2027",
        "rera_id": "P0240009999", "amenities": ["Clubhouse 20,000 sq ft"],
        "usp": "Tellapur appreciated 42% in 5 years — entry pricing from ₹85 lakh.",
        "units_left": 12, "offer_expiry": None,
    }]},
    "market": {
        "micro_markets": [{"name": "Tellapur", "price_per_sqft": "", "usp": "42% 5-year appreciation",
                           "appreciation_5yr": "42%", "key_projects": ["Apex Meadows"]}],
        "competitors": [],
        "objection_scripts": {k: "Ardham ayindi andi — value chuddam." for k in (
            "price_too_high", "need_to_think", "other_builder_cheaper",
            "location_far", "will_wait_for_rates_to_fall", "not_ready_to_move")},
        "objection_stories": {k: "Tellapur lo 5 years lo 42% appreciation vachindi andi." for k in (
            "price_too_high", "need_to_think", "other_builder_cheaper",
            "location_far", "will_wait_for_rates_to_fall", "not_ready_to_move")},
    },
    "persona": {
        "agent_instructions": "You are Meera, Senior Consultant at Apex Homes. Speak Telugish. "
                              "Keep replies to 1-2 sentences, always end with a question.",
        "first_line": "Hello andi! Nenu Meera, Apex Homes nunchi matladutunnanu.",
        "stt_hints": ["Apex Meadows", "Tellapur", "Gachibowli"],
    },
    "critic": {"blocking": [], "style": []},
}


async def _fake_generate_json(prompt: str, **kwargs):
    if "atomic, verifiable business fact" in prompt:
        return FAKE_ARTIFACTS["facts"]
    if "FAQ pack" in prompt:
        return FAKE_ARTIFACTS["company"]
    if "ITEMS container" in prompt:
        return FAKE_ARTIFACTS["inventory"]
    if "MARKET + OBJECTION" in prompt:
        return FAKE_ARTIFACTS["market"]
    if "voice-agent persona" in prompt:
        return FAKE_ARTIFACTS["persona"]
    if "quality critic" in prompt:
        return FAKE_ARTIFACTS["critic"]
    raise AssertionError("Unrecognized compiler prompt")


def _make_client(tmp_path, client_id="testco"):
    import yaml

    cdir = tmp_path / "clients" / client_id
    (cdir / "kb").mkdir(parents=True)
    (cdir / "kb" / "facts.txt").write_text(KB_TEXT, encoding="utf-8")
    (cdir / "client.yaml").write_text(yaml.safe_dump({
        "client_id": client_id, "industry": "real_estate", "lang_preset": "tinglish",
        "persona": {"name": "Meera", "role": "Senior Consultant", "company": "Apex Homes"},
    }), encoding="utf-8")
    return cdir


def test_pipeline_end_to_end(monkeypatch, tmp_path):
    import kb_compiler.critic as critic_mod
    import kb_compiler.synthesize as synth_mod
    from kb_compiler.pipeline import read_compile_state, run_compile

    monkeypatch.setattr(synth_mod, "generate_json", _fake_generate_json)
    monkeypatch.setattr(critic_mod, "generate_json", _fake_generate_json)

    cdir = _make_client(tmp_path)
    result = asyncio.run(run_compile("testco", tmp_path))

    assert result["success"], result
    assert result["validation"]["ok"], result["validation"]["errors"]
    state = read_compile_state(cdir)
    assert state["stage"] == "done" and state["pct"] == 100

    lock = json.loads((cdir / "compiled.lock.json").read_text(encoding="utf-8"))
    assert lock["model"]  # synthesis model (gemini-3.1-pro or env override)
    assert lock["approved"] is False  # human gate not passed yet
    assert lock["source_hashes"]  # KB hashed for incremental recompile

    # Stage-model contract (master plan §4.2): Pro synthesis, Flash extract,
    # separate critic — all recorded in the lockfile.
    from kb_compiler.llm import CRITIC_MODEL, EXTRACT_MODEL, SYNTH_MODEL

    assert lock["stage_models"] == {
        "extract": EXTRACT_MODEL, "synthesize": SYNTH_MODEL, "critic": CRITIC_MODEL}
    assert lock["model"] == SYNTH_MODEL

    # Grounding trail persisted for the review screen
    facts = json.loads((cdir / "facts.json").read_text(encoding="utf-8"))
    assert facts and all(f.get("source_ref") for f in facts)

    packs = cdir / "packs"
    company = json.loads((packs / "company_knowledge.json").read_text(encoding="utf-8"))
    assert len(company["faq"]) == 2
    inventory = json.loads((packs / "inventory.json").read_text(encoding="utf-8"))
    assert inventory["properties"][0]["units_left"] == 12


def test_grounding_blocks_invented_numbers(monkeypatch, tmp_path):
    """A hallucinated '73%' (not in the KB) must FAIL the build."""
    import copy

    import kb_compiler.critic as critic_mod
    import kb_compiler.synthesize as synth_mod
    from kb_compiler.pipeline import run_compile

    poisoned = copy.deepcopy(FAKE_ARTIFACTS)
    poisoned["market"]["objection_stories"]["price_too_high"] = (
        "Tellapur lo 73% appreciation vachindi andi."  # INVENTED — not in KB
    )

    async def fake(prompt: str, **kwargs):
        if "atomic, verifiable business fact" in prompt:
            return poisoned["facts"]
        if "FAQ pack" in prompt:
            return poisoned["company"]
        if "ITEMS container" in prompt:
            return poisoned["inventory"]
        if "MARKET + OBJECTION" in prompt:
            return poisoned["market"]
        if "voice-agent persona" in prompt:
            return poisoned["persona"]
        return poisoned["critic"]

    monkeypatch.setattr(synth_mod, "generate_json", fake)
    monkeypatch.setattr(critic_mod, "generate_json", fake)

    _make_client(tmp_path)
    result = asyncio.run(run_compile("testco", tmp_path))
    assert result["success"]  # pipeline completes…
    assert not result["validation"]["ok"]  # …but the build is BLOCKED
    assert any("UNGROUNDED" in e and "73" in e for e in result["validation"]["errors"])


def test_approve_refuses_blocked_build(monkeypatch, tmp_path):
    from kb_compiler.bake import approve_and_apply, bake_cartridge

    cdir = _make_client(tmp_path)
    bake_cartridge(cdir, {"lang_preset": "tinglish"}, {},
                   {"ok": False, "errors": ["UNGROUNDED NUMBER"]}, {"blocking": []})
    result = approve_and_apply(cdir, tmp_path)
    assert not result["success"]
    assert "Validation failed" in result["error"]


def test_approve_installs_and_backs_up(monkeypatch, tmp_path):
    import kb_compiler.critic as critic_mod
    import kb_compiler.synthesize as synth_mod
    from kb_compiler.pipeline import run_compile
    from kb_compiler.bake import approve_and_apply, load_lock

    monkeypatch.setattr(synth_mod, "generate_json", _fake_generate_json)
    monkeypatch.setattr(critic_mod, "generate_json", _fake_generate_json)

    # Isolate config writes from the real repo
    import config_store
    written: dict = {}
    monkeypatch.setattr(config_store, "write_full_config",
                        lambda updates: written.update(updates) or written)
    import assistant_settings
    monkeypatch.setattr(assistant_settings, "touch_reload_signal", lambda: None)

    cdir = _make_client(tmp_path)
    # Pre-existing pack to prove backup happens
    old_pack = tmp_path / "data" / "company_knowledge" / "tinglish.json"
    old_pack.parent.mkdir(parents=True)
    old_pack.write_text('{"old": true}', encoding="utf-8")

    asyncio.run(run_compile("testco", tmp_path))
    result = approve_and_apply(cdir, tmp_path, approved_by="tester")

    assert result["success"], result
    installed = json.loads(old_pack.read_text(encoding="utf-8"))
    assert "faq" in installed  # new pack in place
    assert result["backup_dir"] and any(
        (tmp_path / "data" / "backups").rglob("tinglish.json"))  # old one saved
    assert written.get("first_line", "").startswith("Hello andi! Nenu Meera")
    assert written.get("agent_instructions")
    lock = load_lock(cdir)
    assert lock["approved"] and lock["approved_by"] == "tester"


def test_json_salvage():
    """Fenced, prose-wrapped, or trailing-noise JSON must still parse."""
    from kb_compiler.llm import CompilerLLMError, parse_json_response

    assert parse_json_response('{"a": 1}') == {"a": 1}
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_response('Here is the JSON you asked for:\n{"a": [1, 2]}\nHope it helps!') == {"a": [1, 2]}
    assert parse_json_response('noise [1, 2, 3] trailing') == [1, 2, 3]
    try:
        parse_json_response("no json here at all")
        raise AssertionError("should have raised")
    except CompilerLLMError:
        pass


def test_interrupted_compile_reconciled(tmp_path):
    """running=true with no live task (server died) must become a clear error."""
    from kb_compiler.pipeline import _write_state, read_compile_state, reconcile_state

    cdir = tmp_path / "clients" / "testco"
    cdir.mkdir(parents=True)
    _write_state(cdir, running=True, stage="synthesize", pct=45, message="building")

    # Task alive → untouched
    state = reconcile_state(cdir, task_alive=True)
    assert state["running"] and state["stage"] == "synthesize"

    # Task gone → orphan converted to recoverable error
    state = reconcile_state(cdir, task_alive=False)
    assert not state["running"] and state["stage"] == "error"
    assert "interrupted" in state["error"].lower()
    assert read_compile_state(cdir)["stage"] == "error"  # persisted


def test_synthesize_shape_retry(monkeypatch):
    """A builder returning the wrong shape gets exactly one retry."""
    import kb_compiler.synthesize as synth_mod

    calls = {"n": 0}

    async def flaky(prompt: str, **kwargs):
        if "FAQ pack" in prompt:
            calls["n"] += 1
            if calls["n"] == 1:
                return {"wrong_key": True}  # malformed first attempt
            return FAKE_ARTIFACTS["company"]
        if "ITEMS container" in prompt:
            return FAKE_ARTIFACTS["inventory"]
        if "MARKET + OBJECTION" in prompt:
            return FAKE_ARTIFACTS["market"]
        if "voice-agent persona" in prompt:
            return FAKE_ARTIFACTS["persona"]
        raise AssertionError("unexpected prompt")

    monkeypatch.setattr(synth_mod, "generate_json", flaky)
    artifacts = asyncio.run(synth_mod.synthesize_all(
        FAKE_ARTIFACTS["facts"]["facts"],
        {"lang_preset": "tinglish", "industry": "real_estate", "persona": {}}))
    assert calls["n"] == 2  # retried once
    assert artifacts["company_knowledge"]["faq"]


def test_atomic_write(tmp_path):
    from kb_compiler.pipeline import write_json_atomic

    path = tmp_path / "x.json"
    write_json_atomic(path, {"k": "v"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": "v"}
    assert not path.with_suffix(".json.tmp").exists()  # no debris


def test_ingest_formats(tmp_path):
    from kb_compiler.ingest import ingest_sources

    (tmp_path / "a.txt").write_text("Plain text fact ₹1 Cr.", encoding="utf-8")
    (tmp_path / "b.csv").write_text("name,price\nMeadows,85 lakh\n", encoding="utf-8")
    (tmp_path / "c.json").write_text('{"k": "v"}', encoding="utf-8")
    (tmp_path / "d.html").write_text("<html><body><p>Web fact</p></body></html>", encoding="utf-8")
    chunks = ingest_sources(["a.txt", "b.csv", "c.json", "d.html"], tmp_path)
    text = " ".join(c["text"] for c in chunks)
    assert "₹1 Cr" in text and "85 lakh" in text and '"k"' in text and "Web fact" in text
    assert all(c["source"] and c["chunk_id"] for c in chunks)
