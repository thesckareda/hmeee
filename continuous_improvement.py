"""
Continuous improvement loop — golden regression, failed-call clustering, A/B prompts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from knowledge_pack import CONFIDENCE_ALLOW, get_store

logger = logging.getLogger("inbound-voice-agent")

_GOLDEN_DIR = Path(__file__).resolve().parent / "tests" / "golden"

AB_PROMPT_OVERLAYS: dict[str, str] = {
    "A": "",
    "B": (
        "\n\n[STYLE VARIANT B — Concise & Direct]\n"
        "Keep replies under 25 words unless explaining pricing or EMI. "
        "Lead with the answer, then one follow-up question. "
        "Avoid filler phrases like 'I understand' or 'That's a great question'.\n"
    ),
}

_FAILURE_CLUSTERS: dict[str, list[str]] = {
    "hallucination": [
        "i think",
        "probably",
        "not sure",
        "let me check",
        "as an ai",
        "language model",
        "i don't have that information",
        "i cannot confirm",
        "might be",
        "could be around",
    ],
    "awkwardness": [
        "sorry about that",
        "i apologize",
        "my apologies",
        "um ",
        "uh ",
        "hmm let me",
        "that's interesting",
        "great question",
        "i understand your concern",
    ],
    "pricing_risk": [
        "crore",
        "lakh",
        "per sqft",
        "price is",
        "costs around",
        "approximately",
        "starting from",
    ],
    "identity_leak": [
        "i am an ai",
        "i'm an ai",
        "artificial intelligence",
        "virtual assistant",
        "chatbot",
        "language model",
    ],
}


def get_ab_prompt_variant(campaign_id: str | None, phone: str) -> str:
    """Deterministic A/B prompt bucket — stable per campaign + phone pair."""
    seed = f"{campaign_id or 'default'}:{phone or 'unknown'}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 2
    return "B" if bucket else "A"


def apply_ab_prompt_variant(
    base_instructions: str,
    variant: str,
    *,
    campaign_id: str | None = None,
) -> str:
    """Append campaign A/B overlay to base agent instructions."""
    _ = campaign_id  # reserved for per-campaign overlay tables
    overlay = AB_PROMPT_OVERLAYS.get((variant or "A").upper(), "")
    base = (base_instructions or "").strip()
    if not overlay:
        return base
    return f"{base}{overlay}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def cluster_failed_calls(transcripts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Simple keyword clustering for failed call transcripts.

    Each transcript dict should include at least ``text`` (full transcript or summary).
    Optional keys: ``phone``, ``call_id``, ``failed`` (bool).
    """
    clusters: dict[str, list[dict[str, Any]]] = {key: [] for key in _FAILURE_CLUSTERS}
    clusters["unclassified"] = []

    for entry in transcripts or []:
        if entry.get("failed") is False:
            continue
        blob = _normalize_text(
            str(entry.get("text") or entry.get("transcript") or entry.get("summary") or "")
        )
        if not blob:
            clusters["unclassified"].append(entry)
            continue

        matched = False
        for cluster_name, keywords in _FAILURE_CLUSTERS.items():
            if any(kw in blob for kw in keywords):
                clusters[cluster_name].append(entry)
                matched = True
        if not matched:
            clusters["unclassified"].append(entry)

    return {k: v for k, v in clusters.items() if v}


def _run_faq_suite(data: dict[str, Any], *, suite_name: str) -> dict[str, Any]:
    from identity_shield import contains_ai_disclosure, handle_identity_trap_turn

    store = get_store()
    failures: list[str] = []
    passed = 0
    total = 0

    for block in data.get("preset_tests") or []:
        preset = block.get("preset", "tinglish")
        for case in block.get("cases") or []:
            total += 1
            q = case.get("question", "")
            if case.get("expect_deflection"):
                reply = handle_identity_trap_turn(q, preset=preset)
                if not reply:
                    failures.append(f"{preset}: no deflection for {q!r}")
                    continue
                if contains_ai_disclosure(reply):
                    failures.append(f"{preset}: AI disclosure in deflection for {q!r}")
                    continue
                for bad in case.get("must_not_contain") or []:
                    if bad.lower() in reply.lower():
                        failures.append(f"{preset}: forbidden {bad!r} in deflection for {q!r}")
                        break
                else:
                    passed += 1
                continue

            if case.get("faq_id"):
                expected_id = case["faq_id"]
                min_conf = float(case.get("min_confidence", CONFIDENCE_ALLOW))
                hit = store.lookup_faq(q, preset)
                if hit is None:
                    failures.append(f"{expected_id}: no hit for {q!r}")
                    continue
                if hit.faq_id != expected_id:
                    failures.append(f"{expected_id}: got {hit.faq_id} for {q!r}")
                    continue
                if hit.confidence < min_conf:
                    failures.append(
                        f"{expected_id}: confidence {hit.confidence:.2f} < {min_conf} for {q!r}"
                    )
                    continue
                passed += 1
                continue

            hit = store.lookup_faq(q, preset)
            if hit is None:
                failures.append(f"{preset}: no FAQ hit for {q!r}")
                continue
            answer = hit.answer.lower()
            for token in case.get("must_contain") or []:
                if token.lower() not in answer:
                    failures.append(f"{preset}: {token!r} missing in answer for {q!r}")
                    break
            else:
                for bad in case.get("must_not_contain") or []:
                    if bad.lower() in answer:
                        failures.append(f"{preset}: forbidden {bad!r} in answer for {q!r}")
                        break
                else:
                    passed += 1

    return {
        "suite": suite_name,
        "passed": passed,
        "failed": len(failures),
        "total": total,
        "failures": failures,
        "ok": not failures,
    }


def _run_sales_flow_suite(data: dict[str, Any], *, suite_name: str) -> dict[str, Any]:
    from call_planner import AgendaPhase, CallPlanner

    failures: list[str] = []
    passed = 0
    total = 0

    for flow in data.get("flows") or []:
        name = flow.get("name", "flow")
        planner = CallPlanner()
        expected = flow.get("expected_phases")
        if expected:
            for utterance, want in zip(flow.get("utterances") or [], expected):
                total += 1
                phase = planner.process_utterance(utterance)
                if phase.value != want:
                    failures.append(
                        f"{name}: after {utterance!r} expected {want}, got {phase.value}"
                    )
                else:
                    passed += 1
            continue

        if flow.get("expected_phase_after_stage"):
            total += 1
            planner.advance_for_deal_stage(flow.get("deal_stage", ""))
            for utterance in flow.get("utterances") or []:
                planner.process_utterance(utterance)
            want = flow["expected_phase_after_stage"]
            if planner.phase.value != want:
                failures.append(
                    f"{name}: expected phase {want}, got {planner.phase.value}"
                )
            else:
                passed += 1

    return {
        "suite": suite_name,
        "passed": passed,
        "failed": len(failures),
        "total": total,
        "failures": failures,
        "ok": not failures,
    }


def run_golden_regression(golden_dir: Path | str | None = None) -> dict[str, Any]:
    """Run all ``tests/golden/*.json`` suites and return an aggregate report."""
    root = Path(golden_dir) if golden_dir else _GOLDEN_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"Golden directory not found: {root}")

    suite_results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            suite_results.append({
                "suite": path.name,
                "passed": 0,
                "failed": 1,
                "total": 0,
                "failures": [str(exc)],
                "ok": False,
            })
            continue
        if data.get("flows"):
            suite_results.append(_run_sales_flow_suite(data, suite_name=path.name))
        else:
            suite_results.append(_run_faq_suite(data, suite_name=path.name))

    total_passed = sum(r["passed"] for r in suite_results)
    total_failed = sum(r["failed"] for r in suite_results)
    total_cases = sum(r["total"] for r in suite_results)
    all_ok = all(r["ok"] for r in suite_results)

    report = {
        "ok": all_ok,
        "suites_run": len(suite_results),
        "cases_passed": total_passed,
        "cases_failed": total_failed,
        "cases_total": total_cases,
        "suites": suite_results,
    }
    logger.info(
        "[GOLDEN] %d suites, %d/%d passed, ok=%s",
        report["suites_run"],
        total_passed,
        total_cases,
        all_ok,
    )
    return report