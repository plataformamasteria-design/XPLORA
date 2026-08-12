#!/usr/bin/env python3
"""Transcreve localmente uma gravação usando faster-whisper.

Uso: python3 transcribe_recording.py recordings/reuniao.wav
Gera .txt, .srt e .json ao lado da gravação, sem enviar áudio a serviços externos.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path


def srt_timestamp(seconds: float) -> str:
    value = timedelta(seconds=max(0, seconds))
    total_ms = int(value.total_seconds() * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds_part, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_part:02},{ms:03}"


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python3 transcribe_recording.py <gravação>", file=sys.stderr)
        return 2
    recording = Path(sys.argv[1]).resolve()
    if not recording.is_file():
        print("Gravação não encontrada.", file=sys.stderr)
        return 2
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Instale faster-whisper: pip install faster-whisper", file=sys.stderr)
        return 2

    # O modelo pode ser alterado por variável de ambiente, sem mexer na interface.
    import os
    model_name = os.environ.get("WHISPER_MODEL", "small")
    device = os.environ.get("WHISPER_DEVICE", "auto")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
    print(f"Carregando Whisper ({model_name}, {device})…", flush=True)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(str(recording), vad_filter=True, beam_size=5)
    items = list(segments)
    stem = recording.with_suffix("")
    text = "\n".join(segment.text.strip() for segment in items if segment.text.strip())
    stem.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    with stem.with_suffix(".srt").open("w", encoding="utf-8") as output:
        for number, segment in enumerate(items, start=1):
            output.write(f"{number}\n{srt_timestamp(segment.start)} --> {srt_timestamp(segment.end)}\n{segment.text.strip()}\n\n")
    stem.with_suffix(".json").write_text(json.dumps({
        "language": info.language,
        "language_probability": info.language_probability,
        "segments": [{"start": segment.start, "end": segment.end, "text": segment.text.strip()} for segment in items],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Concluído: {stem.with_suffix('.txt').name}, {stem.with_suffix('.srt').name} e {stem.with_suffix('.json').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
