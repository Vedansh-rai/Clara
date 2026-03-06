"""
transcribe.py — Audio or text input → transcript string.

Supports:
  - openai_whisper  : Uses OpenAI Whisper API (default, cloud)
  - local_whisper   : Uses faster-whisper locally (set TRANSCRIPTION_BACKEND=local_whisper)
  - text_passthrough: Input is already a plain .txt transcript — no transcription needed

Input can be:
  - An audio file path (.mp3, .mp4, .wav, .m4a, .ogg, .webm)
  - A text file path (.txt) — treated as a pre-written transcript

Output:
  - (transcript_text: str, metadata: TranscriptMetadata)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

from pipeline.schema import TranscriptMetadata
from pipeline.utils.logger import get_logger, log_event

load_dotenv()

_BACKEND = os.getenv("TRANSCRIPTION_BACKEND", "openai_whisper").lower()
_AUDIO_EXTENSIONS = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm", ".flac"}
_TEXT_EXTENSIONS = {".txt"}

log = get_logger("transcribe")


def transcribe(file_path: str | Path) -> tuple[str, TranscriptMetadata]:
    """
    Transcribe an audio file or load a text transcript.

    Returns:
        (transcript_text, TranscriptMetadata)
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    ext = path.suffix.lower()

    if ext in _TEXT_EXTENSIONS:
        return _load_text(path)
    elif ext in _AUDIO_EXTENSIONS:
        backend = _BACKEND
        if backend == "openai_whisper":
            return _whisper_api(path)
        elif backend == "local_whisper":
            return _local_whisper(path)
        else:
            raise ValueError(f"Unknown TRANSCRIPTION_BACKEND: {backend!r}")
    else:
        raise ValueError(
            f"Unsupported file extension: {ext!r}. "
            f"Audio: {_AUDIO_EXTENSIONS} | Text: {_TEXT_EXTENSIONS}"
        )


# ─── Backends ─────────────────────────────────────────────────────────────────


def _load_text(path: Path) -> tuple[str, TranscriptMetadata]:
    """Load a pre-existing plain text transcript."""
    text = path.read_text(encoding="utf-8")
    log.info(f"Loaded text transcript from {path.name} ({len(text)} chars)")
    meta = TranscriptMetadata(
        source_file=str(path),
        backend="text_passthrough",
        language="en",
    )
    return text, meta


def _whisper_api(path: Path) -> tuple[str, TranscriptMetadata]:
    """Transcribe using the OpenAI Whisper API."""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    log.info(f"Transcribing {path.name} via OpenAI Whisper API…")
    t0 = time.monotonic()

    with open(path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
        )

    elapsed = time.monotonic() - t0
    transcript = result.text
    duration = getattr(result, "duration", None)

    log_event(log, "whisper_api_done", file=path.name, elapsed_s=round(elapsed, 1))
    log.info(f"Transcription complete: {len(transcript)} chars in {elapsed:.1f}s")

    meta = TranscriptMetadata(
        source_file=str(path),
        duration_seconds=duration,
        language=getattr(result, "language", None),
        backend="openai_whisper",
    )
    return transcript, meta


def _local_whisper(path: Path) -> tuple[str, TranscriptMetadata]:
    """Transcribe using faster-whisper locally."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError(
            "faster-whisper is not installed. "
            "Run: pip install faster-whisper\n"
            "Or switch to TRANSCRIPTION_BACKEND=openai_whisper"
        )

    model_name = os.getenv("LOCAL_WHISPER_MODEL", "base")
    log.info(f"Transcribing {path.name} via local Whisper ({model_name})…")
    t0 = time.monotonic()

    model = WhisperModel(model_name, compute_type="int8")
    segments, info = model.transcribe(str(path), beam_size=5)
    transcript = " ".join(seg.text.strip() for seg in segments)

    elapsed = time.monotonic() - t0
    log.info(f"Transcription complete: {len(transcript)} chars in {elapsed:.1f}s")

    meta = TranscriptMetadata(
        source_file=str(path),
        duration_seconds=info.duration,
        language=info.language,
        backend=f"local_whisper/{model_name}",
    )
    return transcript, meta
