"""
generate_v1.py — Demo ExtractedCallData → AgentConfig v1.

v1 represents the preliminary agent configuration based ONLY on the demo call.
All unknowns are preserved. Nothing is invented.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline.prompt_builder import build_prompt
from pipeline.schema import AgentConfig, DataSource, ExtractedCallData
from pipeline.utils.logger import get_logger, log_event
from pipeline.utils.versioning import (
    compute_hash,
    get_latest_version,
    save_config,
    save_transcript,
)

log = get_logger("generate_v1")


def generate_v1(
    extracted: ExtractedCallData,
    transcript: Optional[str] = None,
    force: bool = False,
) -> AgentConfig:
    """
    Create AgentConfig v1 from demo call extraction.

    Args:
        extracted:  ExtractedCallData from the demo call.
        transcript: Raw transcript text (saved for auditability).
        force:      Overwrite existing v1 if present.

    Returns:
        AgentConfig v1 — saved to disk.
    """
    if extracted.source != DataSource.DEMO:
        log.warning(
            f"Expected source=demo, got source={extracted.source.value}. "
            "Proceeding, but verify this is intentional."
        )

    client_id = extracted.client_id
    log.info(f"[{client_id}] Generating v1 config from demo data…")

    # Check idempotency — skip if same input was already processed
    existing_version = get_latest_version(client_id)
    if existing_version == 1 and not force:
        from pipeline.utils.versioning import load_config
        existing = load_config(client_id, 1)
        if transcript and existing.source_hash == compute_hash(transcript):
            log.info(f"[{client_id}] v1 already exists with same transcript hash. Skipping.")
            return existing

    # Build config from extracted data
    now = datetime.now(timezone.utc)
    config = AgentConfig(
        client_id=client_id,
        company_name=extracted.company_name,
        version=1,
        source_stage=DataSource.DEMO,
        created_at=now,
        updated_at=now,
        industry=extracted.industry,
        crm_system=extracted.crm_system,
        service_area=extracted.service_area,
        business_hours=extracted.business_hours,
        timezone=extracted.timezone,
        emergency_definitions=extracted.emergency_definitions,
        routing_rules=extracted.routing_rules,
        transfer_numbers=extracted.transfer_numbers,
        after_hours_handling=extracted.after_hours_handling,
        transfer_timeout_seconds=extracted.transfer_timeout_seconds,
        fallback_logic=extracted.fallback_logic,
        integration_rules=extracted.integration_rules,
        special_constraints=extracted.special_constraints,
        questions_or_unknowns=extracted.questions_or_unknowns,
        changelog=[],  # v1 has no prior state to diff against
        source_hash=compute_hash(transcript) if transcript else None,
    )

    # Generate Retell agent prompt
    config.prompt = build_prompt(config)

    # Persist
    if transcript:
        save_transcript(client_id, "demo", transcript)

    path = save_config(config, overwrite=force)
    log_event(log, "v1_generated", client_id=client_id, path=str(path),
              unknowns=len(config.questions_or_unknowns))

    log.info(f"[{client_id}] ✓ v1 saved to {path}")
    if config.questions_or_unknowns:
        log.warning(
            f"[{client_id}] v1 has {len(config.questions_or_unknowns)} open question(s) "
            "— will be resolved during onboarding."
        )

    return config
