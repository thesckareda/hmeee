"""Pydantic models for the Voice AI Assistant platform (migration v4)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─── Shared JSONB shapes ──────────────────────────────────────────────────────

class FlowSection(BaseModel):
    id: str
    title: str
    content: str = ""
    enabled: bool = True
    order: int = 0


class SilenceHandling(BaseModel):
    enabled: bool = True
    timeout_seconds: int = Field(default=8, ge=1, le=120)
    max_prompts: int = Field(default=2, ge=0, le=10)
    prompt_message: str = "Are you still there?"
    end_call_after_max_prompts: bool = False


class EndCallRules(BaseModel):
    max_duration_seconds: int = Field(default=1800, ge=60, le=7200)
    end_phrases: list[str] = Field(default_factory=lambda: ["goodbye", "bye"])
    voicemail_detection: bool = True
    voicemail_message: str = ""


class TransferRouting(BaseModel):
    enabled: bool = False
    transfer_number: str = ""
    transfer_phrases: list[str] = Field(default_factory=list)
    warm_transfer: bool = False


class ResponseBehavior(BaseModel):
    interruption_sensitivity: str = "medium"
    max_response_sentences: int = Field(default=3, ge=1, le=20)
    filler_phrases_enabled: bool = True
    backchannel_enabled: bool = True


class AmbientSound(BaseModel):
    enabled: bool = True
    sound_type: str = "cicd_1"
    volume: float = Field(default=0.12, ge=0.08, le=0.18)


class ExtractedVariable(BaseModel):
    name: str
    description: str = ""
    how_to_extract: str = ""


class DeliveryMethod(str, Enum):
    WEBHOOK = "webhook"
    EMAIL = "email"
    BOTH = "both"
    NONE = "none"


# ─── agents ───────────────────────────────────────────────────────────────────

class AgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    purpose: str | None = None
    system_prompt: str = ""
    languages: list[str] = Field(default_factory=lambda: ["en"])
    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.5-flash"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    stt_provider: str = "soniox"
    stt_model: str = "stt-rt-v5"
    tts_provider: str = "gemini"
    tts_voice_id: str = "kavya"
    welcome_message: str | None = None
    max_call_duration_seconds: int = Field(default=1800, ge=60, le=7200)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("languages")
    @classmethod
    def languages_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("languages must contain at least one entry")
        return v


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    purpose: str | None = None
    system_prompt: str | None = None
    languages: list[str] | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stt_provider: str | None = None
    stt_model: str | None = None
    tts_provider: str | None = None
    tts_voice_id: str | None = None
    welcome_message: str | None = None
    max_call_duration_seconds: int | None = Field(default=None, ge=60, le=7200)
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None


class Agent(AgentBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


# ─── conversational_flows ─────────────────────────────────────────────────────

class ConversationalFlowBase(BaseModel):
    sections: list[FlowSection] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)


class ConversationalFlowCreate(ConversationalFlowBase):
    agent_id: UUID


class ConversationalFlowUpdate(BaseModel):
    sections: list[FlowSection] | None = None
    version: int | None = Field(default=None, ge=1)


class ConversationalFlow(ConversationalFlowBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    created_at: datetime
    updated_at: datetime


# ─── call_configurations ──────────────────────────────────────────────────────

class CallConfigurationBase(BaseModel):
    silence_handling: SilenceHandling | dict[str, Any] = Field(default_factory=SilenceHandling)
    end_call_rules: EndCallRules | dict[str, Any] = Field(default_factory=EndCallRules)
    transfer_routing: TransferRouting | dict[str, Any] = Field(default_factory=TransferRouting)
    response_behavior: ResponseBehavior | dict[str, Any] = Field(default_factory=ResponseBehavior)
    ambient_sound: AmbientSound | dict[str, Any] = Field(default_factory=AmbientSound)
    advanced_settings: dict[str, Any] = Field(default_factory=dict)


class CallConfigurationCreate(CallConfigurationBase):
    agent_id: UUID


class CallConfigurationUpdate(BaseModel):
    silence_handling: SilenceHandling | dict[str, Any] | None = None
    end_call_rules: EndCallRules | dict[str, Any] | None = None
    transfer_routing: TransferRouting | dict[str, Any] | None = None
    response_behavior: ResponseBehavior | dict[str, Any] | None = None
    ambient_sound: AmbientSound | dict[str, Any] | None = None
    advanced_settings: dict[str, Any] | None = None


class CallConfiguration(CallConfigurationBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    created_at: datetime
    updated_at: datetime


# ─── post_call_actions ────────────────────────────────────────────────────────

class PostCallActionBase(BaseModel):
    delivery_method: DeliveryMethod | str = DeliveryMethod.WEBHOOK
    trigger_statuses: list[str] = Field(default_factory=lambda: ["completed"])
    include_summary: bool = True
    include_full_transcript: bool = False
    include_sentiment: bool = True
    include_extracted_info: bool = True
    extracted_variables: list[ExtractedVariable] = Field(default_factory=list)
    webhook_url: str | None = None
    webhook_headers: dict[str, str] = Field(default_factory=dict)
    email_recipients: list[str] = Field(default_factory=list)


class PostCallActionCreate(PostCallActionBase):
    agent_id: UUID


class PostCallActionUpdate(BaseModel):
    delivery_method: DeliveryMethod | str | None = None
    trigger_statuses: list[str] | None = None
    include_summary: bool | None = None
    include_full_transcript: bool | None = None
    include_sentiment: bool | None = None
    include_extracted_info: bool | None = None
    extracted_variables: list[ExtractedVariable] | None = None
    webhook_url: str | None = None
    webhook_headers: dict[str, str] | None = None
    email_recipients: list[str] | None = None


class PostCallAction(PostCallActionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    created_at: datetime
    updated_at: datetime


# ─── Full agent bundle (for worker + API) ─────────────────────────────────────

class AgentCreateFromDescription(BaseModel):
    """Natural language agent creation request (Step 4)."""

    description: str = Field(..., min_length=10, max_length=8000)
    use_cases: list[str] = Field(default_factory=list)


class AgentAiEditRequest(BaseModel):
    """Natural-language edit for an existing agent configuration."""

    prompt: str = Field(..., min_length=3, max_length=16000)
    preview_only: bool = False
    restart_if_live: bool = True


class AgentCreateFull(BaseModel):
    """Create agent with all related configuration in one request."""

    agent: AgentCreate
    conversational_flow: ConversationalFlowBase | None = None
    call_configuration: CallConfigurationBase | None = None
    post_call_action: PostCallActionBase | None = None


class FullAgentConfig(BaseModel):
    """Merged configuration used by LiveKit worker and detail APIs."""

    agent: Agent
    conversational_flow: ConversationalFlow | None = None
    call_configuration: CallConfiguration | None = None
    post_call_action: PostCallAction | None = None

    def merged_system_prompt(self) -> str:
        """Append enabled flow sections to the base system prompt."""
        base = self.agent.system_prompt.strip()
        if not self.conversational_flow or not self.conversational_flow.sections:
            return base

        enabled = sorted(
            [s for s in self.conversational_flow.sections if s.enabled],
            key=lambda s: s.order,
        )
        if not enabled:
            return base

        flow_lines = ["\n\n--- Conversational Flow ---"]
        for section in enabled:
            flow_lines.append(f"\n## {section.title}\n{section.content.strip()}")
        return base + "".join(flow_lines)


# ─── call_logs extensions ─────────────────────────────────────────────────────

class CallLogAgentFields(BaseModel):
    agent_id: UUID | None = None
    call_status: str | None = "completed"
    call_direction: str | None = "outbound"
    channel_type: str | None = "phone"
    call_transferred: bool = False
    ended_by: str | None = None
    room_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    bulk_call_id: UUID | None = None
    extracted_info: dict[str, Any] = Field(default_factory=dict)