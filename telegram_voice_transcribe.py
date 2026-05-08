#! python3
"""CLI for checking or running Telegram voice transcription."""

from __future__ import annotations

import argparse
from pathlib import Path

from telegram_voice_transcription import (
    format_voice_transcription_status,
    load_dotenv,
    transcribe_voice_file,
)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Transcribe a downloaded Telegram voice file.")
    parser.add_argument("audio_path", nargs="?", help="Path to a downloaded .oga/.ogg voice file.")
    parser.add_argument("--status", action="store_true", help="Show transcription backend status.")
    args = parser.parse_args()

    if args.status or not args.audio_path:
        print(format_voice_transcription_status())
        return 0

    transcript = transcribe_voice_file(Path(args.audio_path))
    if not transcript:
        raise RuntimeError("No transcription backend configured or transcript was empty.")
    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
