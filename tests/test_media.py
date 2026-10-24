"""Question audio, transcripts and rendering: the optional tools, each behind a seam that tests can fake."""

import json
import shutil
import subprocess
import sys
import types

import pytest

from content_engine import render
from content_engine.http import HttpError
from content_engine.session import Session
from content_engine.transcript import find_takes, parse_words, transcribe_missing
from content_engine.trims import Segment
from content_engine.tts import ElevenLabs, make_audio

from .conftest import FakeHttp

KEY = "test-key-not-real"


def test_elevenlabs_needs_a_key_and_a_voice(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        ElevenLabs("voice1")


def test_audio_is_made_once_per_exact_text_and_cached(tmp_path):
    fake = FakeHttp({("POST", "/v1/text-to-speech/voice1"): (200, b"ID3-mp3-bytes")})
    voice = ElevenLabs("voice1", api_key=KEY, transport=fake)
    s = Session("s1", "2026-03-09", "t", [{"n": 1, "question": "Why?", "context": "Hypothetical: x."},
                                          {"n": 2, "question": "How?", "context": ""}], dir=tmp_path / "s1")
    first = make_audio(s, voice, tmp_path / "cache")
    assert first["made"] == [1, 2] and first["characters"] == len("Why? Hypothetical: x.") + len("How?")
    call = fake.calls[0]
    assert call["headers"]["xi-api-key"] == KEY and "output_format=mp3_44100_128" in call["url"]
    assert json.loads(call["body"]) == {"text": "Why? Hypothetical: x.", "model_id": "eleven_multilingual_v2"}
    assert (s.audio_dir / "1.mp3").read_bytes() == b"ID3-mp3-bytes"
    second = make_audio(s, voice, tmp_path / "cache")
    assert second["cached"] == [1, 2] and second["characters"] == 0 and len(fake.calls) == 2


def test_tts_errors_do_not_leak_the_key(tmp_path):
    voice = ElevenLabs("voice1", api_key=KEY, transport=FakeHttp({}))
    with pytest.raises(HttpError) as e:
        voice.speak("hello")
    assert KEY not in str(e.value)


def test_tts_reflected_header_credentials_are_redacted():
    fake = FakeHttp({("POST", "/v1/text-to-speech/voice1"): (400, {"detail": "Rejected " + KEY})})
    voice = ElevenLabs("voice1", api_key=KEY, transport=fake)
    with pytest.raises(HttpError) as e:
        voice.speak("Synthetic question")
    assert KEY not in str(e.value) and "[redacted]" in str(e.value)


def test_transcripts_in_three_shapes():
    rows = [["Hi", 0.5, 0.7], ["there", 0.8, 1.0]]
    assert parse_words(rows) == [("Hi", 0.5, 0.7), ("there", 0.8, 1.0)]
    dicts = [{"word": " there", "start": 0.8, "end": 1.0}, {"text": "Hi", "start": 0.5, "end": 0.7}]
    assert parse_words(dicts) == [("Hi", 0.5, 0.7), ("there", 0.8, 1.0)]
    segs = {"segments": [{"words": [{"word": "Hi", "start": 0.5, "end": 0.7}]}, {"words": None}]}
    assert parse_words(segs) == [("Hi", 0.5, 0.7)]
    assert parse_words({"words": [{"word": "", "start": 1, "end": 2}, {"word": "x"}, 7]}) == []


def test_find_takes_pairs_videos_with_transcripts(tmp_path):
    for name in ("take-01.mp4", "take-01.json", "take-02.MOV", "notes.txt"):
        (tmp_path / name).write_text("x")
    found = find_takes(tmp_path)
    assert list(found) == ["take-01", "take-02"]
    assert found["take-01"]["transcript"].name == "take-01.json" and found["take-02"]["transcript"] is None


def test_transcribe_uses_the_optional_whisper_extra(tmp_path, monkeypatch):
    (tmp_path / "take-01.mp4").write_bytes(b"x")
    (tmp_path / "take-02.mp4").write_bytes(b"x")
    (tmp_path / "take-02.json").write_text("[]")
    W = types.SimpleNamespace

    class Model:
        def __init__(self, size):
            self.size = size

        def transcribe(self, path, word_timestamps):
            assert word_timestamps and path.endswith("take-01.mp4")
            return [W(words=[W(word=" Hello", start=0.5, end=0.9), W(word=" ", start=1, end=1)])], None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    assert transcribe_missing(tmp_path, "tiny") == ["take-01"]
    assert json.loads((tmp_path / "take-01.json").read_text()) == [["Hello", 0.5, 0.9]]


def test_transcribe_without_the_extra_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(RuntimeError, match="uv sync --extra whisper"):
        transcribe_missing(tmp_path)


SEGS = [Segment(0.5, 1.5, "question"), Segment(2.0, 3.0, "answer")]


def test_filter_graph_trims_joins_and_burns_captions_when_asked():
    g = render.filter_graph(SEGS, "1.ass")
    assert "[0:v]trim=start=0.500:end=1.500,setpts=PTS-STARTPTS[v0]" in g
    assert "[0:a]atrim=start=2.000:end=3.000,asetpts=PTS-STARTPTS[a1]" in g
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vcat][aout]" in g and g.endswith("[vcat]ass=1.ass[vout]")
    assert render.filter_graph(SEGS, None).endswith("[vcat]null[vout]")


def test_render_reports_missing_inputs_instead_of_failing(tmp_path, monkeypatch):
    assert "no video file" in render.render(None, SEGS, tmp_path / "o.mp4", None)
    (tmp_path / "t.mp4").write_bytes(b"x")
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    assert "ffmpeg is not installed" in render.render(tmp_path / "t.mp4", SEGS, tmp_path / "o.mp4", None)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")
def test_render_cuts_a_real_video(tmp_path):
    src = tmp_path / "take.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    out = tmp_path / "out" / "1.mp4"
    status = render.render(src, SEGS, out, None)
    assert status.startswith("rendered")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True, check=True).stdout)
    assert dur == pytest.approx(2.0, abs=0.15)
