"""
schema.py — All Pydantic data models for the Clara onboarding pipeline.

Principle: Every field that may be absent from a call or form is Optional.
           Missing data is represented as None, never guessed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─── Helpers ──────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    """Return current UTC time with timezone info (non-deprecated)."""
    return datetime.now(timezone.utc)


# ─── Enums ───────────────────────────────────────────────────────────────────


class DataSource(str, Enum):
    DEMO = "demo"
    ONBOARDING = "onboarding"
    FORM = "form"


class CallType(str, Enum):
    EMERGENCY = "emergency"
    NON_EMERGENCY = "non_emergency"
    INSPECTION = "inspection"
    GENERAL = "general"


# ─── Sub-models ──────────────────────────────────────────────────────────────


class DayHours(BaseModel):
    """Operating hours for a single day."""

    open: Optional[str] = Field(None, description="Opening time, e.g. '08:00'")
    close: Optional[str] = Field(None, description="Closing time, e.g. '17:00'")
    closed: bool = Field(False, description="True if the business is closed this day")


class RoutingRule(BaseModel):
    """A single routing rule: when trigger fires, send to destination."""

    trigger: str = Field(..., description="Condition that activates this rule")
    destination: str = Field(..., description="Where the call is routed")
    priority: int = Field(1, description="Lower number = higher priority")
    call_type: Optional[CallType] = None
    notes: Optional[str] = None


class ChangeLogEntry(BaseModel):
    """Records a single field change between versions."""

    field: str
    old_value: Any
    new_value: Any
    source: DataSource
    timestamp: datetime = Field(default_factory=_utc_now)
    reason: str = Field(..., description="Why this change was made")


class TranscriptMetadata(BaseModel):
    """Metadata about a transcribed audio file."""

    source_file: str
    duration_seconds: Optional[float] = None
    language: Optional[str] = None
    transcribed_at: datetime = Field(default_factory=_utc_now)
    backend: str = "openai_whisper"


# ─── Core extraction model ────────────────────────────────────────────────────


class ExtractedCallData(BaseModel):
    """
    Structured data extracted from a single call transcript or onboarding form.

    IMPORTANT: Only populate fields that are EXPLICITLY stated in the source.
    If a field is not mentioned, leave it as None.
    Never infer or guess values. Ambiguous items go into questions_or_unknowns.
    """

    # Provenance
    source: DataSource
    client_id: str
    extracted_at: datetime = Field(default_factory=_utc_now)
    transcript_metadata: Optional[TranscriptMetadata] = None

    # Business identity
    company_name: Optional[str] = Field(None, description="Legal or trading name of the business")
    industry: Optional[str] = Field(None, description="e.g. 'Electrical contracting'")
    crm_system: Optional[str] = Field(None, description="CRM in use, e.g. 'Jobber', 'ServiceTitan'")
    service_area: Optional[str] = Field(None, description="Geographic area served")
    office_address: Optional[str] = Field(None, description="Physical office address if mentioned")
    services_supported: List[str] = Field(default_factory=list, description="List of services the business offers")

    # Business hours (keyed by day name, lowercase)
    business_hours: Optional[Dict[str, DayHours]] = Field(
        None,
        description="Map of day -> hours. e.g. {'monday': {'open':'08:00','close':'17:00'}}"
    )
    timezone: Optional[str] = Field(None, description="IANA timezone, e.g. 'America/New_York'")

    # Call handling
    emergency_definitions: List[str] = Field(
        default_factory=list,
        description="Explicit descriptions of what counts as an emergency"
    )
    emergency_routing_rules: List[str] = Field(
        default_factory=list,
        description="Who to call, order, fallback for emergencies"
    )
    non_emergency_routing_rules: List[str] = Field(
        default_factory=list,
        description="Routing rules for non-emergency calls"
    )
    call_transfer_rules: List[str] = Field(
        default_factory=list,
        description="Timeouts, retries, what to say if transfer fails"
    )
    routing_rules: List[RoutingRule] = Field(
        default_factory=list,
        description="Ordered list of call routing rules"
    )
    transfer_numbers: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of label -> phone number for transfers"
    )
    after_hours_handling: Optional[str] = Field(
        None,
        description="How calls should be handled outside business hours"
    )
    after_hours_flow_summary: Optional[str] = Field(
        None,
        description="Summary of after-hours flow"
    )
    office_hours_flow_summary: Optional[str] = Field(
        None,
        description="Summary of office-hours flow"
    )
    transfer_timeout_seconds: Optional[int] = Field(
        None,
        description="How long to wait before declaring a transfer failed"
    )
    fallback_logic: Optional[str] = Field(
        None,
        description="What to do if a transfer fails"
    )

    # Integration / system constraints
    integration_rules: List[str] = Field(
        default_factory=list,
        description="Rules governing CRM/system integrations, e.g. 'Never create sprinkler jobs'"
    )
    integration_constraints: List[str] = Field(
        default_factory=list,
        description="Specific integration constraints"
    )
    special_constraints: List[str] = Field(
        default_factory=list,
        description="Any other hard rules stated by the client"
    )
    notes: Optional[str] = Field(
        None,
        description="Short notes about the account or specific edge cases"
    )

    # Unresolved items — NEVER omit these
    questions_or_unknowns: List[str] = Field(
        default_factory=list,
        description="Fields mentioned but unclear, or topics that need follow-up"
    )

    # Evidence (for auditability)
    raw_evidence: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of field_name -> quote/excerpt that supports the extracted value"
    )


# ─── Agent configuration (versioned) ─────────────────────────────────────────


class AgentConfig(BaseModel):
    """
    A versioned, production-ready configuration for a Clara voice agent.
    v1 = derived from demo call only.
    v2+ = updated after onboarding call or form submission.
    """

    # Identity
    client_id: str
    company_name: Optional[str] = None
    version: int = Field(1, ge=1)
    source_stage: DataSource

    # Timestamps
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    # Operational config (mirrors ExtractedCallData)
    industry: Optional[str] = None
    crm_system: Optional[str] = None
    service_area: Optional[str] = None
    office_address: Optional[str] = None
    services_supported: List[str] = Field(default_factory=list)
    business_hours: Optional[Dict[str, DayHours]] = None
    timezone: Optional[str] = None
    emergency_definitions: List[str] = Field(default_factory=list)
    emergency_routing_rules: List[str] = Field(default_factory=list)
    non_emergency_routing_rules: List[str] = Field(default_factory=list)
    call_transfer_rules: List[str] = Field(default_factory=list)
    routing_rules: List[RoutingRule] = Field(default_factory=list)
    transfer_numbers: Dict[str, str] = Field(default_factory=dict)
    after_hours_handling: Optional[str] = None
    after_hours_flow_summary: Optional[str] = None
    office_hours_flow_summary: Optional[str] = None
    transfer_timeout_seconds: Optional[int] = None
    fallback_logic: Optional[str] = None
    integration_rules: List[str] = Field(default_factory=list)
    integration_constraints: List[str] = Field(default_factory=list)
    special_constraints: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    # Open items — never hidden
    questions_or_unknowns: List[str] = Field(default_factory=list)

    # Version history
    changelog: List[ChangeLogEntry] = Field(default_factory=list)

    # Generated Retell agent prompt
    prompt: Optional[str] = None

    # Source data fingerprint for idempotency
    source_hash: Optional[str] = None
