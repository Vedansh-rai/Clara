# Clara AI - Assignment Pipeline

## Problem

Converting messy conversations into structured agent configurations.

## Solution

The pipeline converts demo and onboarding inputs into versioned agent artifacts.

```
Demo Call
↓
Rule Extraction
↓
v1 Agent Spec
↓
Onboarding Merge
↓
v2 Agent Spec
↓
Prompt Generation
```

## Features

✔ versioned agent configs  
✔ explicit unknown handling  
✔ conflict-aware merge engine  
✔ prompt generation  
✔ batch processing  
✔ idempotent pipeline

## Architecture Diagram

```
Conversation Data
	│
	▼
Demo Parser
	│
	▼
v1 Agent Config
	│
	▼
Onboarding Parser
	│
	▼
Merge Engine
	│
	▼
v2 Agent Config
	│
	▼
Prompt Generator
	│
	▼
Clara Voice Agent
```

## Batch Runner

Run all cases:

```bash
# install dependencies
pip install -r requirements.txt

# run pipeline
python pipeline.py run --input data/

# run tests
pytest
```

Expected output:

```text
Processed 10 files (5 demo + 5 onboarding)
Generated 5 v1 agents
Generated 5 v2 agents
Generated 5 prompts
```

## Quick Start

1. **Install**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Run pipeline**:
   ```bash
   python pipeline.py run --input data/
   ```

3. **Run tests**:
   ```bash
   python pipeline.py test
   ```

## Example Output

### account_memo.json (per account, per version)
```json
{
  "account_id": "client_001",
  "company_name": "Fire Protection Services",
  "business_hours": {
    "monday": { "open": "08:00", "close": "17:00" },
    "tuesday": { "open": "08:00", "close": "17:00" },
    "wednesday": { "open": "08:00", "close": "17:00" },
    "thursday": { "open": "08:00", "close": "17:00" },
    "friday": { "open": "08:00", "close": "16:00" },
    "saturday": null,
    "sunday": null
  },
  "office_address": null,
  "services_supported": null,
  "emergency_definition": "Active fire alarm, gas leak, suppression system failure",
  "emergency_routing_rules": [
    { "trigger": "emergency", "destination": "dispatch_line", "priority": 1 }
  ],
  "non_emergency_routing_rules": [
    { "trigger": "scheduling", "destination": "office_line", "priority": 2 }
  ],
  "call_transfer_rules": {
    "dispatch_line": "+12145550100",
    "office_line": "+12145550101"
  },
  "integration_constraints": [
    "All new jobs must be entered into ServiceTrade before dispatch"
  ],
  "after_hours_flow_summary": "Greet caller, ask purpose, confirm emergency. If emergency: collect name, phone, address and transfer to dispatch. If transfer fails, apologize and confirm dispatch will follow up. If non-emergency: collect details and confirm next-business-day follow-up.",
  "office_hours_flow_summary": "Greet caller, ask purpose, collect name and phone, route to appropriate department. If transfer fails, collect details and confirm callback.",
  "questions_or_unknowns": [
    "dispatch phone number",
    "inspection scheduling rules",
    "ServiceTrade API credentials"
  ],
  "notes": "Client uses ServiceTrade CRM. Emergency transfers have 60s timeout before fallback."
}
```

### v1.json (from demo call)
```json
{
  "version": 1,
  "source_stage": "demo",
  "company_name": "Fire Protection Services",
  "crm_system": "ServiceTrade",
  "questions_or_unknowns": [
    "Business hours not discussed",
    "Timezone not mentioned",
    "Emergency definitions not stated"
  ]
}
```

### v2.json (after onboarding)
```json
{
  "version": 2,
  "source_stage": "form",
  "company_name": "Fire Protection Services",
  "crm_system": "ServiceTrade",
  "service_area": "North Texas commercial zone",
  "timezone": "America/Chicago",
  "business_hours": {
    "monday": { "open": "08:00", "close": "17:00" },
    "friday": { "open": "08:00", "close": "16:00" }
  },
  "transfer_timeout_seconds": 60,
  "questions_or_unknowns": [
    "dispatch phone number",
    "inspection scheduling rules",
    "ServiceTrade authentication"
  ],
  "changelog": [
    {
      "field": "timezone",
      "old_value": null,
      "new_value": "America/Chicago",
      "source": "form",
      "reason": "onboarding_confirmed"
    }
  ]
}
```

### final_prompt.txt (agent instructions)
```
Agent: Clara

Company: Fire Protection Services

BUSINESS HOURS FLOW

1. Greet the caller warmly.
2. Ask the purpose of the call.
3. Collect caller name and phone number.
4. Route call based on purpose:
   - Emergency → transfer immediately to dispatch line.
   - Non-emergency → collect service request details and confirm next steps.
5. If transfer fails after 60 seconds: apologize and confirm dispatch will follow up.
6. Confirm next steps with the caller.
7. Ask if the caller needs anything else.
8. Close the call professionally.

AFTER HOURS FLOW

1. Greet the caller and identify yourself as Clara.
2. Ask the purpose of the call.
3. Confirm whether this is an emergency.

If emergency:
   - Collect caller name, phone number, and service address.
   - Attempt transfer to dispatch line.
   - If transfer fails after 60 seconds: apologize and confirm that dispatch will follow up shortly.

If non-emergency:
   - Collect service request details.
   - Confirm follow-up next business day.

8. Ask if the caller needs anything else.
9. Close the call.
```

## Repository Structure

```
/workflows                          # Automation workflow exports
/scripts                            # Utility scripts (if any)
/data                               # Input cases (demo.txt + form.json per case)
/configs/<account_id>/              # Immutable versioned config files
    agent_v1.json
    agent_v2.json
/outputs/accounts/<account_id>/     # Account-scoped deployment artifacts
    v1/
      account_memo.json
      agent_spec.json
    v2/
      account_memo.json
      agent_spec.json
      changes.json
/agents/<account_id>/               # Final deployable bundle per account
    v1.json
    v2.json
    changelog.json
    final_prompt.txt
/tests                              # Idempotency + edge case tests
/logs                               # Pipeline logs and batch metrics
    pipeline.log
    metrics.json
/pipeline                           # Core pipeline modules
/README.md
```

Outputs are generated per account with versioned artifacts. Each account gets an immutable v1 (from demo) and a v2 (after onboarding merge), plus a human-readable `final_prompt.txt` ready for Retell deployment.

## Automation Workflow

The repository includes an automation workflow in:

```
/workflows/clara_onboarding_pipeline.json
```

This workflow orchestrates:

1. **File ingestion** — demo transcripts and onboarding forms loaded from `data/`
2. **Transcript normalisation** — raw text cleaned and structured for extraction
3. **Structured data extraction** — key fields parsed (hours, CRM, routing, emergencies)
4. **v1 agent generation** — baseline `AgentConfig` saved with all unknowns flagged
5. **Onboarding update merge** — form data merged conflict-aware into v1
6. **v2 agent generation** — resolved config saved with full changelog
7. **Changelog creation** — field-level diff recorded for every change
8. **Artifact storage** — `v1.json`, `v2.json`, `changelog.json`, `final_prompt.txt` written per account

## Output Structure

For each client, deployment artifacts are written to:

```
agents/
  client_001/
    v1.json
    v2.json
    changelog.json
    final_prompt.txt
```

Additional account-scoped output is preserved in:

```
outputs/accounts/<client_id>/v1/
outputs/accounts/<client_id>/v2/
```

## Explicit Unknowns

Unresolved data is preserved in `questions_or_unknowns` inside `v2.json`.

Example:

```yaml
questions_or_unknowns:
  - dispatch phone number
  - inspection scheduling rules
  - ServiceTrade authentication
```

## Logging

Pipeline logs are written to:

```
logs/pipeline.log
```

Example entries:

```text
[INFO] Demo parser started
[INFO] v1 generated
[INFO] Onboarding merge applied
[INFO] v2 generated
[INFO] Prompt generated
```

## Dataset

The pipeline is designed to process **5 demo calls + 5 onboarding calls** (10 input files total), producing one versioned agent bundle per account. Input cases are organised as:

```
data/
  case_1/          # demo.txt + form.json
  case_2/
  ...
  case_5/
```

To add new accounts, create a new `case_N/` folder with a `demo.txt` transcript and a `form.json` onboarding form.

To test with new data, simply place your demo and onboarding recording files (audio or `.txt` transcripts) into the `data/` directory.

The pipeline automatically handles transcription and processing:
```bash
python pipeline.py run --input data/
```

## Setup Instructions

### Retell Setup

If you wish to deploy the generated agents to Retell:
1. Create a free account at [Retell AI](https://www.retellai.com/).
2. You can manually create an agent in the dashboard.
3. Open the `v2.json` (or `agent_spec.json`) output for a client, copy the generated system prompt from the `prompt` field (or the `final_prompt.txt` file), and paste it into the Retell Agent prompt configuration.
4. Configure the required tools manually within the Retell UI based on the routing rules.

### Workflow Orchestrator (n8n / Make)

A sample workflow is provided in the `/workflows` directory.

**n8n Setup:**
1. Import the `workflows/clara_onboarding_pipeline.json` file into your n8n instance.
2. Ensure you have the necessary credentials set up (if automating APIs).
3. We provide a REST API in `server.py` that can be triggered directly by n8n Webhooks.
4. Run `python server.py` to start the local batch API.

## Known Limitations

- **Audio Transcription Accuracy**: Uses local transcription by default when processing audio files. Accuracy depends deeply on the speech-to-text model loaded. Heavy background noise might cause lost details.
- **LLM Context Limits**: Extremely long calls might push up against token window limitations, though most demo and onboarding calls fit comfortably.
- **Form Merge Conflicts**: The conflict-aware engine favors call transcripts for context but handles overrides via simple overwrite logic. Complex multi-dependency logic isn't fully managed by the diff engine yet.

## Future Improvements (Production Access)

If granted production access and a higher integration budget, we would improve:
1. **Direct API Integration**: Automatically create and version Agents via the Retell API instead of outputting a manual spec.
2. **CRM Integration**: Instead of outputting routing rules as text, automatically configure the dispatch rules directly into ServiceTrade or similar CRMs using OAuth workflows.
3. **Database Storage**: Move from local JSON filesystem storage to a proper Postgres database with JSONB columns for real-time querying and easier state rollbacks.
4. **Webhooks Events**: Push completion events up to Zapier or Make so the team gets a Slack notification when a new `v2` Agent is ready.

## Idempotency Test

Run:

```bash
pytest tests/test_idempotency.py
```

Validated behavior:

✔ Running pipeline twice with the same input produces identical `v2.json` output.
