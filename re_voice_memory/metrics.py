"""
Observability — Prometheus metrics for memory health and business signals.
All metric functions are no-ops if prometheus_client is not installed.
"""
import logging
import os

logger = logging.getLogger(__name__)

_HAS_PROMETHEUS = False
try:
    import prometheus_client as prom
    _HAS_PROMETHEUS = True
except ImportError:
    pass


# ── Metric definitions (no-op stubs if prometheus unavailable) ────────────

if _HAS_PROMETHEUS:
    INJECTION_LATENCY_MS = prom.Histogram(
        "re_voice_injection_latency_ms",
        "Pre-call context injection latency",
        labelnames=["lead_type", "blocks_injected"],
        buckets=[5, 10, 20, 30, 50, 75, 100, 200, 500],
    )
    MEMORY_HIT_RATE = prom.Counter(
        "re_voice_memory_hit_rate",
        "Memory tier hit/miss counts",
        labelnames=["tier", "outcome"],
    )
    INTENT_SCORE = prom.Histogram(
        "re_voice_intent_score",
        "Post-call intent score distribution",
        labelnames=["call_count_bucket"],
        buckets=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    )
    STAGE_TRANSITIONS = prom.Counter(
        "re_voice_deal_stage_transitions",
        "Deal stage changes per call",
        labelnames=["from_stage", "to_stage"],
    )
    OBJECTION_TYPES = prom.Counter(
        "re_voice_objection_types",
        "Objection type frequency",
        labelnames=["type"],
    )
    CRM_SYNC_SUCCESS = prom.Counter(
        "re_voice_crm_sync_success",
        "CRM sync outcomes",
        labelnames=["crm_type", "outcome"],
    )
    OFFER_INTENTS = prom.Counter(
        "re_voice_offer_intents_captured",
        "Offer intents captured during calls",
    )
    VIEWINGS_SCHEDULED = prom.Counter(
        "re_voice_viewings_scheduled",
        "Property viewings scheduled",
    )
    STT_PARTIAL_LATENCY_MS = prom.Histogram(
        "hermes_stt_partial_latency_ms",
        "Time from audio chunk send to first partial transcript",
        buckets=[20, 40, 60, 80, 100, 150, 200, 300, 500],
    )
    TTS_FIRST_CHUNK_MS = prom.Histogram(
        "hermes_tts_first_chunk_ms",
        "Time from text input to first audio chunk out",
        buckets=[10, 20, 40, 60, 100, 150, 250, 400, 600],
    )
    LLM_TTFT_MS = prom.Histogram(
        "hermes_llm_ttft_ms",
        "LLM time to first token",
        buckets=[50, 100, 150, 180, 250, 350, 500, 800],
    )
    E2E_TURN_LATENCY_MS = prom.Histogram(
        "hermes_e2e_turn_latency_ms",
        "Full turn latency: caller stops speaking to agent starts speaking",
        buckets=[100, 200, 310, 350, 450, 600, 800, 1200],
    )

    def _noop(*args, **kwargs):
        pass
else:
    INJECTION_LATENCY_MS = None
    MEMORY_HIT_RATE = None
    INTENT_SCORE = None
    STAGE_TRANSITIONS = None
    OBJECTION_TYPES = None
    CRM_SYNC_SUCCESS = None
    OFFER_INTENTS = None
    VIEWINGS_SCHEDULED = None
    STT_PARTIAL_LATENCY_MS = None
    TTS_FIRST_CHUNK_MS = None
    LLM_TTFT_MS = None
    E2E_TURN_LATENCY_MS = None


# ── Metric emission helpers ───────────────────────────────────────────────

def record_injection_latency(lead_type: str, blocks_injected: int, latency_ms: float):
    if INJECTION_LATENCY_MS:
        INJECTION_LATENCY_MS.labels(lead_type=lead_type, blocks_injected=str(blocks_injected)).observe(latency_ms)

def record_memory_hit(tier: str, hit: bool):
    if MEMORY_HIT_RATE:
        MEMORY_HIT_RATE.labels(tier=tier, outcome="hit" if hit else "miss").inc()

_KV_FALLBACK_COUNT = 0

def record_kv_fallback():
    """Redis circuit opened — production is running on the SQLite fallback."""
    global _KV_FALLBACK_COUNT
    _KV_FALLBACK_COUNT += 1
    if MEMORY_HIT_RATE:
        MEMORY_HIT_RATE.labels(tier="redis_fallback", outcome="miss").inc()

def get_kv_fallback_count() -> int:
    return _KV_FALLBACK_COUNT

def record_intent_score(call_count: int, score: float):
    if INTENT_SCORE:
        bucket = "1" if call_count == 1 else ("2-3" if call_count <= 3 else "4+")
        INTENT_SCORE.labels(call_count_bucket=bucket).observe(score)

def record_stage_transition(from_stage: str, to_stage: str):
    if STAGE_TRANSITIONS:
        STAGE_TRANSITIONS.labels(from_stage=from_stage, to_stage=to_stage).inc()

def record_objection(obj_type: str):
    if OBJECTION_TYPES:
        OBJECTION_TYPES.labels(type=obj_type).inc()

def record_crm_sync(crm_type: str, success: bool):
    if CRM_SYNC_SUCCESS:
        CRM_SYNC_SUCCESS.labels(crm_type=crm_type, outcome="success" if success else "failure").inc()

def record_offer_intent():
    if OFFER_INTENTS:
        OFFER_INTENTS.inc()

def record_viewing():
    if VIEWINGS_SCHEDULED:
        VIEWINGS_SCHEDULED.inc()


def record_stt_partial_latency(latency_ms: float):
    if STT_PARTIAL_LATENCY_MS:
        STT_PARTIAL_LATENCY_MS.observe(latency_ms)


def record_tts_first_chunk(latency_ms: float):
    if TTS_FIRST_CHUNK_MS:
        TTS_FIRST_CHUNK_MS.observe(latency_ms)


def record_llm_ttft(latency_ms: float):
    if LLM_TTFT_MS:
        LLM_TTFT_MS.observe(latency_ms)


def record_e2e_turn_latency(latency_ms: float):
    if E2E_TURN_LATENCY_MS:
        E2E_TURN_LATENCY_MS.observe(latency_ms)


# ── Optional HTTP metrics endpoint ────────────────────────────────────────

def maybe_start_metrics_server():
    """Start Prometheus HTTP server if METRICS_PORT env var is set."""
    port = os.environ.get("METRICS_PORT", "")
    if port and _HAS_PROMETHEUS:
        try:
            prom.start_http_server(int(port))
            logger.info("[METRICS] HTTP server started on port %s", port)
        except Exception as e:
            logger.warning("[METRICS] Failed to start server: %s", e)
