"""
extract.py — Transcript/notes → ExtractedCallData via LLM.

Core rule: ONLY extract what is EXPLICITLY stated.
           NEVER infer, assume, or hallucinate values.
           Ambiguous/unclear items go into questions_or_unknowns.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pipeline.llm_client import complete
from pipeline.schema import DataSource, ExtractedCallData, TranscriptMetadata
from pipeline.utils.logger import get_logger, log_event
from pipeline.utils.versioning import compute_hash

log = get_logger("extract")

_SYSTEM_PROMPT = """
You are a data extraction specialist for Clara AI, an AI voice agent platform for service trade businesses.

Your task: Extract structured operational configuration data from a call transcript or meeting notes.

CRITICAL RULES — follow these exactly:
1. Only extract facts that are EXPLICITLY stated in the text. Do NOT infer or assume anything.
2. If a field is not mentioned or is unclear, set it to null (for single values) or [] (for lists).
3. Anything that was mentioned but is ambiguous, contradictory, or needs follow-up → add to questions_or_unknowns.
4. For raw_evidence: quote the EXACT phrase or sentence from the transcript that supports each extracted value.
5. Return ONLY valid JSON. No markdown, no explanation, just the JSON object.

OUTPUT SCHEMA:
{
  "company_name": string | null,
  "industry": string | null,
  "crm_system": string | null,
  "service_area": string | null,
  "office_address": "string" | null,
  "services_supported": ["string", ...],
  "business_hours": {
    "monday":    {"open": "HH:MM" | null, "close": "HH:MM" | null, "closed": bool},
    "tuesday":   {...},
    "wednesday": {...},
    "thursday":  {...},
    "friday":    {...},
    "saturday":  {...},
    "sunday":    {...}
  } | null,
  "timezone": string | null,
  "emergency_definitions": ["string", ...],
  "emergency_routing_rules": ["string", ...],
  "non_emergency_routing_rules": ["string", ...],
  "call_transfer_rules": ["string", ...],
  "routing_rules": [
    {
      "trigger": "string",
      "destination": "string",
      "priority": int,
      "call_type": "emergency" | "non_emergency" | "inspection" | "general" | null,
      "notes": "string" | null
    }
  ],
  "transfer_numbers": {"label": "phone_number"},
  "after_hours_handling": string | null,
  "after_hours_flow_summary": string | null,
  "office_hours_flow_summary": string | null,
  "transfer_timeout_seconds": int | null,
  "fallback_logic": string | null,
  "integration_rules": ["string", ...],
  "integration_constraints": ["string", ...],
  "special_constraints": ["string", ...],
  "notes": string | null,
  "questions_or_unknowns": ["string", ...],
  "raw_evidence": {"field_name": "exact quote from transcript"}
}

IMPORTANT questions_or_unknowns examples:
- "Business hours mentioned but exact times not stated"
- "Emergency definition unclear — client said 'urgent' but did not define criteria"
- "Transfer number for after-hours not provided"
- "Timezone not mentioned"
- "CRM integration status uncertain — in progress but not confirmed"
""".strip()


def extract_from_transcript(
    transcript: str,
    source: DataSource,
    client_id: str,
    metadata: Optional[TranscriptMetadata] = None,
) -> ExtractedCallData:
    """
    Run LLM extraction on a transcript or notes string.

    Args:
        transcript: Raw text of the call transcript or meeting notes.
        source:     'demo', 'onboarding', or 'form'.
        client_id:  Client identifier for logging/tracing.
        metadata:   Optional transcription metadata.

    Returns:
        Validated ExtractedCallData. Unknown fields are null/[], not guessed.
    """
    log.info(f"[{client_id}] Extracting from {source.value} transcript ({len(transcript)} chars)…")

    user_prompt = (
        f"SOURCE TYPE: {source.value.upper()}\n"
        f"CLIENT ID: {client_id}\n\n"
        f"TRANSCRIPT / NOTES:\n{transcript}"
    )

    raw_response = complete(system=_SYSTEM_PROMPT, user=user_prompt, json_mode=True)
    log_event(log, "llm_extraction_done", client_id=client_id, source=source.value,
              response_len=len(raw_response))

    # Parse and validate
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as e:
        log.error(f"LLM returned invalid JSON: {e}\nRaw: {raw_response[:500]}")
        raise ValueError(f"LLM extraction returned invalid JSON: {e}") from e

    # Inject provenance fields
    data["source"] = source.value
    data["client_id"] = client_id
    if metadata:
        data["transcript_metadata"] = metadata.model_dump()

    try:
        extracted = ExtractedCallData.model_validate(data)
    except Exception as e:
        log.error(f"Schema validation failed: {e}")
        raise

    _log_summary(extracted)
    return extracted


def _log_summary(data: ExtractedCallData) -> None:
    filled = sum(
        1 for f in [
            data.company_name, data.business_hours, data.timezone,
            data.transfer_timeout_seconds, data.fallback_logic, data.after_hours_handling,
        ]
        if f is not None
    ) + len(data.routing_rules) + len(data.emergency_definitions)

    log.info(
        f"  Extracted: company={data.company_name!r} industry={data.industry!r} "
        f"crm={data.crm_system!r} tz={data.timezone!r}"
    )
    log.info(
        f"  Rules: routing={len(data.routing_rules)} emergency_defs={len(data.emergency_definitions)} "
        f"integration_rules={len(data.integration_rules)}"
    )
    if data.questions_or_unknowns:
        log.warning(f"  ⚠ {len(data.questions_or_unknowns)} open questions:")
        for q in data.questions_or_unknowns:
            log.warning(f"    - {q}")
