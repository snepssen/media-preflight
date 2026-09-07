"""What the file says it is: container, streams, tags, cover art.

Everything here comes from ffprobe and is *declared* rather than measured. The
distinction matters more than it sounds: a header claiming 48 kHz stereo is not
evidence that both channels carry sound, and a container's duration is not the
audio's duration. Measurement lives in analysis.py; this module only reads what
the file asserts about itself, and normalises the assertions into one shape.
"""

from __future__ import annotations

import json
import os
import subprocess
from fractions import Fraction

import platform_support


class ProbeError(RuntimeError):
    """ffprobe could not read the file, with ffprobe's own reason attached."""


def _run(ffprobe, args, timeout=120):
    try:
        result = subprocess.run([ffprobe, "-v", "error"] + args,
                                capture_output=True, text=True,
                                timeout=timeout, **platform_support.no_console())
    except subprocess.TimeoutExpired:
        raise ProbeError("ffprobe timed out reading the file.")
    except OSError as exc:
        raise ProbeError(f"ffprobe could not be run: {exc}")
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise ProbeError(detail[-1] if detail else "ffprobe failed.")
    return result.stdout


def _number(value, default=None):
    """ffprobe reports numbers as strings, and 'N/A' when it does not know."""
    if value is None or value in ("N/A", ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rate(value):
    """Turn ffprobe's '30000/1001' into 29.97, and '0/0' into None."""
    if not value or value in ("N/A", "0/0"):
        return None
    try:
        fraction = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    if fraction == 0:
        return None
    return float(fraction)


def inspect(path, ffprobe=None):
    """Return the normalised declaration of one media file."""
    if ffprobe is None:
        _, ffprobe = platform_support.require_tools()
    if not os.path.isfile(path):
        raise ProbeError(f"No such file: {path}")

    raw = json.loads(_run(ffprobe, [
        "-print_format", "json", "-show_format", "-show_streams",
        "-show_chapters", path]))
    return normalise(raw, path)


def normalise(raw, path):
    """Shape ffprobe's JSON into the facts the checks are written against.

    Split out from ``inspect`` so the tests can exercise it against recorded
    ffprobe output without ffprobe being installed.
    """
    fmt = raw.get("format", {}) or {}
    streams = [_stream(s) for s in raw.get("streams", []) or []]

    audio = [s for s in streams if s["type"] == "audio"]
    video = [s for s in streams if s["type"] == "video"]
    # A still image carried inside an audio file is cover art, not a video
    # track, and calling it one turns every podcast episode into a video that
    # fails every video rule.
    cover = [s for s in video if s["attached_pic"]]
    moving = [s for s in video if not s["attached_pic"]]

    facts = {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "size_bytes": int(_number(fmt.get("size"), os.path.getsize(path)
                                  if os.path.isfile(path) else 0) or 0),
        "container": {
            "format_name": fmt.get("format_name", ""),
            "format_long_name": fmt.get("format_long_name", ""),
            "duration_s": _number(fmt.get("duration")),
            "bit_rate": _number(fmt.get("bit_rate")),
            "tags": _lower_tags(fmt.get("tags")),
        },
        "chapters": len(raw.get("chapters", []) or []),
        "streams": streams,
        "audio": audio[0] if audio else None,
        "audio_streams": audio,
        "video": moving[0] if moving else None,
        "video_streams": moving,
        "cover_art": bool(cover),
        "subtitle_streams": [s for s in streams if s["type"] == "subtitle"],
    }
    # The audio stream's own bitrate is missing in some containers; the
    # container average is the only number available, and for an audio-only
    # file it is close enough to check a floor against. Say which it was.
    if facts["audio"] and facts["audio"]["bit_rate"] is None:
        if not moving and facts["container"]["bit_rate"]:
            facts["audio"]["bit_rate"] = facts["container"]["bit_rate"]
            facts["audio"]["bit_rate_from_container"] = True
    return facts


def _lower_tags(tags):
    """Tag keys vary in case between containers; the checks should not care."""
    return {str(k).lower(): v for k, v in (tags or {}).items()}


def _stream(s):
    kind = s.get("codec_type", "")
    disposition = s.get("disposition", {}) or {}
    out = {
        "index": s.get("index"),
        "type": kind,
        "codec": s.get("codec_name", ""),
        "codec_long": s.get("codec_long_name", ""),
        "duration_s": _number(s.get("duration")),
        "bit_rate": _number(s.get("bit_rate")),
        "tags": _lower_tags(s.get("tags")),
        "attached_pic": bool(disposition.get("attached_pic")),
        "default": bool(disposition.get("default")),
        "forced": bool(disposition.get("forced")),
    }
    if kind == "audio":
        out.update({
            "sample_rate": int(_number(s.get("sample_rate"), 0) or 0),
            "channels": int(s.get("channels") or 0),
            "channel_layout": s.get("channel_layout", ""),
            "sample_fmt": s.get("sample_fmt", ""),
            "bits_per_sample": int(s.get("bits_per_raw_sample")
                                   or s.get("bits_per_sample") or 0),
            "bit_rate_from_container": False,
        })
    elif kind == "video":
        out.update({
            "width": int(s.get("width") or 0),
            "height": int(s.get("height") or 0),
            "pix_fmt": s.get("pix_fmt", ""),
            "avg_frame_rate": _rate(s.get("avg_frame_rate")),
            "r_frame_rate": _rate(s.get("r_frame_rate")),
            "profile": s.get("profile", ""),
            "level": s.get("level"),
            "nb_frames": _number(s.get("nb_frames")),
            "field_order": s.get("field_order", ""),
        })
    return out


# ------------------------------------------------------------- bitrate mode

# ACX rejects variable-bitrate MP3, and no header field records the mode, so it
# has to be inferred from the packets. Reading every packet of a ten-hour
# audiobook to answer a yes/no question is not worth it: three short windows
# spread across the file separate CBR from VBR reliably, because a VBR encoder
# that produced identical packet sizes in all three would have had to be fed
# three identical stretches of audio.
_SAMPLE_WINDOWS = 3
_SAMPLE_SECONDS = 8


def bitrate_mode(path, duration_s=None, ffprobe=None):
    """Return 'cbr', 'vbr' or 'unknown' for the first audio stream."""
    if ffprobe is None:
        _, ffprobe = platform_support.require_tools()

    intervals = _intervals(duration_s)
    sizes = []
    for interval in intervals:
        try:
            out = _run(ffprobe, ["-select_streams", "a:0", "-read_intervals",
                                 interval, "-show_entries", "packet=size",
                                 "-print_format", "json", path], timeout=60)
        except ProbeError:
            return "unknown"
        packets = json.loads(out).get("packets", []) or []
        sizes.extend(int(p["size"]) for p in packets if p.get("size"))

    return classify_packet_sizes(sizes)


def classify_packet_sizes(sizes):
    """CBR MP3 alternates between at most two frame sizes because of padding."""
    if len(sizes) < 20:
        return "unknown"
    # Ignore the first and last packet of each window: a window boundary can
    # cut a frame, and a truncated frame is not evidence of anything.
    distinct = sorted(set(sizes))
    if len(distinct) <= 2 and (distinct[-1] - distinct[0]) <= 1:
        return "cbr"
    return "vbr"


def _intervals(duration_s):
    if not duration_s or duration_s <= _SAMPLE_SECONDS * _SAMPLE_WINDOWS:
        return ["%+d" % 0]
    step = duration_s / (_SAMPLE_WINDOWS + 1)
    return ["%.3f%%+%d" % (step * (i + 1), _SAMPLE_SECONDS)
            for i in range(_SAMPLE_WINDOWS)]
