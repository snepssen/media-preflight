"""What the audio actually contains, measured in one decode.

Everything the audio half of the report needs — integrated and short-term
loudness, true peak, RMS, noise floor, per-channel levels, stereo phase, and
every stretch of silence — comes out of a single pass over the file. That is
not an optimisation for its own sake: a second decode of a two-hour audiobook
costs a minute of somebody's afternoon, and measurements taken in separate
passes can disagree about where a moment is.

The filter chain is:

    ebur128 -> ametadata(print) -> [aphasemeter] -> silencedetect -> astats

ebur128 and aphasemeter *inject* per-frame metadata, ametadata prints it to
stdout, and silencedetect and astats write their own findings to stderr. Both
streams are read at once, because a filled pipe that nobody is draining is a
deadlock, and the failure looks exactly like a slow file.

Timeline resolution
-------------------
ebur128 reports every 100 ms. The timeline is kept at one second, holding the
loudest momentary and short-term value seen within each second and the running
true peak. The report quotes timestamps to the second, so the finer grain would
be thrown away at the end anyway — and at one second a ten-hour audiobook costs
36,000 rows instead of 360,000.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading

import platform_support


# Measurement parameters. A profile may override them, because "silence" is a
# claim about a threshold and different targets draw the line differently.
DEFAULTS = {
    "silence_threshold_db": -45.0,   # below this counts as silence
    "silence_min_s": 0.30,           # shorter gaps are speech, not silence
    "clip_threshold_dbfs": -0.10,    # sample peak at or above this is clipping
}

_META_FRAME = re.compile(r"^frame:\d+\s+pts:\S+\s+pts_time:(\S+)")
_META_KEY = re.compile(r"^lavfi\.([A-Za-z0-9_.]+)=(-?[\d.]+|-?inf|nan)$")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_SUMMARY_NUMBER = re.compile(r"(-?[\d.]+|-?inf)")


def _db(amplitude):
    """Linear amplitude to dBFS, with digital silence as -inf rather than a crash."""
    if amplitude is None:
        return None
    if amplitude <= 0:
        return float("-inf")
    return 20.0 * math.log10(amplitude)


def _float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value


class Timeline:
    """Per-second maxima, accumulated as the metadata stream is read."""

    def __init__(self):
        self.seconds = {}          # int second -> dict
        self.true_peak = 0.0       # running maximum, linear
        self.phase_min = None

    def add(self, t, keys):
        second = int(t)
        row = self.seconds.get(second)
        if row is None:
            row = self.seconds[second] = {
                "t": second, "momentary": None, "short_term": None,
                "true_peak_dbfs": None, "phase": None}

        momentary = keys.get("r128.M")
        short_term = keys.get("r128.S")
        # ebur128 reports -120.7 for a window with nothing in it; keeping those
        # would make every gap look like the quietest moment in the programme.
        if momentary is not None and momentary > -70:
            row["momentary"] = max(row["momentary"] or -math.inf, momentary)
        if short_term is not None and short_term > -70:
            row["short_term"] = max(row["short_term"] or -math.inf, short_term)

        peak = keys.get("r128.true_peak")
        if peak is not None and peak > self.true_peak:
            self.true_peak = peak
            row["true_peak_dbfs"] = _db(peak)

        phase = keys.get("aphasemeter.phase")
        if phase is not None:
            row["phase"] = min(row["phase"], phase) if row["phase"] is not None else phase
            self.phase_min = phase if self.phase_min is None else min(self.phase_min, phase)

    def rows(self):
        return [self.seconds[k] for k in sorted(self.seconds)]

    def first_time_at_or_above(self, dbfs):
        """When the running true peak first reached a level. The moment a peak
        problem *begins* is the useful timestamp; later peaks are the same fault."""
        for row in self.rows():
            if row["true_peak_dbfs"] is not None and row["true_peak_dbfs"] >= dbfs:
                return float(row["t"])
        return None

    def runs_outside(self, key, minimum=None, maximum=None, min_length=2):
        """Contiguous stretches where a per-second value left a band.

        ``min_length`` exists so that one second past the line — which every
        real recording contains — is not reported as a fault. Two consecutive
        seconds is a passage; one is a transient.
        """
        runs, current = [], None
        for row in self.rows():
            value = row.get(key)
            outside = value is not None and (
                (minimum is not None and value < minimum) or
                (maximum is not None and value > maximum))
            if outside:
                excess = max(
                    (value - maximum) if maximum is not None else -math.inf,
                    (minimum - value) if minimum is not None else -math.inf)
                if current is None:
                    current = {"start": row["t"], "end": row["t"],
                               "worst": value, "excess": excess}
                else:
                    current["end"] = row["t"]
                    if excess > current["excess"]:
                        current["worst"], current["excess"] = value, excess
            elif current is not None:
                runs.append(current)
                current = None
        if current is not None:
            runs.append(current)
        for run in runs:
            run["end"] += 1
        return [r for r in runs if (r["end"] - r["start"]) >= min_length]


def build_filter_chain(channels, options):
    """The one chain every audio measurement comes out of."""
    parts = ["ebur128=peak=true:metadata=1", "ametadata=print:file=-"]
    if channels == 2:
        # Phase is a statement about the relationship between two channels; on
        # anything else aphasemeter reports 1.0 and means nothing by it.
        parts.insert(1, "aphasemeter=video=0")
    parts.append("silencedetect=noise=%gdB:d=%g" % (
        options["silence_threshold_db"], options["silence_min_s"]))
    parts.append("astats")
    return ",".join(parts)


def analyse(path, ffmpeg=None, channels=2, duration_s=None, options=None,
            progress=None):
    """Measure one file's first audio stream. Returns the measurements dict."""
    if ffmpeg is None:
        ffmpeg, _ = platform_support.require_tools()
    settings = dict(DEFAULTS)
    settings.update(options or {})

    command = [ffmpeg, "-hide_banner", "-nostats", "-v", "info",
               "-i", path, "-map", "0:a:0",
               "-af", build_filter_chain(channels, settings),
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
        timeline = parse_metadata_stream(process.stdout, duration_s, progress)
    finally:
        process.stdout.close()
        process.wait()
        pump.join(timeout=5)
        process.stderr.close()
    stderr = "".join(stderr_lines)

    if process.returncode != 0:
        detail = [l.strip() for l in stderr.splitlines() if l.strip()]
        raise AnalysisError(detail[-1] if detail else
                            "ffmpeg failed while measuring the audio.")

    measurements = {"timeline": timeline.rows(),
                    "phase_min": timeline.phase_min,
                    "settings": settings}
    measurements.update(parse_ebur128_summary(stderr))
    measurements.update(parse_astats(stderr))
    measurements["silences"] = parse_silences(stderr, duration_s)
    _derive(measurements, duration_s)
    return measurements


class AnalysisError(RuntimeError):
    """ffmpeg could not measure the file, with ffmpeg's own last word attached."""


def parse_metadata_stream(stream, duration_s=None, progress=None):
    """Read ametadata's output as it arrives; never hold the whole file's worth."""
    timeline = Timeline()
    time_s, keys = None, {}
    last_reported = -1.0
    for line in stream:
        line = line.strip()
        frame = _META_FRAME.match(line)
        if frame:
            if time_s is not None:
                timeline.add(time_s, keys)
            time_s = _float(frame.group(1)) or 0.0
            keys = {}
            if progress and duration_s and time_s - last_reported >= 1.0:
                last_reported = time_s
                progress(min(1.0, time_s / duration_s))
            continue
        pair = _META_KEY.match(line)
        if pair and time_s is not None:
            keys[pair.group(1)] = _float(pair.group(2))
    if time_s is not None:
        timeline.add(time_s, keys)
    return timeline


def parse_ebur128_summary(text):
    """The Summary block ffmpeg prints once the whole file has been read."""
    out = {"integrated_lufs": None, "loudness_range_lu": None,
           "lra_low_lufs": None, "lra_high_lufs": None,
           "true_peak_dbfs": None}
    block = text.split("Summary:")
    if len(block) < 2:
        return out
    fields = {
        "integrated_lufs": r"I:\s*(-?[\d.]+|-?inf)\s*LUFS",
        "loudness_range_lu": r"LRA:\s*(-?[\d.]+|-?inf)\s*LU",
        "lra_low_lufs": r"LRA low:\s*(-?[\d.]+|-?inf)\s*LUFS",
        "lra_high_lufs": r"LRA high:\s*(-?[\d.]+|-?inf)\s*LUFS",
        "true_peak_dbfs": r"Peak:\s*(-?[\d.]+|-?inf)\s*dBFS",
    }
    tail = block[-1]
    for name, pattern in fields.items():
        found = re.search(pattern, tail)
        if found:
            out[name] = _float(found.group(1))
    return out


def parse_astats(text):
    """Per-channel and overall time-domain statistics from the trailing block.

    ffmpeg's own label for the quietest RMS window is misspelled 'RMS through'
    in some builds and 'RMS trough' in others; both are accepted rather than
    silently returning nothing on half the world's installations.
    """
    lines = [re.sub(r"^\[Parsed_astats[^\]]*\]\s*", "", l.strip())
             for l in text.splitlines()]
    sections, current = [], None
    for line in lines:
        if re.match(r"^Channel:\s*\d+$", line) or line == "Overall":
            current = {"name": line}
            sections.append(current)
            continue
        if current is None:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()

    def pick(section, *names):
        for name in names:
            if name in section:
                number = _SUMMARY_NUMBER.search(section[name])
                if number:
                    return _float(number.group(1))
        return None

    channels, overall = [], {}
    for section in sections:
        stats = {
            "rms_dbfs": pick(section, "RMS level dB"),
            "rms_peak_dbfs": pick(section, "RMS peak dB"),
            "rms_trough_dbfs": pick(section, "RMS trough dB", "RMS through dB"),
            "peak_dbfs": pick(section, "Peak level dB"),
            "dc_offset": pick(section, "DC offset"),
            "flat_factor": pick(section, "Flat factor"),
            "abs_peak_count": pick(section, "Abs Peak count"),
            "noise_floor_dbfs": pick(section, "Noise floor dB"),
            "crest_factor": pick(section, "Crest factor"),
            "bit_depth": section.get("Bit depth"),
            "zero_crossings": pick(section, "Zero crossings"),
        }
        if section["name"] == "Overall":
            overall = stats
        else:
            stats["channel"] = int(section["name"].split(":")[1])
            channels.append(stats)

    result = {"channels": channels}
    result.update({k: v for k, v in overall.items() if k != "channel"})
    return result


def parse_silences(text, duration_s=None):
    """silencedetect's event pairs, turned into intervals with a position.

    A silence that never ends is the file ending in silence; ffmpeg reports the
    start and then simply stops, so the tail has to be closed here.
    """
    events = []
    for line in text.splitlines():
        if "silence_start" in line:
            found = _SILENCE_START.search(line)
            if found:
                events.append(("start", _float(found.group(1))))
        elif "silence_end" in line:
            found = _SILENCE_END.search(line)
            if found:
                events.append(("end", _float(found.group(1))))

    silences, open_start = [], None
    for kind, value in events:
        if kind == "start":
            open_start = max(0.0, value or 0.0)
        elif open_start is not None:
            silences.append({"start": open_start, "end": value})
            open_start = None
    if open_start is not None:
        silences.append({"start": open_start,
                         "end": duration_s if duration_s else open_start})

    for item in silences:
        item["duration"] = max(0.0, (item["end"] or 0.0) - item["start"])
        if item["start"] <= 0.05:
            item["position"] = "head"
        elif duration_s and item["end"] is not None and \
                item["end"] >= duration_s - 0.05:
            item["position"] = "tail"
        else:
            item["position"] = "middle"
    return silences


def _derive(measurements, duration_s):
    """The few numbers the checks want that are a sentence away from the rest."""
    silences = measurements.get("silences") or []
    head = [s for s in silences if s["position"] == "head"]
    tail = [s for s in silences if s["position"] == "tail"]
    measurements["lead_silence_s"] = head[0]["duration"] if head else 0.0
    measurements["tail_silence_s"] = tail[-1]["duration"] if tail else 0.0
    measurements["mid_silences"] = [s for s in silences
                                    if s["position"] == "middle"]
    measurements["duration_s"] = duration_s

    # An abrupt ending is a file that stops while it is still loud. Both halves
    # matter: no tail silence at all, and programme level right to the last
    # sample. Either alone is ordinary.
    timeline = measurements.get("timeline") or []
    last_loudness = None
    for row in reversed(timeline):
        if row["momentary"] is not None:
            last_loudness = row["momentary"]
            break
    measurements["final_momentary_lufs"] = last_loudness
    measurements["ends_abruptly"] = bool(
        measurements["tail_silence_s"] < 0.05 and
        last_loudness is not None and last_loudness > -35)

    channels = measurements.get("channels") or []
    rms = [c["rms_dbfs"] for c in channels if c["rms_dbfs"] is not None]
    measurements["silent_channels"] = [
        c["channel"] for c in channels
        if c["rms_dbfs"] is not None and c["rms_dbfs"] < -80]
    # A channel carrying nothing makes the spread infinite, which is true and
    # useless: the silent-channel finding already says what is wrong, and a
    # balance figure of infinity in the report only crowds it out.
    if measurements["silent_channels"]:
        measurements["channel_rms_spread_db"] = None
    else:
        measurements["channel_rms_spread_db"] = (
            max(rms) - min(rms) if len(rms) > 1 else 0.0)


# -------------------------------------------------------- locating the peaks

# The one-second timeline holds the *running* true peak, which answers "when
# did this start" but not "where else". When a peak rule has already failed —
# and only then — a second pass measures the sample peak of every window, which
# is what turns "your file clips" into a list of places to look.

def locate_peaks(path, threshold_dbfs, ffmpeg=None, sample_rate=48000,
                 window_s=1.0, limit=200):
    """Windows whose sample peak reached ``threshold_dbfs``."""
    if ffmpeg is None:
        ffmpeg, _ = platform_support.require_tools()
    window = max(1, int(round((sample_rate or 48000) * window_s)))
    chain = ("asetnsamples=n=%d:p=0,astats=metadata=1:reset=1,"
             "ametadata=print:file=-" % window)
    command = [ffmpeg, "-hide_banner", "-nostats", "-v", "error",
               "-i", path, "-map", "0:a:0", "-af", chain, "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=3600, **platform_support.no_console())
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_peak_windows(result.stdout, threshold_dbfs, window_s, limit)


def parse_peak_windows(text, threshold_dbfs, window_s=1.0, limit=200):
    """Read the windowed astats stream, keeping only the windows that offend."""
    windows, time_s, keys = [], None, {}

    def flush():
        if time_s is None:
            return
        peak = keys.get("astats.Overall.Peak_level")
        if peak is None or peak < threshold_dbfs:
            return
        windows.append({
            "start": float(time_s),
            "end": float(time_s) + window_s,
            "worst": peak,
            "samples": int(keys.get("astats.Overall.Abs_Peak_count") or 0),
            "flat_factor": keys.get("astats.Overall.Flat_factor"),
            "detail": "peak %.2f dBFS" % peak,
        })

    for line in text.splitlines():
        line = line.strip()
        frame = _META_FRAME.match(line)
        if frame:
            flush()
            if len(windows) >= limit:
                break
            time_s = _float(frame.group(1)) or 0.0
            keys = {}
            continue
        pair = _META_KEY.match(line)
        if pair and time_s is not None:
            keys[pair.group(1)] = _float(pair.group(2))
    else:
        flush()
    return merge_adjacent(windows)


def merge_adjacent(windows, gap=0.001):
    """Neighbouring offending windows are one passage, not several faults."""
    merged = []
    for window in windows:
        if merged and window["start"] - merged[-1]["end"] <= gap:
            last = merged[-1]
            last["end"] = window["end"]
            last["samples"] += window["samples"]
            if window["worst"] > last["worst"]:
                last["worst"] = window["worst"]
                last["detail"] = window["detail"]
        else:
            merged.append(dict(window))
    return merged


def summarise_clipping(windows, window_s=1.0):
    """How much of the file holds samples pinned at full scale."""
    seconds = sum(w["end"] - w["start"] for w in windows)
    samples = sum(w.get("samples", 0) for w in windows)
    return {"clipping_seconds": round(seconds, 3),
            "clipped_samples": samples,
            "clipping_windows": windows}
