from __future__ import annotations

from pathlib import Path

from pipeline.generate_v1 import generate_v1
from pipeline.generate_v2 import generate_v2
from pipeline.merge_form import merge_form
from pipeline.utils import versioning
from run_pipeline import extract_demo_baseline


def test_pipeline_idempotency(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(versioning, "_CONFIGS_DIR", tmp_path / "configs")
    monkeypatch.setattr(versioning, "_AGENTS_DIR", tmp_path / "agents")

    client_id = "client_test"
    demo_path = repo_root / "data" / "case_1" / "demo.txt"
    form_path = repo_root / "data" / "case_1" / "form.json"

    demo_transcript = demo_path.read_text(encoding="utf-8")
    form_transcript = form_path.read_text(encoding="utf-8")

    demo_extracted = extract_demo_baseline(client_id=client_id, transcript=demo_transcript)
    generate_v1(demo_extracted, transcript=demo_transcript, force=False)

    form_data = merge_form(form_path, client_id=client_id)
    first_v2 = generate_v2(form_data, transcript=form_transcript, base_version=1, force=False)

    v2_path = versioning.config_path(client_id, 2)
    first_snapshot = v2_path.read_text(encoding="utf-8")

    second_v2 = generate_v2(form_data, transcript=form_transcript, base_version=1, force=False)
    second_snapshot = v2_path.read_text(encoding="utf-8")

    assert first_v2.version == 2
    assert second_v2.version == 2
    assert first_snapshot == second_snapshot
