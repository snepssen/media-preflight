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
    "silent_channel_count": lambda f, m: float(len(m.get("silent_channels") or [])),
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
}


# Metrics that come out of the file's header rather than out of its audio.
# A header field has no "when": saying so keeps the report from offering an
# apology for the absence of a timestamp on a sample rate.
DECLARED_METRICS = {
    "audio_codec", "sample_rate", "channels", "audio_bitrate_kbps",
    "bitrate_mode", "container", "bit_depth", "cover_art", "duration_s",
    "duration_min", "av_duration_gap_s",
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
