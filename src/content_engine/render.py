"""Render a cut with ffmpeg, when it is installed. Everything before this step is pure Python.

The take's video and audio are trimmed to the segments and joined. When ffmpeg has the `ass` filter (built with
libass), captions and graphics are burned in from the .ass file; without it the cut is rendered clean and the .ass
file sits next to it (any editor can import it).
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

from .trims import Segment


def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


@functools.cache
def has_filter(name: str) -> bool:
    exe = ffmpeg()
    if not exe:
        return False
    out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, check=False).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def filter_graph(segments: list[Segment], ass_name: str | None) -> str:
    parts, labels = [], []
    for i, s in enumerate(segments):
        parts.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d]" % (s.a, s.b, i))
        parts.append("[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[a%d]" % (s.a, s.b, i))
        labels.append("[v%d][a%d]" % (i, i))
    parts.append("%sconcat=n=%d:v=1:a=1[vcat][aout]" % ("".join(labels), len(segments)))
    parts.append("[vcat]ass=%s[vout]" % ass_name if ass_name else "[vcat]null[vout]")
    return ";".join(parts)


def command(video: Path, segments: list[Segment], out: Path, ass_name: str | None) -> list[str]:
    return [ffmpeg() or "ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(video),
            "-filter_complex", filter_graph(segments, ass_name), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]


def render(video: Path | None, segments: list[Segment], out: Path, ass_path: Path | None) -> str:
    """Render one reel. Returns a short status for the review page; never raises for a missing tool."""
    if video is None or not video.exists():
        return "not rendered: no video file for this take"
    if not ffmpeg():
        return "not rendered: ffmpeg is not installed"
    out.parent.mkdir(parents=True, exist_ok=True)
    burn = ass_path is not None and has_filter("ass")
    cwd = ass_path.parent if burn and ass_path else None  # a bare file name avoids filter-graph escaping
    cmd = command(video.resolve(), segments, out.resolve(), ass_path.name if burn and ass_path else None)
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return "render failed: %s" % (done.stderr.strip().splitlines() or ["ffmpeg error"])[-1][:200]
    return "rendered with captions burned in" if burn else "rendered; captions in the .ass file (ffmpeg has no libass)"
