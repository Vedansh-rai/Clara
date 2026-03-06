"""
server.py — Clara Onboarding Pipeline REST API
Built with FastAPI + Uvicorn.

Endpoints:
  POST /demo/{client_id}            — Process demo call (audio or .txt)
  POST /onboard/{client_id}         — Process onboarding call (audio or .txt)
  POST /form/{client_id}            — Apply onboarding form (JSON)
  GET  /inspect/{client_id}         — Get latest AgentConfig summary
  GET  /inspect/{client_id}/{ver}   — Get specific version
  GET  /prompt/{client_id}          — Get generated agent prompt text
  GET  /clients                     — List all client IDs with versions
  GET  /health                      — Health check
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
import pathlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

# Ensure pipeline is importable
sys.path.insert(0, str(pathlib.Path(__file__).parent))

os.environ.setdefault("CONFIGS_DIR", "configs")
os.environ.setdefault("LOGS_DIR", "logs")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse
from fastapi.exception_handlers import http_exception_handler

app = FastAPI(
    title="Clara Onboarding Pipeline API",
    description=(
        "Converts messy real-world call transcripts and onboarding data "
        "into versioned, production-ready Clara voice agent configurations."
    ),
    version="1.0.0",
)

# ─── Health ───────────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


# ─── Clients ──────────────────────────────────────────────────────────────────


@app.get("/clients", tags=["Clients"])
def list_clients():
    """List all client IDs and their highest version numbers."""
    configs_dir = pathlib.Path(os.environ["CONFIGS_DIR"])
    if not configs_dir.exists():
        return {"clients": []}

    clients = []
    for d in sorted(configs_dir.iterdir()):
        if not d.is_dir():
            continue
        versions = [
            int(f.stem.replace("agent_v", ""))
            for f in d.glob("agent_v*.json")
            if f.stem.replace("agent_v", "").isdigit()
        ]
        if versions:
            clients.append({"client_id": d.name, "latest_version": max(versions), "versions": sorted(versions)})

    return {"clients": clients}


# ─── Demo ─────────────────────────────────────────────────────────────────────


@app.post("/demo/{client_id}", tags=["Pipeline"])
async def run_demo(
    client_id: str,
    file: UploadFile = File(..., description="Audio file (.mp3/.mp4/.wav/etc.) or text transcript (.txt)"),
    force: bool = Query(False, description="Overwrite existing v1 if present"),
):
    """
    Stage 1 — Process a demo call to generate AgentConfig v1.

    Upload an audio file or .txt transcript. The pipeline will:
    1. Transcribe audio → text (if audio)
    2. Extract structured data via LLM
    3. Generate agent_v1.json
    """
    from pipeline.extract import extract_from_transcript
    from pipeline.generate_v1 import generate_v1
    from pipeline.schema import DataSource
    from pipeline.transcribe import transcribe

    with tempfile.NamedTemporaryFile(
        suffix=pathlib.Path(file.filename or "upload.txt").suffix,
        delete=False,
    ) as tmp:
        tmp.write(await file.read())
        tmp_path = pathlib.Path(tmp.name)

    try:
        transcript, metadata = transcribe(tmp_path)
        extracted = extract_from_transcript(
            transcript=transcript,
            source=DataSource.DEMO,
            client_id=client_id,
            metadata=metadata,
        )
        config = generate_v1(extracted, transcript=transcript, force=force)
        return _config_response(config, message="v1 generated successfully")
    except Exception as exc:
        _raise_for_llm_error(exc)
    finally:
        tmp_path.unlink(missing_ok=True)


# ─── Onboard ──────────────────────────────────────────────────────────────────


@app.post("/onboard/{client_id}", tags=["Pipeline"])
async def run_onboard(
    client_id: str,
    file: UploadFile = File(..., description="Audio file or .txt transcript of onboarding call"),
    form_json: Optional[str] = Form(None, description="Optional onboarding form as JSON string"),
    base_version: Optional[int] = Query(None, description="Version to update from (default: latest)"),
    force: bool = Query(False, description="Overwrite existing version if present"),
):
    """
    Stage 3 — Process an onboarding call to generate the next AgentConfig version.

    Optionally include a form_json string to merge structured form data alongside
    the call transcript. Call data takes precedence; form fills gaps.
    """
    from pipeline.extract import extract_from_transcript
    from pipeline.generate_v2 import generate_v2
    from pipeline.schema import DataSource
    from pipeline.transcribe import transcribe

    with tempfile.NamedTemporaryFile(
        suffix=pathlib.Path(file.filename or "upload.txt").suffix,
        delete=False,
    ) as tmp:
        tmp.write(await file.read())
        tmp_path = pathlib.Path(tmp.name)

    try:
        transcript, metadata = transcribe(tmp_path)
        extracted = extract_from_transcript(
            transcript=transcript,
            source=DataSource.ONBOARDING,
            client_id=client_id,
            metadata=metadata,
        )

        if form_json:
            from pipeline.merge_form import merge_form
            from pipeline.pipeline import _merge_call_and_form
            form_data = merge_form(form_json, client_id=client_id)
            extracted = _merge_call_and_form(extracted, form_data)

        config = generate_v2(extracted, transcript=transcript, base_version=base_version, force=force)
        return _config_response(config, message=f"v{config.version} generated successfully")
    except Exception as exc:
        _raise_for_llm_error(exc)
    finally:
        tmp_path.unlink(missing_ok=True)


# ─── Form ─────────────────────────────────────────────────────────────────────


@app.post("/form/{client_id}", tags=["Pipeline"])
async def run_form(
    client_id: str,
    file: Optional[UploadFile] = File(None, description="JSON form file (optional if using json_body)"),
    json_body: Optional[str] = Form(None, description="Form data as JSON string"),
    base_version: Optional[int] = Query(None, description="Version to update from (default: latest)"),
    overwrite: bool = Query(False, description="Overwrite existing version if present"),
):
    """
    Optional Stage — Apply a structured onboarding form JSON to produce the next version.

    Accepts either a file upload or a JSON string in the request body.
    """
    from pipeline.generate_v2 import generate_v2
    from pipeline.merge_form import merge_form

    if file:
        raw = (await file.read()).decode("utf-8")
    elif json_body:
        raw = json_body
    else:
        raise HTTPException(status_code=422, detail="Provide either a JSON file upload or json_body.")

    extracted = merge_form(raw, client_id=client_id)
    config = generate_v2(extracted, base_version=base_version, force=overwrite)
    return _config_response(config, message=f"v{config.version} generated from form")


# ─── Inspect ──────────────────────────────────────────────────────────────────


@app.get("/inspect/{client_id}", tags=["Inspect"])
def inspect_latest(client_id: str):
    """Get the latest AgentConfig for a client (full JSON)."""
    return _load_or_404(client_id)


@app.get("/inspect/{client_id}/{version}", tags=["Inspect"])
def inspect_version(client_id: str, version: int):
    """Get a specific version of an AgentConfig."""
    return _load_or_404(client_id, version)


@app.get("/prompt/{client_id}", tags=["Inspect"], response_class=PlainTextResponse)
def get_prompt(
    client_id: str,
    version: Optional[int] = Query(None, description="Version (default: latest)"),
):
    """Return the generated Retell agent prompt as plain text."""
    config = _load_or_404(client_id, version)
    if not config.get("prompt"):
        raise HTTPException(status_code=404, detail="No prompt generated yet for this config.")
    return config["prompt"]


@app.get("/changelog/{client_id}", tags=["Inspect"])
def get_changelog(
    client_id: str,
    version: Optional[int] = Query(None, description="Version to inspect (default: latest)"),
):
    """Return just the changelog and open questions for a config version."""
    config = _load_or_404(client_id, version)
    return {
        "client_id": client_id,
        "version": config["version"],
        "changelog": config.get("changelog", []),
        "questions_or_unknowns": config.get("questions_or_unknowns", []),
    }


# ─── Metrics ─────────────────────────────────────────────────────────────────


@app.get("/metrics", tags=["Analytics"])
def get_metrics():
    """Return the latest batch run metrics from logs/metrics.json."""
    import json
    from pipeline.utils.versioning import _CONFIGS_DIR

    metrics_file = pathlib.Path("logs/metrics.json")
    if not metrics_file.exists():
        raise HTTPException(
            status_code=404,
            detail="No metrics found. Run 'python pipeline.py run --input data/' first.",
        )
    return json.loads(metrics_file.read_text(encoding="utf-8"))


# ─── Diff ─────────────────────────────────────────────────────────────────────


@app.get("/diff/{client_id}", tags=["Analytics"])
def get_diff(client_id: str):
    """
    Return a structured diff between v1 and v2 for a client.

    Highlights added, changed, and unchanged fields, plus changelog and unknowns.
    """
    from pipeline.utils.versioning import get_latest_version, load_config

    latest = get_latest_version(client_id)
    if latest is None or latest < 1:
        raise HTTPException(status_code=404, detail=f"No config found for '{client_id}'.")

    v1 = load_config(client_id, 1).model_dump(mode="json")
    v2 = load_config(client_id, latest).model_dump(mode="json")

    SKIP = {"version", "updated_at", "created_at", "source_hash", "changelog", "prompt", "source_stage"}
    diff_fields = {}
    for key in set(v1) | set(v2):
        if key in SKIP:
            continue
        val1 = v1.get(key)
        val2 = v2.get(key)
        if val1 == val2:
            status = "unchanged"
        elif val1 is None and val2 is not None:
            status = "added"
        elif val1 is not None and val2 is None:
            status = "removed"
        else:
            status = "changed"
        diff_fields[key] = {"v1": val1, "v2": val2, "status": status}

    resolved = sum(
        1
        for e in v2.get("changelog", [])
        if e.get("field") == "questions_or_unknowns" and e.get("new_value") == "RESOLVED"
    )

    return {
        "client_id": client_id,
        "v1_version": 1,
        "v2_version": latest,
        "fields": diff_fields,
        "changelog": v2.get("changelog", []),
        "questions_or_unknowns": v2.get("questions_or_unknowns", []),
        "resolved_count": resolved,
        "changed_count": sum(1 for f in diff_fields.values() if f["status"] == "changed"),
        "added_count": sum(1 for f in diff_fields.values() if f["status"] == "added"),
    }


# ─── Dashboard ────────────────────────────────────────────────────────────────


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Clara Onboarding Dashboard</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" />
<style>
  body { background:#f8f9fa; }
  .card-metric { border-left:4px solid #0d6efd; }
  .badge-success { background:#198754; }
  .badge-warning { background:#ffc107; color:#000; }
  .badge-danger { background:#dc3545; }
  .diff-added { background:#d1fae5; }
  .diff-changed { background:#fef9c3; }
  .diff-removed { background:#fee2e2; }
  .diff-unchanged { color:#6c757d; }
  pre { white-space: pre-wrap; word-break: break-word; font-size:.8rem; }
  #clientTable tbody tr { cursor:pointer; }
  #clientTable tbody tr:hover { background:#e9ecef; }
</style>
</head>
<body>
<div class="container-fluid py-4">
  <h2 class="mb-1">Clara Onboarding Dashboard</h2>
  <p class="text-muted mb-4">Real-time view of the batch pipeline</p>

  <!-- Summary Cards -->
  <div class="row g-3 mb-4" id="summaryCards">
    <div class="col-6 col-md-3">
      <div class="card card-metric h-100">
        <div class="card-body">
          <div class="small text-muted">Total Clients</div>
          <div class="fs-2 fw-bold" id="metricTotal">—</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card card-metric h-100" style="border-color:#198754">
        <div class="card-body">
          <div class="small text-muted">Succeeded</div>
          <div class="fs-2 fw-bold text-success" id="metricSucceeded">—</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card card-metric h-100" style="border-color:#6f42c1">
        <div class="card-body">
          <div class="small text-muted">Unknowns Resolved</div>
          <div class="fs-2 fw-bold text-purple" id="metricResolved">—</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card card-metric h-100" style="border-color:#fd7e14">
        <div class="card-body">
          <div class="small text-muted">Prompts Generated</div>
          <div class="fs-2 fw-bold text-warning" id="metricPrompts">—</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Client Table -->
  <div class="card mb-4">
    <div class="card-header fw-semibold">Clients</div>
    <div class="card-body p-0">
      <table class="table table-hover mb-0" id="clientTable">
        <thead class="table-light">
          <tr>
            <th>Client ID</th>
            <th>Company</th>
            <th>CRM</th>
            <th>Timezone</th>
            <th>Routing Rules</th>
            <th>Unknowns Left</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="clientTableBody"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- Modal -->
<div class="modal fade" id="diffModal" tabindex="-1">
  <div class="modal-dialog modal-xl modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="modalTitle">Client Diff</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <ul class="nav nav-tabs mb-3" id="diffTabs">
          <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#tabDiff">Field Diff</a></li>
          <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabPrompt">Final Prompt</a></li>
          <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabUnknowns">Open Questions</a></li>
          <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabChangelog">Changelog</a></li>
        </ul>
        <div class="tab-content">
          <div class="tab-pane active" id="tabDiff">
            <table class="table table-sm table-bordered" id="diffTable">
              <thead><tr><th>Field</th><th>v1</th><th>v2</th><th>Status</th></tr></thead>
              <tbody id="diffTableBody"></tbody>
            </table>
          </div>
          <div class="tab-pane" id="tabPrompt"><pre id="promptPreview"></pre></div>
          <div class="tab-pane" id="tabUnknowns"><ul id="unknownsList" class="list-group list-group-flush"></ul></div>
          <div class="tab-pane" id="tabChangelog">
            <table class="table table-sm" id="changelogTable">
              <thead><tr><th>Field</th><th>Old</th><th>New</th><th>Reason</th></tr></thead>
              <tbody id="changelogTableBody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
const fmt = v => (v === null || v === undefined) ? '<span class="text-muted">—</span>' : (typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v));
const statusBadge = s => s === 'success'
  ? '<span class="badge badge-success text-white">SUCCESS</span>'
  : '<span class="badge badge-danger text-white">FAILED</span>';

async function loadDashboard() {
  const [metricsRes, clientsRes] = await Promise.all([fetch('/metrics'), fetch('/clients')]);

  if (metricsRes.ok) {
    const m = await metricsRes.json();
    document.getElementById('metricTotal').textContent = m.total_cases ?? '—';
    document.getElementById('metricSucceeded').textContent = m.successful ?? '—';
    document.getElementById('metricResolved').textContent = m.totals?.total_resolved_unknowns ?? '—';
    document.getElementById('metricPrompts').textContent = m.totals?.prompts_generated ?? '—';

    const tbody = document.getElementById('clientTableBody');
    tbody.innerHTML = '';
    for (const client of (m.clients || [])) {
      const tr = document.createElement('tr');
      tr.dataset.clientId = client.client_id;
      tr.innerHTML = `
        <td><code>${client.client_id}</code></td>
        <td>${client.company_name || '—'}</td>
        <td>${client.crm_system || '—'}</td>
        <td>${client.timezone || '—'}</td>
        <td>${client.routing_rules ?? '—'}</td>
        <td>${client.remaining_unknowns ?? '—'}</td>
        <td>${statusBadge(client.status)}</td>
      `;
      tr.addEventListener('click', () => openDiff(client.client_id, client.company_name));
      tbody.appendChild(tr);
    }
  } else {
    document.getElementById('metricTotal').textContent = '—';
    document.getElementById('clientTableBody').innerHTML =
      '<tr><td colspan="7" class="text-muted text-center">No metrics yet. Run the pipeline first.</td></tr>';
  }
}

async function openDiff(clientId, companyName) {
  document.getElementById('modalTitle').textContent = `${companyName || clientId} · Diff`;
  const modal = new bootstrap.Modal(document.getElementById('diffModal'));
  modal.show();

  const [diffRes, promptRes] = await Promise.all([
    fetch(`/diff/${clientId}`),
    fetch(`/prompt/${clientId}`),
  ]);

  // Diff table
  const tbody = document.getElementById('diffTableBody');
  tbody.innerHTML = '';
  if (diffRes.ok) {
    const d = await diffRes.json();
    for (const [field, info] of Object.entries(d.fields || {})) {
      const cls = info.status === 'added' ? 'diff-added'
                : info.status === 'changed' ? 'diff-changed'
                : info.status === 'removed' ? 'diff-removed'
                : 'diff-unchanged';
      const tr = document.createElement('tr');
      tr.className = cls;
      tr.innerHTML = `<td><code>${field}</code></td><td><pre>${fmt(info.v1)}</pre></td><td><pre>${fmt(info.v2)}</pre></td><td>${info.status}</td>`;
      tbody.appendChild(tr);
    }

    // Unknowns
    const ul = document.getElementById('unknownsList');
    ul.innerHTML = '';
    for (const q of (d.questions_or_unknowns || [])) {
      ul.innerHTML += `<li class="list-group-item">${q}</li>`;
    }
    if (!d.questions_or_unknowns?.length) ul.innerHTML = '<li class="list-group-item text-muted">No open questions.</li>';

    // Changelog
    const cl = document.getElementById('changelogTableBody');
    cl.innerHTML = '';
    for (const e of (d.changelog || [])) {
      cl.innerHTML += `<tr><td><code>${e.field}</code></td><td><pre>${fmt(e.old_value)}</pre></td><td><pre>${fmt(e.new_value)}</pre></td><td>${e.reason || ''}</td></tr>`;
    }
  }

  // Prompt
  const promptEl = document.getElementById('promptPreview');
  if (promptRes.ok) {
    promptEl.textContent = await promptRes.text();
  } else {
    promptEl.textContent = 'No prompt available.';
  }
}

loadDashboard();
</script>
</body>
</html>"""


@app.get("/dashboard", tags=["Analytics"], response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    """Web UI dashboard — client table, diff viewer, metrics summary."""
    return HTMLResponse(content=_DASHBOARD_HTML)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _raise_for_llm_error(exc: Exception) -> None:
    """Re-raise LLM quota/rate errors as clean HTTP responses."""
    msg = str(exc)
    if "quota" in msg.lower() or "rate" in msg.lower() or "429" in msg:
        raise HTTPException(
            status_code=429,
            detail=f"LLM rate limit hit — please retry in a moment. ({msg[:200]})",
        )
    raise HTTPException(status_code=500, detail=msg[:400])


def _load_or_404(client_id: str, version: Optional[int] = None) -> dict:
    """Load an AgentConfig or raise 404."""
    from pipeline.utils.versioning import get_latest_version, load_config

    v = version or get_latest_version(client_id)
    if v is None:
        raise HTTPException(
            status_code=404,
            detail=f"No config found for client '{client_id}'. Run /demo first.",
        )
    try:
        config = load_config(client_id, v)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Version {v} not found for client '{client_id}'.",
        )
    return config.model_dump(mode="json")


def _config_response(config, message: str) -> Dict[str, Any]:
    """Serialise an AgentConfig into a clean API response."""
    return {
        "message": message,
        "client_id": config.client_id,
        "version": config.version,
        "company_name": config.company_name,
        "source_stage": config.source_stage.value,
        "changelog_entries": len(config.changelog),
        "open_questions": len(config.questions_or_unknowns),
        "questions_or_unknowns": config.questions_or_unknowns,
        "prompt_chars": len(config.prompt or ""),
        "updated_at": config.updated_at.isoformat(),
    }


# ─── Entry point ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(pathlib.Path(__file__).parent / "pipeline")],
    )
