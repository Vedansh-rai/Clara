"""
test_pipeline.py — End-to-end dry-run test using Ben's Electric sample data.
Run: python test_pipeline.py
"""

import os
import sys
import pathlib

os.environ["CONFIGS_DIR"] = "configs"
os.environ["LOGS_DIR"] = "logs"

# Ensure pipeline is importable from project root
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from pipeline.schema import ExtractedCallData, DataSource, RoutingRule
from pipeline.generate_v1 import generate_v1
from pipeline.generate_v2 import generate_v2
from pipeline.merge_form import merge_form
from pipeline.utils.versioning import get_latest_version
from pipeline.prompt_builder import build_prompt

print("=" * 60)
print("  Clara Pipeline — Ben's Electric End-to-End Test")
print("=" * 60)

# ── Step 1: Simulate demo extraction ──────────────────────────────────────────
demo_transcript = pathlib.Path("cases/bens_electric/demo.txt").read_text(encoding="utf-8")

demo_extracted = ExtractedCallData(
    source=DataSource.DEMO,
    client_id="bens_electric",
    company_name="Ben's Electric",
    industry="Electrical contracting",
    crm_system="Jobber",
    service_area=None,
    business_hours=None,
    timezone=None,
    emergency_definitions=[
        "Ben Penoyer personally handles after-hours emergency calls"
    ],
    routing_rules=[],
    transfer_numbers={},
    after_hours_handling="Ben is personally on call for emergencies",
    transfer_timeout_seconds=None,
    fallback_logic=None,
    integration_rules=[],
    special_constraints=[],
    questions_or_unknowns=[
        "Business hours not discussed — exact days and times unknown",
        "Timezone not mentioned",
        "Emergency call definitions not formally stated",
        "Transfer phone number for Ben Penoyer not provided",
        "After-hours routing rules not specified",
        "Transfer timeout / fallback logic not discussed",
        "Integration rules for Jobber not finalized — integration still in progress",
        "Types of work to accept vs decline not specified",
        "Geographic service area not stated",
    ],
    raw_evidence={
        "company_name": "Ben Penoyer is running his own electrical business",
        "crm_system": "Using Jobber CRM with plans for integration with Clara AI",
        "after_hours_handling": "personally on call for emergencies",
    },
)

print(f"\n[STEP 1] Generating v1 from demo data...")
v1 = generate_v1(demo_extracted, transcript=demo_transcript, force=True)
print(f"  ✓ v1 saved")
print(f"  Company      : {v1.company_name}")
print(f"  CRM          : {v1.crm_system}")
print(f"  Business hrs : {v1.business_hours}")
print(f"  Timezone     : {v1.timezone}")
print(f"  Open Qs      : {len(v1.questions_or_unknowns)}")
print(f"  Prompt chars : {len(v1.prompt or '')}")

# ── Step 2: Parse onboarding form ─────────────────────────────────────────────
print(f"\n[STEP 2] Parsing onboarding form...")
form_data = merge_form("cases/bens_electric/form.json", client_id="bens_electric")
print(f"  ✓ Form parsed")
print(f"  Company      : {form_data.company_name}")
print(f"  Timezone     : {form_data.timezone}")
print(f"  Business hrs : {bool(form_data.business_hours)} ({len(form_data.business_hours or {})} days)")
print(f"  Transfer #s  : {list(form_data.transfer_numbers.keys())}")
print(f"  Routing rules: {len(form_data.routing_rules)}")
print(f"  Integr rules : {len(form_data.integration_rules)}")
print(f"  Constraints  : {len(form_data.special_constraints)}")

# ── Step 3: Generate v2 ───────────────────────────────────────────────────────
print(f"\n[STEP 3] Generating v2 from onboarding form...")
v2 = generate_v2(form_data, base_version=1, force=True)
print(f"  ✓ v2 saved")
print(f"  Changelog    : {len(v2.changelog)} entries")
print(f"  Remaining Qs : {len(v2.questions_or_unknowns)}")
print(f"  Prompt chars : {len(v2.prompt or '')}")

# ── Step 4: Print changelog ───────────────────────────────────────────────────
print(f"\n{'=' * 60}")
print("  CHANGELOG (v1 → v2)")
print(f"{'=' * 60}")
for entry in v2.changelog:
    old_s = str(entry.old_value)[:50] if entry.old_value is not None else "null"
    new_s = str(entry.new_value)[:50]
    print(f"  [{entry.source.value}] {entry.field}")
    print(f"    {old_s!r} → {new_s!r}")

# ── Step 5: Remaining questions ───────────────────────────────────────────────
if v2.questions_or_unknowns:
    print(f"\n{'=' * 60}")
    print(f"  REMAINING OPEN QUESTIONS ({len(v2.questions_or_unknowns)})")
    print(f"{'=' * 60}")
    for q in v2.questions_or_unknowns:
        print(f"  ⚠  {q}")
else:
    print("\n  ✓ All open questions resolved!")

# ── Step 6: Show output files ─────────────────────────────────────────────────
print(f"\n{'=' * 60}")
print("  OUTPUT FILES")
print(f"{'=' * 60}")
for f in sorted(pathlib.Path("configs/bens_electric").iterdir()):
    print(f"  {f}  ({f.stat().st_size:,} bytes)")

# ── Step 7: Idempotency check ─────────────────────────────────────────────────
print(f"\n[STEP 4] Idempotency check — re-running generate_v2 with same data...")
v2b = generate_v2(form_data, base_version=1, force=True)
assert v2b.version == v2.version, "Version mismatch — idempotency broken"
assert len(v2b.changelog) == len(v2.changelog), "Changelog length mismatch"
print(f"  ✓ Idempotent — same output produced on re-run")

# ── Step 8: Verify v1 not modified ────────────────────────────────────────────
from pipeline.utils.versioning import load_config
v1_reloaded = load_config("bens_electric", 1)
assert v1_reloaded.version == 1, "v1 version corrupted"
assert v1_reloaded.timezone is None, "v1 should not have timezone (was unknown)"
print(f"  ✓ v1 immutability confirmed — v1.timezone is still None")

print(f"\n{'=' * 60}")
print("  ALL TESTS PASSED")
print(f"{'=' * 60}")
