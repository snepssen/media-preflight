"""What the picture actually contains, measured in one decode.

The audio pass in analysis.py and this one are deliberately separate: they read
different streams, and combining them would mean two filters writing frame
metadata to the same pipe with nothing keeping their blocks apart. One decode
per stream is the rule; a second decode of the same stream needs a reason.

    blackdetect -> freezedetect -> idet -> signalstats -> metadata(print)

blackdetect and freezedetect announce themselves on stderr, signalstats injects
per-frame statistics that metadata prints to stdout. Both streams are read at
once, for the same reason the audio pass does it: a filled pipe nobody is
draining is a deadlock that looks exactly like a slow file.

On flashing
-----------
The flashing check is a *screening* heuristic and says so everywhere it
appears. It counts large frame-to-frame changes in average luminance and
flags any second holding three or more of them, which is where WCAG draws its
general flash threshold. It does not do the spatial analysis a real
photosensitivity test does, it does not look at the proportion of the screen
that changed, and it knows nothing about the separate red-flash rule. It finds
the passages worth looking at with human eyes. It cannot clear a programme, and
nothing in this tool claims it can.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading

import analysis
import platform_support
import probe

DEFAULTS = {
    "black_min_s": 0.5,             # shorter than this is a cut, not a hole
    "black_picture_threshold": 0.98,
    "black_pixel_threshold": 0.10,
    "freeze_min_s": 2.0,
    "freeze_noise_db": -60.0,
    "flash_luma_delta": 20.0,       # 8-bit luma change counted as a transition
    "flash_per_second": 3,          # WCAG's general flash threshold
    # Interlacing thresholds — see classify_fields for what they mean.
    "interlace_share": 0.5,
    "field_dominance": 0.8,
    "interlace_evidence": 0.25,
    "telecine_ratio": 0.05,
}

# idet's own summary, printed once the stream has been read. It prints an
# all-zero block first — an artefact of how it flushes — so the *last* block is
# the one that means anything.
_IDET_MULTI = re.compile(
    r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*"
    r"Progressive:\s*(\d+)\s*Undetermined:\s*(\d+)")
_IDET_REPEAT = re.compile(
    r"Repeated Fields:\s*Neither:\s*(\d+)\s*Top:\s*(\d+)\s*"
    r"Bottom:\s*(\d+)")

_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")
_BLACK_OPEN = re.compile(r"black_start:\s*([\d.]+)\s*$")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")


class VideoError(RuntimeError):
    """ffmpeg could not measure the picture, with its own last word attached."""


def build_filter_chain(options):
    return ",".join([
        "blackdetect=d=%g:pic_th=%g:pix_th=%g" % (
            options["black_min_s"], options["black_picture_threshold"],
            options["black_pixel_threshold"]),
        "freezedetect=n=%gdB:d=%g" % (
            options["freeze_noise_db"], options["freeze_min_s"]),
        "idet",
        "signalstats",
        "metadata=print:file=-",
    ])


def analyse(path, ffmpeg=None, duration_s=None, options=None, progress=None):
    """Measure one file's first moving-picture stream."""
    if ffmpeg is None:
        ffmpeg, _ = platform_support.require_tools()
    settings = dict(DEFAULTS)
    settings.update(options or {})

    command = [ffmpeg, "-hide_banner", "-nostats", "-v", "info",
               "-i", path, "-map", "0:v:0",
               "-vf", build_filter_chain(settings),
               "-f", "null", "-"]

    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               bufsize=1, **platform_support.no_console())
    stderr_lines = []

    def drain():
        for line in process.stderr:
            stderr_lines.append(line)

    pump = threading.Thread(target=drain, daemon=True)
    pump.start()
    try:
        luma = parse_luma_stream(process.stdout, duration_s, progress)
    finally:
        process.stdout.close()
        process.wait()
        pump.join(timeout=5)
        process.stderr.close()
    stderr = "".join(stderr_lines)

    if process.returncode != 0:
        detail = [l.strip() for l in stderr.splitlines() if l.strip()]
        raise VideoError(detail[-1] if detail else
                         "ffmpeg failed while measuring the picture.")

    measurements = {
        "settings": settings,
        "black": parse_black(stderr, duration_s),
        "frozen": parse_freeze(stderr, duration_s),
        "flashes": find_flashing(luma, settings, duration_s),
        "frames_measured": len(luma),
    }
    measurements.update(classify_fields(parse_idet(stderr), settings))
    _derive(measurements, duration_s)
    return measurements


def parse_luma_stream(stream, duration_s=None, progress=None):
    """(time, average luma) per frame, read as it arrives.

    Only YAVG is kept. signalstats prints fifteen fields a frame and a
    ninety-minute feature is 130,000 frames; holding all of it to use one
    column would be a waste of somebody's memory.
    """
    luma, time_s = [], None
    last_reported = -1.0
    for line in stream:
        line = line.strip()
        frame = analysis._META_FRAME.match(line)
        if frame:
            time_s = analysis._float(frame.group(1)) or 0.0
            if progress and duration_s and time_s - last_reported >= 1.0:
                last_reported = time_s
                progress(min(1.0, time_s / duration_s))
            continue
        if time_s is not None and line.startswith("lavfi.signalstats.YAVG="):
            value = analysis._float(line.split("=", 1)[1])
            if value is not None:
                luma.append((time_s, value))
    return luma


def parse_black(text, duration_s=None):
    """blackdetect's intervals. A run that never ends is the file ending black."""
    out = []
    for line in text.splitlines():
        closed = _BLACK.search(line)
        if closed:
            start, end = float(closed.group(1)), float(closed.group(2))
            out.append({"start": start, "end": end, "duration": end - start})
            continue
        open_run = _BLACK_OPEN.search(line.strip())
        if open_run:
            start = float(open_run.group(1))
            end = duration_s if duration_s else start
            out.append({"start": start, "end": end,
                        "duration": max(0.0, end - start)})
    return _position(out, duration_s)


def parse_freeze(text, duration_s=None):
    """freezedetect reports a start and, when the picture moves again, an end."""
    starts, ends = [], []
    for line in text.splitlines():
        found = _FREEZE_START.search(line)
        if found:
            starts.append(float(found.group(1)))
            continue
        found = _FREEZE_END.search(line)
        if found:
            ends.append(float(found.group(1)))

    out = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else (duration_s or start)
        out.append({"start": start, "end": end,
                    "duration": max(0.0, end - start)})
    return _position(out, duration_s)


# --------------------------------------------------------------- interlacing

def parse_idet(text):
    """idet's counts, taken from the last summary block it printed."""
    multi = _IDET_MULTI.findall(text)
    repeat = _IDET_REPEAT.findall(text)
    out = {"tff": 0, "bff": 0, "progressive": 0, "undetermined": 0,
           "repeated_top": 0, "repeated_bottom": 0, "repeated_neither": 0}
    if multi:
        tff, bff, progressive, undetermined = (int(v) for v in multi[-1])
        out.update({"tff": tff, "bff": bff, "progressive": progressive,
                    "undetermined": undetermined})
    if repeat:
        neither, top, bottom = (int(v) for v in repeat[-1])
        out.update({"repeated_neither": neither, "repeated_top": top,
                    "repeated_bottom": bottom})
    return out


def classify_fields(counts, options):
    """Decide what idet's counts actually say, which is less than it looks.

    idet is a screening measurement with a known failure mode: on progressive
    material with hard vertical edges and fast motion it reports a great many
    frames as interlaced. What gives it away is that those detections are
    *mixed* — top-field-first on one frame and bottom-field-first on the next —
    because they are noise rather than field dominance. Genuinely interlaced
    material is overwhelmingly one or the other.

    So two things have to hold before this claims a file is interlaced: enough
    of the decided frames look interlaced at all, and one field order clearly
    dominates. Material that trips the first test and fails the second is
    reported as inconclusive, which is the truthful answer and not a verdict.
    """
    tff, bff = counts["tff"], counts["bff"]
    progressive, undetermined = counts["progressive"], counts["undetermined"]
    decided = tff + bff + progressive
    total = decided + undetermined

    result = {
        "idet": counts,
        "interlace_share": None,
        "field_dominance": None,
        "interlace_detected": "unknown",
        "telecine_ratio": None,
    }
    if not total:
        return result

    # A static shot gives idet nothing to measure and it says so by calling
    # every frame undetermined. That is an absence of evidence, not evidence.
    if decided / total < options["interlace_evidence"]:
        return result

    interlaced_frames = tff + bff
    share = interlaced_frames / decided
    dominance = (max(tff, bff) / interlaced_frames) if interlaced_frames else 0.0
    result["interlace_share"] = round(share, 3)
    result["field_dominance"] = round(dominance, 3)

    repeated = counts["repeated_top"] + counts["repeated_bottom"]
    result["telecine_ratio"] = round(repeated / total, 3)

    if share < options["interlace_share"]:
        result["interlace_detected"] = "progressive"
    elif dominance >= options["field_dominance"]:
        result["interlace_detected"] = "tff" if tff >= bff else "bff"
    else:
        result["interlace_detected"] = "inconclusive"
    return result


def _position(intervals, duration_s):
    for item in intervals:
        if item["start"] <= 0.05:
            item["position"] = "head"
        elif duration_s and item["end"] >= duration_s - 0.05:
            item["position"] = "tail"
        else:
            item["position"] = "middle"
        item["detail"] = "%.2f s" % item["duration"]
    return intervals


def find_flashing(luma, options, duration_s=None):
    """Seconds holding enough large luminance changes to be worth a look.

    A transition is a frame-to-frame change in average luminance past the
    threshold. Three of them inside one second is where WCAG's general flash
    threshold sits, so that is what gets flagged — as a place to look, not as a
    verdict.
    """
    threshold = options["flash_luma_delta"]
    per_second = options["flash_per_second"]

    transitions = []
    previous = None
    for time_s, value in luma:
        if previous is not None and abs(value - previous) >= threshold:
            transitions.append(time_s)
        previous = value

    # Slide a one-second window along the transitions rather than bucketing by
    # whole seconds: three flashes either side of a second boundary are still
    # three flashes in a second.
    regions, index = [], 0
    for start in range(len(transitions)):
        while index < len(transitions) and \
                transitions[index] - transitions[start] < 1.0:
            index += 1
        count = index - start
        if count >= per_second:
            begin, finish = transitions[start], transitions[index - 1]
            if regions and begin <= regions[-1]["end"]:
                regions[-1]["end"] = max(regions[-1]["end"], finish + 1.0)
                regions[-1]["worst"] = max(regions[-1]["worst"], count)
            else:
                regions.append({"start": begin, "end": finish + 1.0,
                                "worst": count})
    for region in regions:
        if duration_s:
            region["end"] = min(region["end"], duration_s)
        region["duration"] = max(0.0, region["end"] - region["start"])
        region["detail"] = "%d transitions in a second" % region["worst"]
    return regions


def _derive(measurements, duration_s):
    black = measurements["black"]
    frozen = measurements["frozen"]
    measurements["black_seconds"] = round(sum(b["duration"] for b in black), 3)
    measurements["longest_black_s"] = round(
        max((b["duration"] for b in black), default=0.0), 3)
    measurements["leading_black_s"] = round(
        sum(b["duration"] for b in black if b["position"] == "head"), 3)
    measurements["trailing_black_s"] = round(
        sum(b["duration"] for b in black if b["position"] == "tail"), 3)
    measurements["frozen_seconds"] = round(
        sum(f["duration"] for f in frozen), 3)
    measurements["longest_frozen_s"] = round(
        max((f["duration"] for f in frozen), default=0.0), 3)
    measurements["flash_regions"] = len(measurements["flashes"])
    measurements["video_duration_s"] = duration_s


# ------------------------------------------------------- frame rate mode

# A variable-frame-rate file is legal, plays fine, and quietly ruins anything
# downstream that assumes a constant one — which is most editing software and
# every "burn in a subtitle at frame N" workflow. No header field records it,
# so it has to be read off the frames, and reading every frame of a feature to
# answer a yes/no question is not worth it. Three windows spread across the
# file separate the two reliably.
_SAMPLE_WINDOWS = 3
_SAMPLE_SECONDS = 4
_TOLERANCE_S = 0.001


def frame_rate_mode(path, duration_s=None, ffprobe=None):
    """Return 'cfr', 'vfr' or 'unknown' for the first video stream."""
    if ffprobe is None:
        _, ffprobe = platform_support.require_tools()

    durations = []
    for interval in _intervals(duration_s):
        try:
            out = probe._run(ffprobe, [
                "-select_streams", "v:0", "-read_intervals", interval,
                "-show_entries", "frame=duration_time", "-print_format",
                "json", path], timeout=60)
        except probe.ProbeError:
            return "unknown"
        for frame in json.loads(out).get("frames", []) or []:
            value = frame.get("duration_time")
            if value not in (None, "N/A"):
                try:
                    durations.append(float(value))
                except ValueError:
                    pass
    return classify_frame_durations(durations)


def classify_frame_durations(durations, tolerance=_TOLERANCE_S, agreement=0.95):
    """Constant when nearly every frame lasts as long as the middle one.

    Not *every* frame: a correctly encoded constant-rate file can still end on
    a frame of a different length, and NTSC rates land on repeating decimals
    that drift by microseconds. Demanding exactness would report most of the
    world's video as variable.
    """
    if len(durations) < 20:
        return "unknown"
    ordered = sorted(durations)
    median = ordered[len(ordered) // 2]
    if median <= 0:
        return "unknown"
    close = sum(1 for d in durations if abs(d - median) <= tolerance)
    return "cfr" if close / len(durations) >= agreement else "vfr"


def _intervals(duration_s):
    if not duration_s or duration_s <= _SAMPLE_SECONDS * _SAMPLE_WINDOWS:
        return ["%+d" % 0]
    step = duration_s / (_SAMPLE_WINDOWS + 1)
    return ["%.3f%%+%d" % (step * (i + 1), _SAMPLE_SECONDS)
            for i in range(_SAMPLE_WINDOWS)]
