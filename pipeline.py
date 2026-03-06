"""
pipeline.py — Clara Onboarding Automation Pipeline CLI.

Usage:
  python pipeline.py run      --input data/
  python pipeline.py test
  python pipeline.py demo     --client-id BENS_ELECTRIC --input demo_notes.txt
  python pipeline.py onboard  --client-id BENS_ELECTRIC --input onboard_call.mp3
  python pipeline.py form     --client-id BENS_ELECTRIC --form onboarding_form.json
  python pipeline.py batch    --input-dir ./cases/
  python pipeline.py inspect  --client-id BENS_ELECTRIC
  python pipeline.py inspect  --client-id BENS_ELECTRIC --version 1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

app = typer.Typer(
    name="clara-pipeline",
    help="Clara AI Onboarding Automation Pipeline",
    no_args_is_help=True,
)
console = Console()


# ─── run (assignment batch) ───────────────────────────────────────────────────


@app.command()
def run(
    input: Path = typer.Option(..., "--input", "-i", help="Directory containing case folders (demo.txt + form.json per case)"),
) -> None:
    """
    Run the assignment batch pipeline.

    Input: Directory with case_1/, case_2/, ... subdirectories.
    Each case folder must contain: demo.txt, form.json
    
    Output:
    - configs/{client_id}/agent_vN.json (versioned configs)
    - agents/{client_id}/ (deployment bundle: v1.json, v2.json, changelog.json, final_prompt.txt)
    - logs/pipeline.log (structured pipeline logs)
    
    Example:
      python pipeline.py run --input data/
    """
    from run_pipeline import run_batch

    console.rule("[bold magenta]BATCH RUN")
    try:
        run_batch(input)
        console.print("[green]✓ Pipeline completed successfully[/green]")
    except Exception as e:
        console.print(f"[red]✗ Pipeline failed: {e}[/red]")
        raise typer.Exit(1)


# ─── test ─────────────────────────────────────────────────────────────────────


@app.command()
def test() -> None:
    """
    Run all tests (unit and idempotency).
    
    Tests location: tests/
    """
    import subprocess

    console.rule("[bold blue]TESTS")
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-v"],
        cwd=Path.cwd(),
    )
    raise typer.Exit(result.returncode)

# ─── demo ─────────────────────────────────────────────────────────────────────


@app.command()
def demo(
    client_id: str = typer.Option(..., "--client-id", "-c", help="Unique client identifier"),
    input_file: Path = typer.Option(..., "--input", "-i", help="Audio file or .txt transcript"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing v1 if present"),
) -> None:
    """
    Stage 1: Process a demo call to generate AgentConfig v1.

    Input: audio file (.mp3/.mp4/.wav/etc.) or text transcript (.txt)
    Output: configs/{client_id}/agent_v1.json
    """
    console.rule(f"[bold blue]DEMO → v1 | {client_id}")

    from pipeline.extract import extract_from_transcript
    from pipeline.generate_v1 import generate_v1
    from pipeline.schema import DataSource
    from pipeline.transcribe import transcribe
    from pipeline.utils.logger import get_logger

    log = get_logger("pipeline.demo", client_id=client_id)

    # Transcribe / load
    transcript, metadata = transcribe(input_file)
    log.info(f"Input: {input_file.name} ({len(transcript)} chars)")

    # Extract
    extracted = extract_from_transcript(
        transcript=transcript,
        source=DataSource.DEMO,
        client_id=client_id,
        metadata=metadata,
    )

    # Generate v1
    config = generate_v1(extracted, transcript=transcript, force=force)
    _print_config_summary(config)


# ─── onboard ──────────────────────────────────────────────────────────────────


@app.command()
def onboard(
    client_id: str = typer.Option(..., "--client-id", "-c", help="Unique client identifier"),
    input_file: Path = typer.Option(..., "--input", "-i", help="Audio file or .txt transcript"),
    form_file: Optional[Path] = typer.Option(None, "--form", help="Optional onboarding form JSON"),
    base_version: Optional[int] = typer.Option(None, "--base-version", help="Version to update from"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing version if present"),
) -> None:
    """
    Stage 3: Process an onboarding call to generate the next AgentConfig version.

    Input: audio file or text transcript (+ optional form JSON)
    Output: configs/{client_id}/agent_v{N}.json
    """
    console.rule(f"[bold green]ONBOARD → v2+ | {client_id}")

    from pipeline.extract import extract_from_transcript
    from pipeline.generate_v2 import generate_v2
    from pipeline.merge_form import merge_form
    from pipeline.schema import DataSource
    from pipeline.transcribe import transcribe
    from pipeline.utils.logger import get_logger

    log = get_logger("pipeline.onboard", client_id=client_id)

    # Transcribe call
    transcript, metadata = transcribe(input_file)
    log.info(f"Input: {input_file.name} ({len(transcript)} chars)")

    # Extract from call
    extracted = extract_from_transcript(
        transcript=transcript,
        source=DataSource.ONBOARDING,
        client_id=client_id,
        metadata=metadata,
    )

    # Optionally merge form data
    if form_file:
        log.info(f"Merging onboarding form: {form_file.name}")
        form_data = merge_form(form_file, client_id=client_id)
        # Merge: call data takes precedence for non-null fields; form fills gaps
        extracted = _merge_call_and_form(extracted, form_data)

    # Generate next version
    config = generate_v2(
        extracted, transcript=transcript, base_version=base_version, force=force
    )
    _print_config_summary(config)


# ─── form ─────────────────────────────────────────────────────────────────────


@app.command()
def form(
    client_id: str = typer.Option(..., "--client-id", "-c", help="Unique client identifier"),
    form_file: Path = typer.Option(..., "--form", "-f", help="Onboarding form JSON file"),
    base_version: Optional[int] = typer.Option(None, "--base-version", help="Version to update from"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing version if present"),
) -> None:
    """
    Optional Stage: Apply a structured onboarding form to generate the next version.

    Input: JSON onboarding form
    Output: configs/{client_id}/agent_v{N}.json
    """
    console.rule(f"[bold yellow]FORM → v2+ | {client_id}")

    from pipeline.generate_v2 import generate_v2
    from pipeline.merge_form import merge_form
    from pipeline.utils.logger import get_logger

    log = get_logger("pipeline.form", client_id=client_id)

    extracted = merge_form(form_file, client_id=client_id)
    config = generate_v2(extracted, base_version=base_version, force=overwrite)
    _print_config_summary(config)


# ─── batch ────────────────────────────────────────────────────────────────────


@app.command()
def batch(
    input_dir: Path = typer.Option(..., "--input-dir", "-d", help="Directory of case subdirectories"),
    stage: str = typer.Option("demo", "--stage", "-s", help="Stage to run: demo | onboard | form"),
    force: bool = typer.Option(False, "--force", "-f", help="Force overwrite of existing versions"),
) -> None:
    """
    Batch-process a directory of cases.

    Expected directory structure:
      cases/
        bens_electric/
          demo.txt          (or demo.mp3)
          onboard.txt       (or onboard.mp3)
          form.json         (optional)
        next_client/
          ...

    Each subdirectory name becomes the client_id.
    """
    console.rule(f"[bold magenta]BATCH {stage.upper()} | {input_dir}")

    if not input_dir.is_dir():
        console.print(f"[red]Error: {input_dir} is not a directory")
        raise typer.Exit(1)

    results: dict[str, str] = {}
    audio_exts = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm", ".flac"}
    text_exts = {".txt"}
    all_exts = audio_exts | text_exts

    for case_dir in sorted(input_dir.iterdir()):
        if not case_dir.is_dir():
            continue

        client_id = case_dir.name
        console.print(f"\n[bold]Processing: {client_id}")

        try:
            if stage == "demo":
                candidates = [
                    f for f in case_dir.iterdir()
                    if f.stem.lower() in {"demo", "demo_call", "demo_notes"}
                    and f.suffix.lower() in all_exts
                ]
                if not candidates:
                    results[client_id] = "SKIP — no demo file found"
                    continue
                input_file = candidates[0]
                demo(client_id=client_id, input_file=input_file, force=force)

            elif stage == "onboard":
                candidates = [
                    f for f in case_dir.iterdir()
                    if f.stem.lower() in {"onboard", "onboarding", "onboard_call"}
                    and f.suffix.lower() in all_exts
                ]
                if not candidates:
                    results[client_id] = "SKIP — no onboarding file found"
                    continue
                input_file = candidates[0]
                form_f = next((f for f in case_dir.iterdir()
                               if f.stem == "form" and f.suffix == ".json"), None)
                onboard(
                    client_id=client_id,
                    input_file=input_file,
                    form_file=form_f,
                    base_version=None,
                    force=force,
                )

            elif stage == "form":
                form_f = next(
                    (f for f in case_dir.iterdir() if f.stem == "form" and f.suffix == ".json"),
                    None
                )
                if not form_f:
                    results[client_id] = "SKIP — no form.json found"
                    continue
                form(client_id=client_id, form_file=form_f, base_version=None, overwrite=force)

            else:
                console.print(f"[red]Unknown stage: {stage!r}. Use demo | onboard | form")
                raise typer.Exit(1)

            results[client_id] = "OK"

        except Exception as e:
            results[client_id] = f"ERROR — {e}"
            console.print_exception()

    # Summary table
    table = Table(title="Batch Results", show_header=True)
    table.add_column("Client ID", style="cyan")
    table.add_column("Result", style="white")
    for cid, result in results.items():
        colour = "green" if result == "OK" else ("yellow" if result.startswith("SKIP") else "red")
        table.add_row(cid, f"[{colour}]{result}")
    console.print(table)


# ─── inspect ──────────────────────────────────────────────────────────────────


@app.command()
def inspect(
    client_id: str = typer.Option(..., "--client-id", "-c", help="Client identifier"),
    version: Optional[int] = typer.Option(None, "--version", "-v", help="Version to inspect (default: latest)"),
    show_prompt: bool = typer.Option(False, "--prompt", "-p", help="Print the generated agent prompt"),
) -> None:
    """
    Inspect a stored AgentConfig (summary, changelog, open questions).
    """
    from pipeline.utils.versioning import get_latest_version, load_config

    v = version or get_latest_version(client_id)
    if v is None:
        console.print(f"[red]No config found for client '{client_id}'")
        raise typer.Exit(1)

    config = load_config(client_id, v)
    _print_config_summary(config, verbose=True)

    if show_prompt and config.prompt:
        console.print(Panel(config.prompt, title="Generated Agent Prompt", border_style="blue"))


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _print_config_summary(config, verbose: bool = False) -> None:
    """Print a rich summary of an AgentConfig."""
    from pipeline.schema import AgentConfig

    title = f"[bold]AgentConfig v{config.version} — {config.company_name or config.client_id}"
    panel_lines = [
        f"  Client ID   : {config.client_id}",
        f"  Version     : v{config.version}",
        f"  Stage       : {config.source_stage.value}",
        f"  Company     : {config.company_name or '[unknown]'}",
        f"  Industry    : {config.industry or '[unknown]'}",
        f"  CRM         : {config.crm_system or '[unknown]'}",
        f"  Timezone    : {config.timezone or '[unknown]'}",
        f"  Routing rules     : {len(config.routing_rules)}",
        f"  Transfer numbers  : {len(config.transfer_numbers)}",
        f"  Emergency defs    : {len(config.emergency_definitions)}",
        f"  Integration rules : {len(config.integration_rules)}",
        f"  Changelog entries : {len(config.changelog)}",
        f"  Open questions    : {len(config.questions_or_unknowns)}",
    ]
    console.print(Panel("\n".join(panel_lines), title=title, border_style="green"))

    if config.questions_or_unknowns:
        console.print("[yellow]⚠ Open questions / unknowns:")
        for q in config.questions_or_unknowns:
            console.print(f"  [yellow]- {q}")

    if verbose and config.changelog:
        table = Table(title="Change Log", show_header=True)
        table.add_column("Field", style="cyan", max_width=25)
        table.add_column("Old", style="red", max_width=30)
        table.add_column("New", style="green", max_width=30)
        table.add_column("Source", max_width=12)
        table.add_column("Reason", max_width=40)
        for entry in config.changelog:
            table.add_row(
                entry.field,
                str(entry.old_value)[:30] if entry.old_value is not None else "—",
                str(entry.new_value)[:30],
                entry.source.value,
                entry.reason[:40],
            )
        console.print(table)


def _merge_call_and_form(call_data, form_data):
    """
    Merge call extraction with form data.
    Call data takes precedence for non-null fields; form fills gaps.
    """
    from pipeline.schema import DataSource, ExtractedCallData

    def pick(call_val, form_val):
        """Call wins if it has data; otherwise use form."""
        if call_val is not None and call_val != [] and call_val != {}:
            return call_val
        return form_val

    return ExtractedCallData(
        source=DataSource.ONBOARDING,
        client_id=call_data.client_id,
        transcript_metadata=call_data.transcript_metadata,
        company_name=pick(call_data.company_name, form_data.company_name),
        industry=pick(call_data.industry, form_data.industry),
        crm_system=pick(call_data.crm_system, form_data.crm_system),
        service_area=pick(call_data.service_area, form_data.service_area),
        business_hours=pick(call_data.business_hours, form_data.business_hours),
        timezone=pick(call_data.timezone, form_data.timezone),
        emergency_definitions=list({*call_data.emergency_definitions, *form_data.emergency_definitions}),
        routing_rules=call_data.routing_rules or form_data.routing_rules,
        transfer_numbers={**form_data.transfer_numbers, **call_data.transfer_numbers},
        after_hours_handling=pick(call_data.after_hours_handling, form_data.after_hours_handling),
        transfer_timeout_seconds=pick(call_data.transfer_timeout_seconds, form_data.transfer_timeout_seconds),
        fallback_logic=pick(call_data.fallback_logic, form_data.fallback_logic),
        integration_rules=list({*call_data.integration_rules, *form_data.integration_rules}),
        special_constraints=list({*call_data.special_constraints, *form_data.special_constraints}),
        questions_or_unknowns=list({*call_data.questions_or_unknowns, *form_data.questions_or_unknowns}),
        raw_evidence={**form_data.raw_evidence, **call_data.raw_evidence},
    )


if __name__ == "__main__":
    app()
