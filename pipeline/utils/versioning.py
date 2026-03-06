"""
versioning.py — Helpers for reading/writing immutable AgentConfig versions.

Rules:
- Each version is stored as configs/{client_id}/agent_v{N}.json
- Once written, a version file is NEVER overwritten (immutable)
- The latest version can always be retrieved with get_latest_config()
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from pipeline.prompt_builder import build_final_prompt_from_v2
from pipeline.schema import AgentConfig

_CONFIGS_DIR = Path(os.getenv("CONFIGS_DIR", "configs"))
_AGENTS_DIR = Path(os.getenv("AGENTS_DIR", "agents"))


def _client_dir(client_id: str) -> Path:
    d = _CONFIGS_DIR / client_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path(client_id: str, version: int) -> Path:
    return _client_dir(client_id) / f"agent_v{version}.json"


def save_config(config: AgentConfig, overwrite: bool = False) -> Path:
    """
    Persist an AgentConfig to disk.
    Raises FileExistsError if the version already exists and overwrite=False.
    """
    path = config_path(config.client_id, config.version)
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"Version {config.version} already exists at {path}. "
            "Versions are immutable. Increment the version instead."
        )
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    _save_agent_bundle(config)
    
    # NEW REQUIREMENT: Output explicit folders per account per version
    # Structure: outputs/accounts/<account_id>/vX/
    output_dir = Path("outputs") / "accounts" / config.client_id / f"v{config.version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1) Account Memo JSON
    memo_dict = {
        "account_id": config.client_id,
        "company_name": config.company_name,
        "business_hours": config.business_hours,
        "office_address": config.office_address,
        "services_supported": config.services_supported,
        "emergency_definition": config.emergency_definitions,
        "emergency_routing_rules": config.emergency_routing_rules,
        "non_emergency_routing_rules": config.non_emergency_routing_rules,
        "call_transfer_rules": config.call_transfer_rules,
        "integration_constraints": config.integration_constraints,
        "after_hours_flow_summary": config.after_hours_flow_summary,
        "office_hours_flow_summary": config.office_hours_flow_summary,
        "questions_or_unknowns": config.questions_or_unknowns,
        "notes": config.notes
    }
    
    # 2) Retell Agent Draft Spec
    agent_spec = {
        "agent_name": "Clara",
        "voice_style": "en-US-Standard-F",
        "system_prompt": config.prompt,
        "key_variables": {
            "timezone": config.timezone,
            "business_hours": config.business_hours,
            "address": config.office_address,
            "emergency_routing": config.emergency_routing_rules
        },
        "tool_invocation_placeholders": {
            "transfer_call": "Transfer the caller to an intended human agent",
            "end_call": "Hang up the call when conversation is finished"
        },
        "call_transfer_protocol": config.call_transfer_rules,
        "fallback_protocol": config.fallback_logic,
        "version": f"v{config.version}"
    }

    # Dump the files
    (output_dir / "account_memo.json").write_text(json.dumps(memo_dict, indent=2, default=str), encoding="utf-8")
    (output_dir / "agent_spec.json").write_text(json.dumps(agent_spec, indent=2, default=str), encoding="utf-8")
    
    # 3) Changelog (if v2+)
    if config.version > 1 and config.changelog:
        cl_dict = [c.model_dump() for c in config.changelog]
        (output_dir / "changes.json").write_text(json.dumps(cl_dict, indent=2, default=str), encoding="utf-8")
        
    return path


def _save_agent_bundle(config: AgentConfig) -> None:
    """Write deployment-ready artifacts for each client under agents/<client_id>."""
    client_dir = _AGENTS_DIR / config.client_id
    client_dir.mkdir(parents=True, exist_ok=True)

    version_file = client_dir / f"v{config.version}.json"
    version_file.write_text(config.model_dump_json(indent=2), encoding="utf-8")

    if config.version >= 2:
        changelog_payload = [entry.model_dump(mode="json") for entry in config.changelog]
        (client_dir / "changelog.json").write_text(
            json.dumps(changelog_payload, indent=2),
            encoding="utf-8",
        )

        persisted_v2 = AgentConfig.model_validate_json(version_file.read_text(encoding="utf-8"))
        final_prompt = build_final_prompt_from_v2(persisted_v2)
        (client_dir / "final_prompt.txt").write_text(final_prompt, encoding="utf-8")


def load_config(client_id: str, version: int) -> AgentConfig:
    """Load a specific version of an AgentConfig."""
    path = config_path(client_id, version)
    if not path.exists():
        raise FileNotFoundError(f"No config found at {path}")
    return AgentConfig.model_validate_json(path.read_text(encoding="utf-8"))


def get_latest_version(client_id: str) -> Optional[int]:
    """Return the highest existing version number, or None if no configs exist."""
    d = _client_dir(client_id)
    versions = [
        int(f.stem.replace("agent_v", ""))
        for f in d.glob("agent_v*.json")
        if f.stem.replace("agent_v", "").isdigit()
    ]
    return max(versions) if versions else None


def get_latest_config(client_id: str) -> Optional[AgentConfig]:
    """Return the latest AgentConfig, or None if none exists."""
    v = get_latest_version(client_id)
    return load_config(client_id, v) if v is not None else None


def compute_hash(text: str) -> str:
    """SHA-256 digest of a string — used for idempotency checks."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_transcript(client_id: str, stage: str, transcript: str) -> Path:
    """Save a raw transcript text file alongside configs."""
    path = _client_dir(client_id) / f"transcript_{stage}.txt"
    path.write_text(transcript, encoding="utf-8")
    return path


def load_transcript(client_id: str, stage: str) -> Optional[str]:
    """Load a previously saved transcript, or None."""
    path = _client_dir(client_id) / f"transcript_{stage}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None
