"""Takes and their word timestamps.

Put each take in sessions/<id>/takes/: the video (take-01.mp4) and its transcript with word timings (take-01.json).
Accepted transcript shapes:

    [["Do", 0.61, 0.79], ["I", 0.85, 0.93], ...]                         word, start, end (seconds)
    [{"word": "Do", "start": 0.61, "end": 0.79}, ...]                   "text" works in place of "word"
    {"words": [...]} or {"segments": [{"words": [...]}, ...]}           Whisper-style output

Any transcription tool that gives word timestamps works. `content-engine transcribe` writes them with a local
Whisper model if the optional extra is installed (`uv sync --extra whisper`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VIDEO = (".mp4", ".mov", ".m4v", ".mkv")
Word = tuple[str, float, float]


def parse_words(data: Any) -> list[Word]:
    if isinstance(data, dict):
        if "segments" in data:
            data = [w for seg in data["segments"] for w in seg.get("words") or []]
        else:
            data = data.get("words") or []
    out: list[Word] = []
    for w in data:
        if isinstance(w, (list, tuple)) and len(w) >= 3:
            text, start, end = w[0], w[1], w[2]
        elif isinstance(w, dict):
            text, start, end = w.get("word", w.get("text")), w.get("start"), w.get("end")
        else:
            continue
        text = str(text or "").strip()
        if text and start is not None and end is not None:
            out.append((text, float(start), float(end)))
    out.sort(key=lambda x: x[1])
    return out


def load_words(path: Path) -> list[Word]:
    return parse_words(json.loads(path.read_text()))


def find_takes(folder: Path) -> dict[str, dict[str, Path | None]]:
    """take name -> {"transcript": path or None, "video": path or None}, sorted by name."""
    found: dict[str, dict[str, Path | None]] = {}
    if not folder.exists():
        return found
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() == ".json":
            found.setdefault(p.stem, {"transcript": None, "video": None})["transcript"] = p
        elif p.suffix.lower() in VIDEO:
            found.setdefault(p.stem, {"transcript": None, "video": None})["video"] = p
    return dict(sorted(found.items()))


def transcribe_missing(folder: Path, model_size: str = "small") -> list[str]:
    """Transcribe every video without a transcript, with faster-whisper (optional extra). Returns take names."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("install the optional extra first: uv sync --extra whisper") from e
    todo = [(name, t["video"]) for name, t in find_takes(folder).items() if t["video"] and not t["transcript"]]
    if not todo:
        return []
    model = WhisperModel(model_size)
    done = []
    for name, video in todo:
        segments, _ = model.transcribe(str(video), word_timestamps=True)
        words = [[w.word.strip(), round(w.start, 3), round(w.end, 3)] for seg in segments for w in seg.words or []
                 if w.word.strip()]
        (folder / ("%s.json" % name)).write_text(json.dumps(words))
        done.append(name)
    return done
