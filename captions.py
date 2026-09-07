"""Reading the captions, and the ways they go wrong.

Three formats, because between them they are almost everything anybody
delivers: SubRip, WebVTT, and Advanced SubStation. They are parsed here rather
than rendered, because every fault worth catching before delivery — cues that
overlap, cues nobody can read in the time given, lines too long for the frame,
a font the machine does not have — is visible in the file itself.

What is *not* here is a judgement about wording, translation, or whether a
caption is a good caption. This module counts and measures. The thresholds
belong to the target, and reasonable people set them differently.

On reading speed
----------------
Characters per second is the measure most subtitling guidance is written in,
and it is a proxy: it knows nothing about vocabulary, language, or who is
watching. Seventeen characters a second is comfortable for most adult viewers
in English and far too fast for a child's programme. The number lives in the
profile, where it can be argued with, and the report says which number it used.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import platform_support

SIDECAR_EXTENSIONS = ("srt", "vtt", "ass", "ssa")

# ASS override blocks, HTML-ish tags, and the two ways a line break is written.
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_ASS_DRAWING = re.compile(r"\\p[1-9].*?\\p0", re.S)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_ASS_FONT = re.compile(r"\\fn([^\\{}]+)")
_VTT_TIME = re.compile(
    r"(\d{1,3}):(\d{2}):(\d{2})[.,](\d{1,3})|(\d{1,3}):(\d{2})[.,](\d{1,3})")


class CaptionError(RuntimeError):
    """The caption file could not be read, with the reason."""


# ------------------------------------------------------------------ finding

def find(media_path, facts=None, explicit=None, ffmpeg=None):
    """Locate captions for a media file. Returns a track dict, or None.

    An explicit path wins. Otherwise a sidecar beside the media is preferred
    over an embedded stream, because a sidecar is the file that will actually
    be delivered alongside it — and if both exist and disagree, the one on disk
    is the one somebody edited last.
    """
    if explicit:
        if not os.path.isfile(explicit):
            raise CaptionError(f"No such caption file: {explicit}")
        return load(explicit)

    stem = os.path.splitext(media_path)[0]
    for extension in SIDECAR_EXTENSIONS:
        candidate = f"{stem}.{extension}"
        if os.path.isfile(candidate):
            return load(candidate)

    if facts and facts.get("subtitle_streams"):
        return extract(media_path, facts["subtitle_streams"][0], ffmpeg)
    return None


def load(path):
    """Read a caption file, choosing the parser by what is inside it."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        raise CaptionError(f"Could not read {os.path.basename(path)}: {error}")
    track = parse(text, os.path.splitext(path)[1].lstrip(".").lower())
    track["source"] = path
    track["origin"] = "sidecar"
    return track


def extract(media_path, stream, ffmpeg=None):
    """Pull an embedded subtitle stream out as ASS, which keeps its styling."""
    if ffmpeg is None:
        ffmpeg, _ = platform_support.require_tools()
    # Bitmap subtitles (PGS, VobSub) are pictures, not text; there is nothing
    # here to measure and saying so is better than emitting an empty track.
    if stream.get("codec") in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
        return {"cues": [], "format": stream.get("codec"), "fonts": [],
                "origin": "embedded", "source": media_path,
                "unreadable": "These are picture subtitles, not text — there "
                              "is nothing in them to measure."}
    index = stream.get("index", 0)
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-i", media_path,
             "-map", f"0:{index}", "-f", "ass", "-"],
            capture_output=True, text=True, timeout=300,
            **platform_support.no_console())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CaptionError(f"Could not extract the subtitle stream: {error}")
    if result.returncode != 0 or not result.stdout.strip():
        return None
    track = parse(result.stdout, "ass")
    track["source"] = f"{os.path.basename(media_path)} (stream {index})"
    track["origin"] = "embedded"
    return track


# ------------------------------------------------------------------ parsing

def parse(text, hint=""):
    """Parse SRT, WebVTT or ASS. The hint is the file extension, if there was one."""
    head = text.lstrip()[:200].upper()
    if "[SCRIPT INFO]" in head or "[V4" in head or hint in ("ass", "ssa"):
        return parse_ass(text)
    if head.startswith("WEBVTT") or hint == "vtt":
        return parse_vtt(text)
    return parse_srt(text)


def _timestamp(value):
    """Any of the three ways these formats write a time, as seconds."""
    value = value.strip()
    found = _VTT_TIME.search(value)
    if not found:
        return None
    if found.group(1) is not None:
        hours, minutes, seconds, fraction = found.group(1, 2, 3, 4)
    else:
        hours, minutes, seconds, fraction = "0", *found.group(5, 6, 7)
    fraction = (fraction + "00")[:3]
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            + int(fraction) / 1000.0)


def parse_srt(text):
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip() != ""]
        if not lines:
            continue
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        start, _, end = lines[timing].partition("-->")
        body = lines[timing + 1:]
        cues.append(_cue(len(cues) + 1, _timestamp(start), _timestamp(end),
                         "\n".join(body)))
    return {"cues": [c for c in cues if c], "format": "srt", "fonts": []}


def parse_vtt(text):
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip() != ""]
        if not lines:
            continue
        first = lines[0].strip().upper()
        # WEBVTT headers, comments and style blocks are not cues.
        if first.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        start, _, rest = lines[timing].partition("-->")
        # Cue settings (line:, align:, position:) follow the end time.
        end = rest.strip().split()[0] if rest.strip() else ""
        cues.append(_cue(len(cues) + 1, _timestamp(start), _timestamp(end),
                         "\n".join(lines[timing + 1:])))
    return {"cues": [c for c in cues if c], "format": "vtt", "fonts": []}


def parse_ass(text):
    cues, fonts, order = [], set(), []
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower().startswith("style:"):
            parts = line.split(":", 1)[1].split(",")
            if len(parts) > 1 and parts[1].strip():
                fonts.add(parts[1].strip())
            continue
        if not line.lower().startswith("dialogue:"):
            continue
        fields = line.split(":", 1)[1].split(",", 9)
        if len(fields) < 10:
            continue
        start, end, body = fields[1], fields[2], fields[9]
        fonts.update(_ASS_FONT.findall(body))
        cues.append(_cue(len(cues) + 1, _timestamp(start), _timestamp(end),
                         body))
        order.append(cues[-1])
    # Dialogue lines are not required to be in time order, and a file where
    # they are not would otherwise report every cue as overlapping the last.
    cues = sorted([c for c in cues if c], key=lambda c: c["start"])
    for index, cue in enumerate(cues, 1):
        cue["index"] = index
    return {"cues": cues, "format": "ass", "fonts": sorted(fonts)}


def _cue(index, start, end, body):
    if start is None or end is None:
        return None
    text = clean(body)
    lines = [l for l in text.split("\n") if l.strip() != ""]
    duration = end - start
    characters = len(text.replace("\n", " ").strip())
    return {
        "index": index,
        "start": start,
        "end": end,
        "duration": duration,
        "text": text,
        "lines": lines,
        "line_count": len(lines),
        "longest_line": max((len(l) for l in lines), default=0),
        "characters": characters,
        # A cue of zero length would divide by zero, and a reading speed for a
        # cue nobody can see is not a meaningful number anyway.
        "cps": (characters / duration) if duration > 0 else None,
    }


def clean(body):
    """Strip the markup, keep the words and the line breaks."""
    text = _ASS_DRAWING.sub("", body)
    text = _ASS_OVERRIDE.sub("", text)
    text = re.sub(r"\\[Nn]", "\n", text)
    text = _TAG.sub("", text)
    text = text.replace("\\h", " ")
    return "\n".join(line.strip() for line in text.split("\n")).strip()


# ---------------------------------------------------------------- measuring

def measure(track, duration_s=None):
    """Turn a parsed track into the numbers the rules are written against."""
    cues = track.get("cues") or []
    out = {
        "caption_format": track.get("format"),
        "caption_source": track.get("source"),
        "caption_origin": track.get("origin"),
        "caption_cue_count": len(cues),
        "cues": cues,
    }
    if track.get("unreadable"):
        out["caption_unreadable"] = track["unreadable"]
        return out
    if not cues:
        return out

    overlaps, gaps = [], []
    previous = None
    for cue in cues:
        if previous is not None:
            gap = cue["start"] - previous["end"]
            if gap < 0:
                overlaps.append({
                    "start": cue["start"], "end": previous["end"],
                    "duration": -gap,
                    "detail": f"cue {cue['index']} starts {-gap:.2f} s before "
                              f"cue {previous['index']} ends"})
            else:
                gaps.append(gap)
        previous = cue

    speeds = [c["cps"] for c in cues if c["cps"] is not None]
    past_end = 0.0
    if duration_s:
        # A cue that *starts* after the last frame is past the end even when
        # its own timing is backwards and its end is earlier than its start.
        past_end = max((max(c["start"], c["end"]) - duration_s for c in cues),
                       default=0.0)

    out.update({
        "caption_overlaps": len(overlaps),
        "caption_overlap_intervals": overlaps,
        "caption_shortest_cue_s": round(min(c["duration"] for c in cues), 3),
        "caption_longest_cue_s": round(max(c["duration"] for c in cues), 3),
        "caption_max_cps": round(max(speeds), 2) if speeds else None,
        "caption_max_line_length": max(c["longest_line"] for c in cues),
        "caption_max_lines": max(c["line_count"] for c in cues),
        "caption_shortest_gap_s": round(min(gaps), 3) if gaps else None,
        "caption_past_end_s": round(max(0.0, past_end), 3),
        "caption_empty_cues": sum(1 for c in cues if not c["text"]),
        "caption_bad_timing": sum(1 for c in cues if c["duration"] <= 0),
        "caption_fonts": track.get("fonts") or [],
    })
    missing = missing_fonts(track.get("fonts") or [])
    out["caption_missing_fonts"] = None if missing is None else len(missing)
    out["caption_missing_font_names"] = missing or []
    return out


# A cue-level fault is located by the cues that cause it, and which cues those
# are depends on the rule's own threshold — so the test lives here, next to the
# thing it is testing, and checks.py asks for it by metric name.
OFFENDERS = {
    "caption_max_cps":
        lambda cue, rule: cue["cps"] is not None
        and rule.get("max") is not None and cue["cps"] > rule["max"],
    "caption_max_line_length":
        lambda cue, rule: rule.get("max") is not None
        and cue["longest_line"] > rule["max"],
    "caption_max_lines":
        lambda cue, rule: rule.get("max") is not None
        and cue["line_count"] > rule["max"],
    "caption_shortest_cue_s":
        lambda cue, rule: rule.get("min") is not None
        and cue["duration"] < rule["min"],
    "caption_longest_cue_s":
        lambda cue, rule: rule.get("max") is not None
        and cue["duration"] > rule["max"],
    "caption_empty_cues": lambda cue, rule: not cue["text"],
    "caption_bad_timing": lambda cue, rule: cue["duration"] <= 0,
}

DETAIL = {
    "caption_max_cps": lambda cue: f"cue {cue['index']}, "
                                   f"{cue['cps']:.1f} characters a second",
    "caption_max_line_length": lambda cue: f"cue {cue['index']}, "
                                           f"{cue['longest_line']} characters",
    "caption_max_lines": lambda cue: f"cue {cue['index']}, "
                                     f"{cue['line_count']} lines",
    "caption_shortest_cue_s": lambda cue: f"cue {cue['index']}, "
                                          f"{cue['duration']:.2f} s",
    "caption_longest_cue_s": lambda cue: f"cue {cue['index']}, "
                                         f"{cue['duration']:.2f} s",
    "caption_empty_cues": lambda cue: f"cue {cue['index']} is empty",
    "caption_bad_timing": lambda cue: f"cue {cue['index']} ends before it starts",
}


def offending_cues(metric, rule, cues, limit=200):
    """The cues that break one rule, as intervals the report can print."""
    test = OFFENDERS.get(metric)
    if test is None:
        return []
    describe = DETAIL.get(metric, lambda cue: f"cue {cue['index']}")
    out = []
    for cue in cues or []:
        if test(cue, rule):
            out.append({"start": cue["start"], "end": cue["end"],
                        "detail": describe(cue)})
            if len(out) >= limit:
                break
    return out


# -------------------------------------------------------------------- fonts

def missing_fonts(wanted):
    """Which of these fonts this machine does not have. None means unknown.

    Font availability is answered by fontconfig when it is installed, and not
    guessed when it is not: a caption file naming a font is only a problem on a
    machine that has to render it, and reporting an absence this tool cannot
    actually verify would be worse than admitting it did not check.
    """
    if not wanted:
        return []
    installed = installed_families()
    if installed is None:
        return None
    folded = {name.strip().lower() for name in installed}
    return sorted({name for name in wanted
                   if name.strip().lower() not in folded})


def installed_families(_cache={}):
    if "families" in _cache:
        return _cache["families"]
    families = None
    binary = shutil.which("fc-list")
    if binary:
        try:
            result = subprocess.run([binary, ":", "family"],
                                    capture_output=True, text=True, timeout=30,
                                    **platform_support.no_console())
            if result.returncode == 0:
                families = set()
                for line in result.stdout.splitlines():
                    # fc-list prints every localised alias, comma separated.
                    for alias in line.split(","):
                        if alias.strip():
                            families.add(alias.strip())
        except (OSError, subprocess.TimeoutExpired):
            families = None
    _cache["families"] = families
    return families
