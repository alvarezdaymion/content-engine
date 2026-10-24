"""Optional: an AI voice reads each card's question (ElevenLabs text-to-speech). Off by default; it is billed per
character, so it only runs with `[tts] enabled = true` and only through `content-engine audio`.

Env:
  ELEVENLABS_API_KEY    API key
  ELEVENLABS_VOICE_ID   voice to use, unless `[tts] voice_id` is set

Call: POST /v1/text-to-speech/<voice>?output_format=mp3_44100_128 with {"text", "model_id"}; the answer is MP3
bytes. Audio is cached by voice, model and exact text, so re-running costs nothing for unchanged cards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from .cards import spoken
from .http import Http, Transport

API = "https://api.elevenlabs.io/v1/text-to-speech/%s"


class ElevenLabs:
    def __init__(self, voice_id: str = "", model: str = "eleven_multilingual_v2", api_key: str | None = None,
                 transport: Transport | None = None):
        self.key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
        if not self.key or not self.voice:
            raise RuntimeError("set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID (or [tts] voice_id)")
        self.model = model
        self.http = Http(transport, timeout=300)

    def tag(self, text: str) -> str:
        return hashlib.sha256(("%s|%s|%s" % (self.voice, self.model, text)).encode()).hexdigest()[:16]

    def speak(self, text: str) -> bytes:
        r = self.http.request("POST", API % self.voice, params={"output_format": "mp3_44100_128"},
                              json_body={"text": text, "model_id": self.model},
                              headers={"xi-api-key": self.key, "Accept": "audio/mpeg"})
        if not r.body:
            raise RuntimeError("ElevenLabs returned no audio")
        return r.body


def make_audio(session: Any, voice: ElevenLabs, cache_dir: Path) -> dict[str, Any]:
    """Write audio/<n>.mp3 for every card. Returns {"made": [...], "cached": [...], "characters": billed}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    session.audio_dir.mkdir(parents=True, exist_ok=True)
    made, cached, chars = [], [], 0
    for card in session.cards:
        text = spoken(card)
        hit = cache_dir / ("%s.mp3" % voice.tag(text))
        if hit.exists():
            cached.append(card["n"])
        else:
            hit.write_bytes(voice.speak(text))
            made.append(card["n"])
            chars += len(text)
        shutil.copyfile(hit, session.audio_dir / ("%d.mp3" % card["n"]))
    return {"made": made, "cached": cached, "characters": chars}
