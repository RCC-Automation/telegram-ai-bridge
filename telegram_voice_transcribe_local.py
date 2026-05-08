#! python3
"""Local no-API-cost voice transcription wrapper.

Uses faster-whisper if available, otherwise openai-whisper if available.
Both run locally; the only cost is local CPU/GPU time and model storage.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENDOR_PY = ROOT / ".vendor_py"
MODEL_DIR = ROOT.parent / "telegram-messages" / "models" / "faster-whisper"
if VENDOR_PY.exists():
    sys.path.insert(0, str(VENDOR_PY))


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: py -3 telegram_voice_transcribe_local.py <audio-path>", file=sys.stderr)
        return 2
    audio_path = Path(sys.argv[1])
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        return 2

    model_name = os.getenv("TELEGRAM_LOCAL_WHISPER_MODEL", "base")
    language = os.getenv("TELEGRAM_LOCAL_WHISPER_LANGUAGE", "").strip() or None

    try:
        transcript = transcribe_with_faster_whisper(audio_path, model_name, language)
        if transcript is None:
            transcript = transcribe_with_openai_whisper(audio_path, model_name, language)
    except Exception as exc:
        print(
            "Local Whisper backend is installed, but transcription failed. "
            "Most likely the model files are not downloaded locally yet. "
            "Run install_local_voice_transcription.ps1 once with network access, "
            "or set TELEGRAM_LOCAL_WHISPER_MODEL to a local faster-whisper model directory.\n"
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 4
    if transcript is None:
        print(
            "No local Whisper backend is installed. Install one free local backend, for example:\n"
            "  py -3 -m pip install faster-whisper\n"
            "or:\n"
            "  py -3 -m pip install openai-whisper\n"
            "openai-whisper also requires ffmpeg available on PATH.",
            file=sys.stderr,
        )
        return 3

    print(transcript.strip())
    return 0


def transcribe_with_faster_whisper(audio_path: Path, model_name: str, language: str | None) -> str | None:
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return None

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
    segments, _info = model.transcribe(str(audio_path), language=language)
    return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe_with_openai_whisper(audio_path: Path, model_name: str, language: str | None) -> str | None:
    try:
        import whisper
    except Exception:
        return None

    model = whisper.load_model(model_name)
    kwargs = {"language": language} if language else {}
    result = model.transcribe(str(audio_path), **kwargs)
    return str(result.get("text") or "").strip()


if __name__ == "__main__":
    raise SystemExit(main())
