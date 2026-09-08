"""Comparing what was measured against what the target asks for.

This module does no work of its own: it is handed the file's declarations, the
measurements, and a profile, and it produces findings. Nothing here runs a
subprocess, so the whole rule engine is testable against a dict.

Where the timestamps come from
------------------------------
A finding carries timestamps only when the timeline holds *the same quantity*
the rule is about. Short-term loudness can localise an integrated-loudness
failure because both are loudness; it cannot localise an RMS failure, because
RMS and LUFS are different measurements and pointing at a moment measured in
one while quoting a threshold in the other would be an invention. Rules that
cannot be localised say so instead of guessing.
"""

from __future__ import annotations

import math

import captions

FAIL, WARN, PASS, SKIP = "fail", "warn", "pass", "skip"


# ------------------------------------------------------------------ metrics
#
# Each entry turns (facts, measurements) into a value. Missing is None, and a
# rule against a missing metric is skipped rather than failed: a file with no
# video stream has not failed the frame-rate rule, it simply has no answer.

def _audio(facts, field, default=None):
    stream = facts.get("audio") or {}
    value = stream.get(field)
    return default if value is None else value


def _dc_offset_max(facts, m):
    offsets = [abs(c["dc_offset"]) for c in (m.get("channels") or [])
               if c.get("dc_offset") is not None]
    return max(offsets) if offsets else None


def _longest_mid_silence(facts, m):
    gaps = m.get("mid_silences") or []
    return max((g["duration"] for g in gaps), default=0.0)


def _bitrate_kbps(facts, m):
    rate = _audio(facts, "bit_rate")
    return round(rate / 1000.0, 1) if rate else None


def _duration(facts, m):
    return (m.get("duration_s") or facts.get("container", {}).get("duration_s"))


def _av_gap(facts, m):
    video = facts.get("video")
    audio = facts.get("audio")
    if not video or not audio:
        return None
    a = audio.get("duration_s") or m.get("duration_s")
    v = video.get("duration_s")
    if a is None or v is None:
        return None
    return abs(a - v)


def _video(facts, field, default=None):
    stream = facts.get("video") or {}
    value = stream.get(field)
    return default if value is None else value


def _resolution(facts, m):
    width, height = _video(facts, "width"), _video(facts, "height")
    return f"{width}x{height}" if width and height else None


def _aspect_ratio(facts, m):
    """Width over height, so a rule can be written as a band rather than a
    list of resolutions somebody has to keep up to date."""
    width, height = _video(facts, "width"), _video(facts, "height")
    if not width or not height:
        return None
    return round(width / height, 4)


def _declared_interlaced(facts):
    """What the file claims about its own fields, or None if it says nothing."""
    order = _video(facts, "field_order")
    if not order or order == "unknown":
        return None
    return order != "progressive"


def _interlaced(facts, m):
    """Interlaced if either the header says so or the picture shows it.

    Either alone is a reason to deinterlace: a header claiming interlace makes
    players deinterlace whatever is actually inside, and interlaced pictures in
    a file flagged progressive are combed on every screen that trusts the flag.
    Where the two disagree, `field_order_disagrees` reports the disagreement
    itself, which is usually the more useful finding.
    """
    detected = m.get("interlace_detected")
    declared = _declared_interlaced(facts)
    if detected in ("tff", "bff"):
        return True
    if declared is not None:
        return declared
    if detected == "progressive":
        return False
    return None


def _field_order_disagrees(facts, m):
    """True when the header and the picture cannot both be right."""
    detected = m.get("interlace_detected")
    if detected not in ("tff", "bff", "progressive"):
        return None
    declared = _declared_interlaced(facts)
    if declared is None:
        return None
    return declared != (detected in ("tff", "bff"))


def _video_bitrate_kbps(facts, m):
    rate = _video(facts, "bit_rate")
    return round(rate / 1000.0, 1) if rate else None


def _worst_short_term(facts, m):
    values = [row["short_term"] for row in (m.get("timeline") or [])
              if row.get("short_term") is not None]
    return max(values) if values else None


METRICS = {
    # measured
    "integrated_lufs": lambda f, m: m.get("integrated_lufs"),
    "loudness_range_lu": lambda f, m: m.get("loudness_range_lu"),
    "true_peak_dbfs": lambda f, m: m.get("true_peak_dbfs"),
    "peak_dbfs": lambda f, m: m.get("peak_dbfs"),
    "rms_dbfs": lambda f, m: m.get("rms_dbfs"),
    "noise_floor_dbfs": lambda f, m: m.get("noise_floor_dbfs"),
    "dc_offset_max": _dc_offset_max,
    "channel_rms_spread_db": lambda f, m: m.get("channel_rms_spread_db"),
    # A file with no audio has not passed the silent-channel check; it has no
    # channels to be silent, which is a different thing and reads as a skip.
    "silent_channel_count": lambda f, m: (float(len(m["silent_channels"]))
                                          if "silent_channels" in m else None),
    "phase_min": lambda f, m: m.get("phase_min"),
    "lead_silence_s": lambda f, m: m.get("lead_silence_s"),
    "tail_silence_s": lambda f, m: m.get("tail_silence_s"),
    "longest_mid_silence_s": _longest_mid_silence,
    "ends_abruptly": lambda f, m: m.get("ends_abruptly"),
    "clipping_seconds": lambda f, m: m.get("clipping_seconds"),
    "short_term_excursions": _worst_short_term,
    "duration_s": _duration,
    "duration_min": lambda f, m: (_duration(f, m) / 60.0
                                  if _duration(f, m) is not None else None),
    # declared
    "audio_codec": lambda f, m: _audio(f, "codec"),
    "sample_rate": lambda f, m: _audio(f, "sample_rate"),
    "channels": lambda f, m: _audio(f, "channels"),
    "audio_bitrate_kbps": _bitrate_kbps,
    "bitrate_mode": lambda f, m: m.get("bitrate_mode"),
    "container": lambda f, m: f.get("container", {}).get("format_name"),
    "bit_depth": lambda f, m: _audio(f, "bits_per_sample") or None,
    "cover_art": lambda f, m: f.get("cover_art"),
    "av_duration_gap_s": _av_gap,

    # picture, declared
    "video_codec": lambda f, m: _video(f, "codec"),
    "video_width": lambda f, m: _video(f, "width"),
    "video_height": lambda f, m: _video(f, "height"),
    "resolution": _resolution,
    "frame_rate": lambda f, m: _video(f, "avg_frame_rate"),
    "frame_rate_mode": lambda f, m: m.get("frame_rate_mode"),
    "pix_fmt": lambda f, m: _video(f, "pix_fmt"),
    "aspect_ratio": _aspect_ratio,
    "video_bitrate_kbps": _video_bitrate_kbps,
    "interlaced": _interlaced,
    "interlace_declared": lambda f, m: _declared_interlaced(f),
    "interlace_detected": lambda f, m: m.get("interlace_detected"),
    "field_order_disagrees": _field_order_disagrees,
    "telecine_ratio": lambda f, m: m.get("telecine_ratio"),

    # picture, measured
    "black_seconds": lambda f, m: m.get("black_seconds"),
    "longest_black_s": lambda f, m: m.get("longest_black_s"),
    "leading_black_s": lambda f, m: m.get("leading_black_s"),
    "trailing_black_s": lambda f, m: m.get("trailing_black_s"),
    "frozen_seconds": lambda f, m: m.get("frozen_seconds"),
    "longest_frozen_s": lambda f, m: m.get("longest_frozen_s"),
    "flash_regions": lambda f, m: m.get("flash_regions"),

    # captions
    "caption_cue_count": lambda f, m: m.get("caption_cue_count"),
    "caption_format": lambda f, m: m.get("caption_format"),
    "caption_overlaps": lambda f, m: m.get("caption_overlaps"),
    "caption_shortest_cue_s": lambda f, m: m.get("caption_shortest_cue_s"),
    "caption_longest_cue_s": lambda f, m: m.get("caption_longest_cue_s"),
    "caption_max_cps": lambda f, m: m.get("caption_max_cps"),
    "caption_max_line_length": lambda f, m: m.get("caption_max_line_length"),
    "caption_max_lines": lambda f, m: m.get("caption_max_lines"),
    "caption_shortest_gap_s": lambda f, m: m.get("caption_shortest_gap_s"),
    "caption_past_end_s": lambda f, m: m.get("caption_past_end_s"),
    "caption_empty_cues": lambda f, m: m.get("caption_empty_cues"),
    "caption_bad_timing": lambda f, m: m.get("caption_bad_timing"),
    "caption_missing_fonts": lambda f, m: m.get("caption_missing_fonts"),
    "caption_uncaptioned_speech_s":
        lambda f, m: m.get("caption_uncaptioned_speech_s"),
    "caption_over_silence": lambda f, m: m.get("caption_over_silence"),
    "caption_drift_s": lambda f, m: m.get("caption_drift_s"),
}

# Metrics whose failures the one-second timeline can point at, because the
# timeline holds the same quantity. Anything absent from here is a whole-file
# measurement and is reported without timestamps.
LOCATABLE = {
    "integrated_lufs": "short_term",
    "short_term_excursions": "short_term",
    "true_peak_dbfs": "true_peak",
    "peak_dbfs": "sample_peak",
    "clipping_seconds": "sample_peak",
    "phase_min": "phase",
    "lead_silence_s": "silence",
    "tail_silence_s": "silence",
    "longest_mid_silence_s": "silence",
    "black_seconds": "black",
    "longest_black_s": "black",
    "leading_black_s": "black",
    "trailing_black_s": "black",
    "frozen_seconds": "frozen",
    "longest_frozen_s": "frozen",
    "flash_regions": "flash",
    "caption_overlaps": "caption_overlaps",
    "caption_max_cps": "captions",
    "caption_max_line_length": "captions",
    "caption_max_lines": "captions",
    "caption_shortest_cue_s": "captions",
    "caption_longest_cue_s": "captions",
    "caption_empty_cues": "captions",
    "caption_bad_timing": "captions",
    "caption_uncaptioned_speech_s": "caption_uncaptioned",
    "caption_over_silence": "caption_orphans",
}


# Metrics that come out of the file's header rather than out of its audio.
# A header field has no "when": saying so keeps the report from offering an
# apology for the absence of a timestamp on a sample rate.
DECLARED_METRICS = {
    "audio_codec", "sample_rate", "channels", "audio_bitrate_kbps",
    "bitrate_mode", "container", "bit_depth", "cover_art", "duration_s",
    "duration_min", "av_duration_gap_s",
    "video_codec", "video_width", "video_height", "resolution", "frame_rate",
    "frame_rate_mode", "pix_fmt", "video_bitrate_kbps", "interlaced",
    "aspect_ratio",
    "caption_cue_count", "caption_format", "caption_missing_fonts",
    "caption_shortest_gap_s", "caption_past_end_s",
    "interlace_declared", "interlace_detected",
}


def scope_of(metric):
    """'declared', 'moment' or 'file' — what kind of answer this metric is."""
    if metric in DECLARED_METRICS:
        return "declared"
    if metric in LOCATABLE:
        return "moment"
    return "file"


def evaluate(facts, measurements, profile, locator=None):
    """Run every rule in the profile. Returns the report dict."""
    findings = [check(rule, facts, measurements, locator)
                for rule in profile.get("rules", [])]
    statuses = {f["status"] for f in findings}
    if FAIL in statuses:
        verdict = FAIL
    elif WARN in statuses:
        verdict = WARN
    else:
        verdict = PASS

    return {
        "verdict": verdict,
        "target": {k: profile.get(k) for k in
                   ("id", "label", "summary", "source", "checked",
                    "confidence")},
        "findings": findings,
        "counts": {
            "fail": sum(1 for f in findings if f["status"] == FAIL),
            "warn": sum(1 for f in findings if f["status"] == WARN),
            "pass": sum(1 for f in findings if f["status"] == PASS),
            "skip": sum(1 for f in findings if f["status"] == SKIP),
        },
        "timestamps": sorted({t for f in findings for t in f["timestamps"]}),
    }


def check(rule, facts, measurements, locator=None):
    """One rule against one file."""
    getter = METRICS.get(rule["metric"])
    value = getter(facts, measurements) if getter else None

    finding = {
        "id": rule["id"],
        "label": rule["label"],
        "metric": rule["metric"],
        "unit": rule.get("unit", ""),
        "severity": rule.get("severity", FAIL),
        "note": rule.get("note", ""),
        "basis": rule.get("basis"),
        "fix": rule.get("fix"),
        "value": value,
        "actual": format_value(value, rule.get("unit", "")),
        "required": describe(rule),
        "scope": scope_of(rule["metric"]),
        "status": SKIP,
        "timestamps": [],
        "intervals": [],
        "detail": "",
    }

    if getter is None:
        finding["detail"] = (f"This build does not know the metric "
                             f"'{rule['metric']}'.")
        return finding
    if value is None:
        finding["detail"] = "Not present in this file, so not checked."
        return finding

    status, detail = judge(rule, value)
    finding["status"] = status
    finding["detail"] = detail
    if status in (FAIL, WARN):
        finding["intervals"] = locate(rule, value, facts, measurements, locator)
        finding["timestamps"] = [i["start"] for i in finding["intervals"]]
    return finding


def judge(rule, value):
    """Where the value sits against the rule. Returns (status, detail)."""
    severity = rule.get("severity", FAIL)

    if "one_of" in rule:
        allowed = rule["one_of"]
        if value in allowed or str(value) in [str(a) for a in allowed]:
            return PASS, ""
        return severity, "Not one of: " + ", ".join(str(a) for a in allowed)

    if "equals" in rule:
        if value == rule["equals"]:
            return PASS, ""
        return severity, f"Expected {rule['equals']}."

    if rule.get("forbid"):
        return (severity, "Present.") if value else (PASS, "")

    if rule.get("require"):
        return (PASS, "") if value else (severity, "Absent.")

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return SKIP, "Not a number, so no band could be applied."
    if isinstance(value, float) and math.isnan(value):
        return SKIP, "Measured as not-a-number."

    low, high = rule.get("min"), rule.get("max")
    warn_low, warn_high = rule.get("warn_min"), rule.get("warn_max")

    if low is not None and value < low:
        return severity, f"Below the floor by {low - value:.2f}."
    if high is not None and value > high:
        return severity, f"Over the ceiling by {value - high:.2f}."
    if warn_low is not None and value < warn_low:
        return WARN, f"Inside the band, but {warn_low - value:.2f} under the "\
                     "comfortable range."
    if warn_high is not None and value > warn_high:
        return WARN, f"Inside the band, but {value - warn_high:.2f} over the "\
                     "comfortable range."
    return PASS, ""


def locate(rule, value, facts, measurements, locator=None):
    """Where in the file the rule is being broken, when that is knowable."""
    kind = LOCATABLE.get(rule["metric"])
    if kind is None:
        return []

    timeline = measurements.get("timeline") or []

    if kind == "short_term":
        return _runs(timeline, "short_term", rule.get("min"), rule.get("max"))

    if kind == "phase":
        return _runs(timeline, "phase", rule.get("min"), rule.get("max"))

    if kind == "true_peak":
        ceiling = rule.get("max")
        if ceiling is None:
            return []
        for row in timeline:
            if row.get("true_peak_dbfs") is not None and \
                    row["true_peak_dbfs"] > ceiling:
                return [{"start": float(row["t"]), "end": float(row["t"]) + 1,
                         "worst": row["true_peak_dbfs"],
                         "detail": "first moment the true peak passed the "
                                   "ceiling"}]
        return []

    if kind == "sample_peak":
        windows = measurements.get("peak_windows")
        if windows is None and locator is not None:
            threshold = rule.get("max")
            if threshold is None:
                threshold = measurements.get("settings", {}).get(
                    "clip_threshold_dbfs", -0.1)
            windows = locator(threshold)
            measurements["peak_windows"] = windows
        return list(windows or [])

    if kind in ("black", "frozen"):
        items = measurements.get(kind) or []
        metric = rule["metric"]
        if metric.startswith("leading"):
            items = [i for i in items if i.get("position") == "head"]
        elif metric.startswith("trailing"):
            items = [i for i in items if i.get("position") == "tail"]
        elif metric.startswith("longest") and rule.get("max") is not None:
            items = [i for i in items if i["duration"] > rule["max"]]
        return [dict(i) for i in items]

    if kind == "flash":
        return [dict(i) for i in (measurements.get("flashes") or [])]

    if kind == "caption_uncaptioned":
        return [dict(i) for i in
                (measurements.get("caption_uncaptioned_intervals") or [])]

    if kind == "caption_orphans":
        return [dict(i) for i in
                (measurements.get("caption_orphan_intervals") or [])]

    if kind == "caption_overlaps":
        return [dict(i) for i in
                (measurements.get("caption_overlap_intervals") or [])]

    if kind == "captions":
        # Which cues are at fault depends on the rule's own threshold, so the
        # test lives beside the cues rather than here.
        return captions.offending_cues(rule["metric"], rule,
                                       measurements.get("cues"))

    if kind == "silence":
        wanted = {"lead_silence_s": "head", "tail_silence_s": "tail",
                  "longest_mid_silence_s": "middle"}[rule["metric"]]
        out = []
        for gap in measurements.get("silences") or []:
            if gap["position"] != wanted:
                continue
            if wanted == "middle" and rule.get("max") is not None and \
                    gap["duration"] <= rule["max"]:
                continue
            out.append({"start": gap["start"], "end": gap["end"],
                        "worst": gap["duration"],
                        "detail": f"{gap['duration']:.2f} s"})
        return out

    return []


def _runs(timeline, key, minimum, maximum):
    """Contiguous seconds outside a band, as intervals."""
    runs, current = [], None
    for row in timeline:
        value = row.get(key)
        outside = value is not None and (
            (minimum is not None and value < minimum) or
            (maximum is not None and value > maximum))
        if outside:
            excess = max(
                (value - maximum) if maximum is not None else -math.inf,
                (minimum - value) if minimum is not None else -math.inf)
            if current is None:
                current = {"start": float(row["t"]), "end": float(row["t"]) + 1,
                           "worst": value, "excess": excess}
            else:
                current["end"] = float(row["t"]) + 1
                if excess > current["excess"]:
                    current["worst"], current["excess"] = value, excess
        elif current is not None:
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    # One second past the line is a transient every real recording contains.
    return [r for r in runs if (r["end"] - r["start"]) >= 2]


# ---------------------------------------------------------------- the set
#
# A delivery has properties no single file has. These metrics are written
# against the whole set, and their findings name the offending *files* the way
# a per-file finding names timestamps.

def _distinct(name):
    return lambda m: m.get(f"{name}_distinct")


def _spread(name):
    def read(m):
        block = m.get(name) or {}
        return block.get("spread")
    return read


SET_METRICS = {
    "set_file_count": lambda m: m.get("file_count"),
    "set_failing_files": lambda m: m.get("failing_files"),
    "set_channels_distinct": _distinct("channels"),
    "set_sample_rate_distinct": _distinct("sample_rate"),
    "set_codec_distinct": _distinct("codec"),
    "set_container_distinct": _distinct("container"),
    "set_bitrate_mode_distinct": _distinct("bitrate_mode"),
    "set_bit_depth_distinct": _distinct("bit_depth"),
    "set_loudness_spread_db": _spread("loudness"),
    "set_peak_spread_db": _spread("peak"),
    "set_total_duration_min": lambda m: (
        (m["total_duration_s"] / 60.0) if m.get("total_duration_s") else None),
    "set_longest_file_min": lambda m: (
        (m["longest_file_s"] / 60.0) if m.get("longest_file_s") else None),
}

# Which files a set finding should name.
SET_OFFENDERS = {
    "set_channels_distinct": "channels_odd",
    "set_sample_rate_distinct": "sample_rate_odd",
    "set_codec_distinct": "codec_odd",
    "set_container_distinct": "container_odd",
    "set_bitrate_mode_distinct": "bitrate_mode_odd",
    "set_bit_depth_distinct": "bit_depth_odd",
}


def evaluate_set(set_measurements, profile):
    """Run the profile's cross-file rules over a whole delivery."""
    rules = profile.get("set_rules") or []
    findings = [check_set(rule, set_measurements) for rule in rules]
    statuses = {f["status"] for f in findings}
    verdict = FAIL if FAIL in statuses else (WARN if WARN in statuses else PASS)
    return {
        "verdict": verdict,
        "findings": findings,
        "counts": {name: sum(1 for f in findings if f["status"] == name)
                   for name in (FAIL, WARN, PASS, SKIP)},
    }


def check_set(rule, set_measurements):
    """One cross-file rule against one delivery."""
    getter = SET_METRICS.get(rule["metric"])
    value = getter(set_measurements) if getter else None

    finding = {
        "id": rule["id"],
        "label": rule["label"],
        "metric": rule["metric"],
        "unit": rule.get("unit", ""),
        "severity": rule.get("severity", FAIL),
        "note": rule.get("note", ""),
        "basis": rule.get("basis"),
        "value": value,
        "actual": _set_actual(rule, value, set_measurements),
        "required": describe(rule),
        "status": SKIP,
        "files": [],
        "detail": "",
    }
    if getter is None:
        finding["detail"] = (f"This build does not know the set metric "
                             f"'{rule['metric']}'.")
        return finding
    if value is None:
        finding["detail"] = ("Not measurable across these files, so not "
                             "checked.")
        return finding

    status, detail = judge(rule, value)
    finding["status"] = status
    finding["detail"] = detail
    if status in (FAIL, WARN):
        finding["files"] = locate_set(rule, set_measurements)
    return finding


def _set_actual(rule, value, set_measurements):
    """A count of distinct values means nothing on its own; say what they are."""
    if value is None:
        return "—"
    key = SET_OFFENDERS.get(rule["metric"])
    if key and isinstance(value, (int, float)) and value > 1:
        name = key[:-4]
        groups = (set_measurements.get("groups") or {}).get(name) or {}
        shown = ", ".join(sorted(groups, key=lambda k: -len(groups[k]))[:4])
        return f"{int(value)} different: {shown}"
    return format_value(value, rule.get("unit", ""))


def locate_set(rule, set_measurements):
    """Which files are at fault, when that is answerable."""
    key = SET_OFFENDERS.get(rule["metric"])
    if key:
        return list(set_measurements.get(key) or [])
    if rule["metric"] == "set_loudness_spread_db":
        block = set_measurements.get("loudness") or {}
    elif rule["metric"] == "set_peak_spread_db":
        block = set_measurements.get("peak") or {}
    else:
        return []
    named = block.get("outliers")
    if named:
        return list(named)
    ends = [block.get("loudest"), block.get("quietest")]
    return [name for name in ends if name]


# ------------------------------------------------------------------ wording

def describe(rule):
    """The requirement, as it appears in the right-hand column of the report."""
    unit = rule.get("unit", "")
    suffix = f" {unit}" if unit else ""

    if "one_of" in rule:
        allowed = [str(a) for a in rule["one_of"]]
        if len(allowed) == 1:
            return allowed[0] + suffix
        return " or ".join([", ".join(allowed[:-1]), allowed[-1]]) + suffix
    if "equals" in rule:
        return f"{rule['equals']}{suffix}"
    if rule.get("forbid"):
        return "none"
    if rule.get("require"):
        return "required"

    low, high = rule.get("min"), rule.get("max")
    if low is not None and high is not None:
        return f"{low:g} to {high:g}{suffix}"
    if high is not None:
        return f"≤ {high:g}{suffix}"
    if low is not None:
        return f"≥ {low:g}{suffix}"
    return ""


def format_value(value, unit=""):
    if value is None:
        return "—"
    suffix = f" {unit}" if unit else ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if math.isinf(value):
            return "silent" if value < 0 else "∞"
        return f"{value:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value}{suffix}"


def timecode(seconds):
    """Seconds as HH:MM:SS, or MM:SS when the file is under an hour."""
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
