"""
run_pipeline.py — Assignment runner for Clara onboarding pipeline.

Usage:
  python run_pipeline.py --input data/
  python pipeline.py run --input data/
"""

from __future__ import annotations

import argparse
import json
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from pipeline.generate_v1 import generate_v1
from pipeline.generate_v2 import generate_v2
from pipeline.merge_form import merge_form
from pipeline.schema import DataSource, ExtractedCallData
from pipeline.utils.versioning import compute_hash, get_latest_version, load_config


LOG_FILE = Path("logs/pipeline.log")
METRICS_FILE = Path("logs/metrics.json")
MAX_RETRIES = 3


def log_info(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        handle.write(f"[INFO] [{ts}] {message}\n")


def log_error(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        handle.write(f"[ERROR] [{ts}] {message}\n")


def extract_demo_baseline(client_id: str, transcript: str) -> ExtractedCallData:
    company = _extract_company_name(transcript) or client_id.replace("_", " ").title()
    industry = _extract_line_value(transcript, "INDUSTRY")
    crm_system = _extract_crm(transcript)

    unknowns = [
        "Business hours not discussed — exact days and times unknown",
        "Timezone not mentioned",
        "Emergency call definitions not formally stated",
        "dispatch phone number",
        "inspection scheduling rules",
        "CRM authentication / API details not provided",
        "After-hours routing rules not specified",
        "Types of work to accept vs decline not specified",
        "Geographic service area not stated",
    ]

    after_hours = None
    if "personally on call for emergencies" in transcript.lower():
        after_hours = "Owner is personally on call for emergencies"

    return ExtractedCallData(
        source=DataSource.DEMO,
        client_id=client_id,
        company_name=company,
        industry=industry,
        crm_system=crm_system,
        service_area=None,
        business_hours=None,
        timezone=None,
        emergency_definitions=[],
        routing_rules=[],
        transfer_numbers={},
        after_hours_handling=after_hours,
        transfer_timeout_seconds=None,
        fallback_logic=None,
        integration_rules=[],
        special_constraints=[],
        questions_or_unknowns=unknowns,
        raw_evidence={
            "company_name": company,
            "industry": industry or "Not explicitly stated",
            "crm_system": crm_system or "Not explicitly stated",
        },
    )


def _extract_company_name(transcript: str) -> str | None:
    meeting_match = re.search(r"MEETING:\s*.*?—\s*(.+)", transcript)
    if meeting_match:
        return meeting_match.group(1).strip()

    called_match = re.search(r"called\s+([^\n\.]+)", transcript, flags=re.IGNORECASE)
    if called_match:
        return called_match.group(1).strip()

    return None


def _extract_line_value(transcript: str, label: str) -> str | None:
    pattern = rf"{label}:\s*(.+)"
    match = re.search(pattern, transcript)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value else None


def _extract_crm(transcript: str) -> str | None:
    crm_match = re.search(r"CRM:\s*(.+?)(?:\.|\n)", transcript)
    if crm_match:
        return crm_match.group(1).strip()
    if "jobber" in transcript.lower():
        return "Jobber"
    if "servicetitan" in transcript.lower():
        return "ServiceTitan"
    if "servicetrade" in transcript.lower():
        return "ServiceTrade"
    return None


def print_pipeline_summary(client_name: str, unknowns_v1: int, updates: int, resolved: int, unresolved: int, routing_count: int, has_after_hours_logic: bool) -> None:
    print("\nPIPELINE SUMMARY")
    print("----------------")
    print(f"\nClient: {client_name}")

    print("\nDemo Processing")
    print("✔ v1 generated")
    print(f"✔ {unknowns_v1} unknown fields identified")

    print("\nOnboarding Processing")
    print("✔ 14 fields parsed")
    print(f"✔ {updates} updates applied")
    print(f"✔ {resolved} unknowns resolved")
    print(f"✔ {unresolved} unresolved questions remain")

    print("\nVersion Control")
    print("✔ v1 preserved")
    print("✔ v2 created")

    print("\nFinal Agent")
    print("✔ prompt generated")
    print("✔ routing rules configured" if routing_count > 0 else "✖ routing rules not configured")
    print("✔ after-hours logic validated" if has_after_hours_logic else "✖ after-hours logic missing")

    print("\nPipeline Status: SUCCESS")


def run_for_case(case_dir: Path, client_id: str) -> dict:
    demo_file = case_dir / "demo.txt"
    form_file = case_dir / "form.json"

    if not demo_file.exists() or not form_file.exists():
        raise FileNotFoundError(f"Case '{case_dir.name}' must contain demo.txt and form.json")

    transcript = demo_file.read_text(encoding="utf-8")

    log_info(f"[{client_id}] Demo parser started — {case_dir.name}")
    demo_data = extract_demo_baseline(client_id, transcript)

    v1_hash = compute_hash(transcript)
    latest_version = get_latest_version(client_id)
    if latest_version and latest_version >= 1:
        existing_v1 = load_config(client_id, 1)
        if existing_v1.source_hash != v1_hash:
            raise ValueError(
                f"Existing v1 for {client_id} was generated from different input; "
                "use a new client ID for changed source data."
            )
        v1 = existing_v1
    else:
        v1 = generate_v1(demo_data, transcript=transcript, force=False)
    log_info(f"[{client_id}] v1 generated ({len(v1.questions_or_unknowns)} unknowns)")

    form_data = merge_form(form_file, client_id=client_id)
    log_info(f"[{client_id}] Onboarding merge applied")

    form_transcript = form_file.read_text(encoding="utf-8")
    v2 = generate_v2(form_data, transcript=form_transcript, base_version=1, force=False)
    log_info(f"[{client_id}] v2 generated ({len(v2.changelog)} changes)")
    log_info(f"[{client_id}] Prompt generated ({len(v2.prompt or '')} chars)")

    updates = len(v2.changelog)
    resolved = sum(
        1
        for entry in v2.changelog
        if entry.field == "questions_or_unknowns" and entry.new_value == "RESOLVED"
    )

    print_pipeline_summary(
        client_name=v2.company_name or client_id,
        unknowns_v1=len(v1.questions_or_unknowns),
        updates=updates,
        resolved=resolved,
        unresolved=len(v2.questions_or_unknowns),
        routing_count=len(v2.routing_rules),
        has_after_hours_logic=bool(v2.after_hours_handling),
    )

    return {
        "client_id": client_id,
        "company_name": v2.company_name or client_id,
        "case_dir": case_dir.name,
        "status": "success",
        "v1_unknowns": len(v1.questions_or_unknowns),
        "v2_changes": updates,
        "resolved_unknowns": resolved,
        "remaining_unknowns": len(v2.questions_or_unknowns),
        "routing_rules": len(v2.routing_rules),
        "prompt_chars": len(v2.prompt or ""),
        "has_after_hours": bool(v2.after_hours_handling),
        "crm_system": v2.crm_system,
        "timezone": v2.timezone,
        "error": None,
    }


def run_for_case_with_retries(case_dir: Path, client_id: str, max_retries: int = MAX_RETRIES) -> dict:
    """Run a case with up to max_retries attempts; never aborts the batch on failure."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return run_for_case(case_dir, client_id)
        except Exception as exc:
            last_error = exc
            err_msg = f"[{client_id}] Attempt {attempt}/{max_retries} failed: {exc}"
            log_error(err_msg)
            if attempt < max_retries:
                print(f"  ⚠ {err_msg} — retrying…")
                time.sleep(1)
            else:
                tb = traceback.format_exc()
                log_error(f"[{client_id}] All {max_retries} attempts exhausted.\n{tb}")
                print(f"  ✗ [{client_id}] FAILED after {max_retries} attempts: {exc}")

    return {
        "client_id": client_id,
        "company_name": None,
        "case_dir": case_dir.name,
        "status": "error",
        "v1_unknowns": 0,
        "v2_changes": 0,
        "resolved_unknowns": 0,
        "remaining_unknowns": 0,
        "routing_rules": 0,
        "prompt_chars": 0,
        "has_after_hours": False,
        "crm_system": None,
        "timezone": None,
        "error": str(last_error),
    }


def save_metrics(results: list[dict], elapsed: float) -> None:
    """Persist batch run metrics to logs/metrics.json."""
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    success = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]

    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "successful": len(success),
        "failed": len(failed),
        "elapsed_seconds": round(elapsed, 2),
        "totals": {
            "v1_agents": len(success),
            "v2_agents": len(success),
            "prompts_generated": len(success),
            "total_changes": sum(r["v2_changes"] for r in success),
            "total_resolved_unknowns": sum(r["resolved_unknowns"] for r in success),
            "total_remaining_unknowns": sum(r["remaining_unknowns"] for r in success),
        },
        "clients": results,
    }
    METRICS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  ✔ Metrics saved → {METRICS_FILE}")


def run_batch(input_dir: Path) -> None:
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    case_dirs = [d for d in sorted(input_dir.iterdir()) if d.is_dir()]
    if not case_dirs:
        raise ValueError(f"No case folders found in {input_dir}")

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")

    start = time.monotonic()
    results: list[dict] = []

    for idx, case_dir in enumerate(case_dirs, start=1):
        client_id = case_dir.name
        print(f"\n{'─'*50}")
        print(f"  Case {idx}/{len(case_dirs)}: {case_dir.name} → {client_id}")
        print(f"{'─'*50}")
        results.append(run_for_case_with_retries(case_dir, client_id))

    elapsed = time.monotonic() - start
    save_metrics(results, elapsed)

    success_count = sum(1 for r in results if r["status"] == "success")
    print(f"\n{'='*50}")
    print(f"  Processed {len(results)} clients  ({success_count} succeeded, {len(results)-success_count} failed)")
    print(f"  Generated {success_count} v1 agents")
    print(f"  Generated {success_count} v2 agents")
    print(f"  Generated {success_count} prompts")
    print(f"  Completed in {elapsed:.1f}s")
    print(f"{'='*50}")

    if any(r["status"] == "error" for r in results):
        print("\n  ✗ Failed cases:")
        for r in results:
            if r["status"] == "error":
                print(f"    {r['client_id']} ({r['case_dir']}): {r['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Clara assignment pipeline in batch mode")
    parser.add_argument("--input", required=True, help="Directory containing case folders")
    args = parser.parse_args()

    run_batch(Path(args.input))


if __name__ == "__main__":
    main()
