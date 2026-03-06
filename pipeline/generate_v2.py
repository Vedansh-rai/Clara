"""
generate_v2.py — AgentConfig v1 + onboarding ExtractedCallData → AgentConfig v2.

Rules:
- New values (were null in v1): fill in, log as onboarding_confirmed.
- Changed values: override, log old and new.
- Unchanged values: no-op (idempotent).
- Conflicts that are ambiguous: flag in questions_or_unknowns, do NOT silently override.
- v1 is NEVER modified. v2 is always a new file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pipeline.prompt_builder import build_prompt
from pipeline.schema import AgentConfig, ChangeLogEntry, DataSource, ExtractedCallData
from pipeline.utils.logger import get_logger, log_event
from pipeline.utils.versioning import (
    compute_hash,
    get_latest_version,
    load_config,
    save_config,
    save_transcript,
)

log = get_logger("generate_v2")


def generate_v2(
    extracted: ExtractedCallData,
    transcript: Optional[str] = None,
    base_version: Optional[int] = None,
    force: bool = False,
) -> AgentConfig:
    """
    Update the latest AgentConfig with onboarding data to produce a new version.

    Args:
        extracted:     ExtractedCallData from onboarding call or merged form.
        transcript:    Raw transcript (saved for audit).
        base_version:  Specific version to update from (defaults to latest).
        force:         Allow overwriting an existing version.

    Returns:
        New AgentConfig (v2 or higher) — saved to disk.
    """
    client_id = extracted.client_id
    log.info(f"[{client_id}] Starting v2 generation from {extracted.source.value} data…")

    # Load the base config
    v = base_version or get_latest_version(client_id)
    if v is None:
        raise ValueError(
            f"No existing config found for client '{client_id}'. "
            "Run 'demo' stage first to generate v1."
        )

    base = load_config(client_id, v)
    new_version = base.version + 1
    log.info(f"[{client_id}] Updating v{base.version} → v{new_version}")

    # Idempotency check
    if transcript:
        new_hash = compute_hash(transcript)
        try:
            existing_new = load_config(client_id, new_version)
            if existing_new.source_hash == new_hash and not force:
                log.info(f"[{client_id}] v{new_version} already exists with same transcript hash. Skipping.")
                return existing_new
        except FileNotFoundError:
            pass

    # Clone base config
    updated = base.model_copy(deep=True)
    updated.version = new_version
    updated.source_stage = extracted.source
    updated.updated_at = datetime.now(timezone.utc)
    updated.source_hash = compute_hash(transcript) if transcript else None

    changelog: list[ChangeLogEntry] = list(base.changelog)

    # ─── Field-by-field diff + merge ─────────────────────────────────────────

    def apply(field: str, new_val: Any) -> None:
        """Apply a single field update with change tracking."""
        old_val = getattr(updated, field)

        if new_val is None or new_val == [] or new_val == {}:
            # No new data provided — preserve existing
            return

        if old_val is None or old_val == [] or old_val == {}:
            # New data fills a gap
            setattr(updated, field, new_val)
            changelog.append(ChangeLogEntry(
                field=field,
                old_value=old_val,
                new_value=new_val,
                source=extracted.source,
                reason="onboarding_confirmed — field was previously unknown",
            ))
            log.info(f"  + {field}: (null) → {_short(new_val)}")
        elif old_val == new_val:
            # Identical — no-op
            log.debug(f"  = {field}: unchanged")
        else:
            # Conflict — check if it can be resolved unambiguously
            if _is_ambiguous_conflict(field, old_val, new_val):
                msg = (
                    f"Conflict on field '{field}': "
                    f"demo said {_short(old_val)!r}, "
                    f"onboarding says {_short(new_val)!r} — requires manual review"
                )
                if msg not in updated.questions_or_unknowns:
                    updated.questions_or_unknowns.append(msg)
                log.warning(f"  ⚠ {field}: CONFLICT flagged for review")
            else:
                # Onboarding overrides demo
                setattr(updated, field, new_val)
                changelog.append(ChangeLogEntry(
                    field=field,
                    old_value=old_val,
                    new_value=new_val,
                    source=extracted.source,
                    reason="onboarding_override — onboarding data supersedes demo assumption",
                ))
                log.info(f"  ~ {field}: {_short(old_val)} → {_short(new_val)}")

    # Apply all fields
    apply("company_name", extracted.company_name)
    apply("industry", extracted.industry)
    apply("crm_system", extracted.crm_system)
    apply("service_area", extracted.service_area)
    apply("business_hours", extracted.business_hours)
    apply("timezone", extracted.timezone)
    apply("after_hours_handling", extracted.after_hours_handling)
    apply("transfer_timeout_seconds", extracted.transfer_timeout_seconds)
    apply("fallback_logic", extracted.fallback_logic)

    # Lists: merge (deduplicate), not replace
    _merge_list(updated, "emergency_definitions", extracted.emergency_definitions, changelog, extracted.source)
    _merge_list(updated, "routing_rules", extracted.routing_rules, changelog, extracted.source)
    _merge_list(updated, "integration_rules", extracted.integration_rules, changelog, extracted.source)
    _merge_list(updated, "special_constraints", extracted.special_constraints, changelog, extracted.source)

    # Transfer numbers: merge dict
    _merge_dict(updated, "transfer_numbers", extracted.transfer_numbers, changelog, extracted.source)

    # Unknowns: carry forward unresolved items, add new ones, remove resolved
    new_unknowns = extracted.questions_or_unknowns
    # Retain v1 unknowns that weren't addressed by onboarding data
    resolved = _resolved_unknowns(base.questions_or_unknowns, extracted)
    carried_forward = [q for q in base.questions_or_unknowns if q not in resolved]
    combined_unknowns = carried_forward + [q for q in new_unknowns if q not in carried_forward]
    updated.questions_or_unknowns = combined_unknowns

    if resolved:
        for q in resolved:
            changelog.append(ChangeLogEntry(
                field="questions_or_unknowns",
                old_value=q,
                new_value="RESOLVED",
                source=extracted.source,
                reason="Onboarding data addressed this open question",
            ))
            log.info(f"  ✓ Resolved: {q}")

    # Finalize
    updated.changelog = changelog

    # Regenerate prompt with updated config
    updated.prompt = build_prompt(updated)

    # Save transcript
    stage_label = extracted.source.value
    if transcript:
        save_transcript(client_id, stage_label, transcript)

    path = save_config(updated, overwrite=force)
    log_event(log, "v2_generated", client_id=client_id, version=new_version,
              changes=len(changelog) - len(base.changelog), path=str(path))

    log.info(f"[{client_id}] ✓ v{new_version} saved to {path} "
             f"({len(changelog) - len(base.changelog)} changes applied)")
    if updated.questions_or_unknowns:
        log.warning(f"[{client_id}] {len(updated.questions_or_unknowns)} open question(s) remain.")

    return updated


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _merge_list(
    config: AgentConfig,
    field: str,
    new_items: list,
    changelog: list[ChangeLogEntry],
    source: DataSource,
) -> None:
    """Add new list items that aren't already present (by string representation)."""
    existing = getattr(config, field)
    existing_strs = {str(x) for x in existing}
    added = []
    for item in new_items:
        if str(item) not in existing_strs:
            existing.append(item)
            added.append(item)
            existing_strs.add(str(item))

    if added:
        changelog.append(ChangeLogEntry(
            field=field,
            old_value=None,
            new_value=added,
            source=source,
            reason=f"onboarding_confirmed — {len(added)} new item(s) added",
        ))
        log.info(f"  + {field}: added {len(added)} item(s)")


def _merge_dict(
    config: AgentConfig,
    field: str,
    new_dict: dict,
    changelog: list[ChangeLogEntry],
    source: DataSource,
) -> None:
    """Merge a dict field. Existing keys preserved; new keys added; conflicts flagged."""
    existing: dict = getattr(config, field)
    added = {}
    for k, v in new_dict.items():
        if k not in existing:
            existing[k] = v
            added[k] = v
        elif existing[k] != v:
            msg = f"Transfer number conflict for '{k}': had {existing[k]!r}, onboarding says {v!r}"
            if msg not in config.questions_or_unknowns:
                config.questions_or_unknowns.append(msg)

    if added:
        changelog.append(ChangeLogEntry(
            field=field,
            old_value=None,
            new_value=added,
            source=source,
            reason="onboarding_confirmed — new transfer numbers added",
        ))
        log.info(f"  + {field}: added {list(added.keys())}")


def _is_ambiguous_conflict(field: str, old_val: Any, new_val: Any) -> bool:
    """
    Decide if a conflict is ambiguous enough to flag rather than silently override.
    Currently: business_hours and timezone conflicts are always flagged.
    """
    high_risk_fields = {"business_hours", "timezone", "transfer_timeout_seconds"}
    return field in high_risk_fields


def _resolved_unknowns(old_unknowns: list[str], extracted: ExtractedCallData) -> list[str]:
    """
    Determine which previously unknown items have been addressed by new data.
    Simple heuristic: if a keyword from the unknown string matches a now-filled field.
    """
    resolved = []
    field_keywords = {
        "business_hours": ["business hours", "hours", "open", "close"],
        "timezone": ["timezone", "time zone"],
        "transfer_timeout_seconds": ["timeout", "transfer fail"],
        "fallback_logic": ["fallback", "transfer fails"],
        "emergency_definitions": ["emergency definition", "definition of emergency"],
        "after_hours_handling": ["after hours", "after-hours"],
        "transfer_numbers": ["transfer number", "phone number", "on-call number"],
        "crm_system": ["crm", "jobber", "servicetitan"],
    }

    for unknown in old_unknowns:
        for field, keywords in field_keywords.items():
            val = getattr(extracted, field, None)
            has_value = val is not None and val != [] and val != {}
            if has_value and any(kw in unknown.lower() for kw in keywords):
                resolved.append(unknown)
                break

    return resolved


def _short(val: Any, max_len: int = 80) -> str:
    """Compact string representation for logging."""
    s = str(val)
    return s if len(s) <= max_len else s[:max_len] + "…"
