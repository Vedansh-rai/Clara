"""
merge_form.py — Structured onboarding form (JSON) → ExtractedCallData.

An onboarding form is a client-submitted structured document that may:
  - Clarify missing demo details
  - Introduce new constraints
  - Override previously assumed routing logic

This module maps form fields → ExtractedCallData schema, flags conflicts,
and returns a ready-to-merge ExtractedCallData object.

Expected form JSON structure (all fields optional):
{
  "company_name": "...",
  "industry": "...",
  "crm_system": "...",
  "service_area": "...",
  "timezone": "America/New_York",
  "business_hours": {
    "monday": {"open": "08:00", "close": "17:00"},
    "saturday": {"closed": true}
  },
  "emergency_definitions": ["..."],
  "routing_rules": [
    {"trigger": "...", "destination": "...", "priority": 1}
  ],
  "transfer_numbers": {"on_call": "+1-555-000-0000"},
  "after_hours_handling": "...",
  "transfer_timeout_seconds": 60,
  "fallback_logic": "...",
  "integration_rules": ["..."],
  "special_constraints": ["..."]
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from pipeline.schema import DataSource, DayHours, ExtractedCallData, RoutingRule
from pipeline.utils.logger import get_logger, log_event

log = get_logger("merge_form")

_KNOWN_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def merge_form(
    form_input: str | Path | dict,
    client_id: str,
) -> ExtractedCallData:
    """
    Parse an onboarding form (JSON file path, JSON string, or dict)
    and return a validated ExtractedCallData ready for generate_v2.

    Args:
        form_input:  Path to a .json file, a JSON string, or a raw dict.
        client_id:   Client identifier.

    Returns:
        ExtractedCallData with source=DataSource.FORM
    """
    raw = _load_form(form_input)
    log.info(f"[{client_id}] Parsing onboarding form ({len(raw)} top-level fields)…")

    questions: list[str] = []
    evidence: dict[str, str] = {}

    def get(field: str, default: Any = None) -> Any:
        val = raw.get(field, default)
        if val is not None and val != [] and val != {}:
            evidence[field] = f"[form] {field} = {str(val)[:120]}"
        return val

    # Business hours
    bh_raw = get("business_hours")
    business_hours = None
    if bh_raw:
        business_hours = {}
        for day, day_data in bh_raw.items():
            day_lower = day.lower()
            if day_lower not in _KNOWN_DAYS:
                questions.append(f"Unrecognised day in business_hours form field: {day!r}")
                continue
            if isinstance(day_data, dict):
                business_hours[day_lower] = DayHours(
                    open=day_data.get("open"),
                    close=day_data.get("close"),
                    closed=day_data.get("closed", False),
                )
            else:
                questions.append(f"Unexpected format for {day} hours in form: {day_data!r}")

    # Routing rules
    rr_raw = get("routing_rules", [])
    routing_rules = []
    for i, rr in enumerate(rr_raw):
        try:
            routing_rules.append(RoutingRule(**rr))
        except Exception as e:
            questions.append(f"Invalid routing rule #{i + 1} in form: {e}")

    # Transfer numbers
    transfer_numbers = get("transfer_numbers", {})
    if not isinstance(transfer_numbers, dict):
        questions.append(f"transfer_numbers in form is not a dict: {transfer_numbers!r}")
        transfer_numbers = {}

    # Validate timeout
    timeout = get("transfer_timeout_seconds")
    if timeout is not None:
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            questions.append(f"transfer_timeout_seconds in form is not an integer: {timeout!r}")
            timeout = None

    log_event(log, "form_parsed", client_id=client_id, fields_found=len(evidence),
              questions=len(questions))

    if questions:
        log.warning(f"[{client_id}] Form parsing flagged {len(questions)} issue(s):")
        for q in questions:
            log.warning(f"  - {q}")

    return ExtractedCallData(
        source=DataSource.FORM,
        client_id=client_id,
        company_name=get("company_name"),
        industry=get("industry"),
        crm_system=get("crm_system"),
        service_area=get("service_area"),
        business_hours=business_hours,
        timezone=get("timezone"),
        emergency_definitions=get("emergency_definitions", []),
        routing_rules=routing_rules,
        transfer_numbers=transfer_numbers,
        after_hours_handling=get("after_hours_handling"),
        transfer_timeout_seconds=timeout,
        fallback_logic=get("fallback_logic"),
        integration_rules=get("integration_rules", []),
        special_constraints=get("special_constraints", []),
        questions_or_unknowns=questions,
        raw_evidence=evidence,
    )


def _load_form(form_input: str | Path | dict) -> Dict[str, Any]:
    """Load form data from a file, JSON string, or dict."""
    if isinstance(form_input, dict):
        return form_input

    if isinstance(form_input, Path) or (isinstance(form_input, str) and Path(form_input).exists()):
        path = Path(form_input)
        if not path.exists():
            raise FileNotFoundError(f"Form file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # Try parsing as JSON string
    try:
        return json.loads(form_input)
    except json.JSONDecodeError as e:
        raise ValueError(f"Cannot parse form input as JSON: {e}") from e
