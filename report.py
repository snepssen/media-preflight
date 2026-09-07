"""Saying what was found, in three registers.

``text`` is the terminal and the app window. ``markdown`` is the thing you send
a client, which has to stand on its own without the person who ran it in the
room. ``data`` is JSON, which is the one that has to stay stable, because it is
what somebody's build script will read.

The rule shared by all three: state the measurement, state the requirement, and
never round a number in a direction that flatters the file.
"""

from __future__ import annotations

import datetime
import json

import checks

NAME = "Media Preflight"
VERSION = "0.1.0"
SCHEMA = 1

MARK = {"pass": "✓", "fail": "✕", "warn": "⚠", "skip": "·"}
WORD = {"pass": "passes", "fail": "fails", "warn": "passes with a warning",
        "skip": "not checked"}

VERDICT_LINE = {
    "pass": "Ready to deliver.",
    "warn": "Deliverable, with things worth looking at.",
    "fail": "Not ready — the failures below would be rejected.",
}


def envelope(facts, measurements, result, profile, corrections=None):
    """The one structure the three renderers and the recipe are built from."""
    audio = facts.get("audio") or {}
    video = facts.get("video") or {}
    return {
        "schema": SCHEMA,
        "tool": {"name": NAME, "version": VERSION},
        "generated": datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "file": {
            "name": facts.get("name"),
            "path": facts.get("path"),
            "size_bytes": facts.get("size_bytes"),
            "container": facts.get("container", {}).get("format_name"),
            "duration_s": facts.get("container", {}).get("duration_s"),
            "audio": {
                "codec": audio.get("codec"),
                "sample_rate": audio.get("sample_rate"),
                "channels": audio.get("channels"),
                "bit_rate": audio.get("bit_rate"),
                "bit_rate_from_container": audio.get("bit_rate_from_container",
                                                     False),
            } if audio else None,
            "video": {
                "codec": video.get("codec"),
                "width": video.get("width"),
                "height": video.get("height"),
                "frame_rate": video.get("avg_frame_rate"),
            } if video else None,
            "cover_art": facts.get("cover_art"),
            "captions": {
                "format": measurements.get("caption_format"),
                "origin": measurements.get("caption_origin"),
                "source": measurements.get("caption_source"),
                "cues": measurements.get("caption_cue_count"),
            } if measurements.get("caption_cue_count") is not None else None,
        },
        "target": result["target"],
        "verdict": result["verdict"],
        "counts": result["counts"],
        "findings": result["findings"],
        "measurements": _measurement_summary(measurements),
        "corrections": corrections or [],
    }


def _measurement_summary(m):
    """The measured numbers, without the second-by-second timeline.

    The timeline is thousands of rows and nothing downstream reads it; the
    findings already carry the intervals that matter.
    """
    keep = ("integrated_lufs", "loudness_range_lu", "true_peak_dbfs",
            "peak_dbfs", "rms_dbfs", "rms_peak_dbfs", "rms_trough_dbfs",
            "noise_floor_dbfs", "dc_offset", "flat_factor", "bit_depth",
            "lead_silence_s", "tail_silence_s", "final_momentary_lufs",
            "ends_abruptly", "channel_rms_spread_db", "silent_channels",
            "phase_min", "duration_s", "bitrate_mode", "clipping_seconds",
            "clipped_samples", "settings")
    out = {k: _plain(m[k]) for k in keep if k in m}
    out["channels"] = [{k: _plain(v) for k, v in c.items()}
                       for c in (m.get("channels") or [])]
    out["silences"] = m.get("silences") or []
    return out


def _plain(value):
    """JSON has no infinity. Digital silence is a real measurement, so it is
    written as the string it reads as rather than dropped."""
    if isinstance(value, float):
        if value != value:
            return None
        if value == float("inf"):
            return "+inf"
        if value == float("-inf"):
            return "-inf"
    return value


def jsonable(value):
    """Make a structure safe to serialise.

    JSON has no infinity and no not-a-number, and Python's json module writes
    ``-Infinity`` regardless — which every strict reader, the browser included,
    then refuses. Digital silence measures as -inf and is a real result, so it
    is converted to a string that says so rather than dropped.
    """
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return _plain(value)


def data(report):
    return json.dumps(jsonable(report), indent=2) + "\n"


# --------------------------------------------------------------------- text

def text(report, width=68, show_passes=True):
    """The report as it appears in the terminal and the app window."""
    lines = []
    file_info = report["file"]
    target = report["target"]

    lines.append(file_info["name"])
    lines.append(_stream_line(file_info))
    lines.append("")
    lines.append(f"Target: {target.get('label', target.get('id'))}")
    if target.get("confidence") == "informal":
        lines.append("        (this target is not a published specification — "
                     "see the note below)")
    lines.append("")

    verdict = report["verdict"]
    counts = report["counts"]
    lines.append(f"{MARK[verdict]} {VERDICT_LINE[verdict]}")
    lines.append(f"  {counts['fail']} failed, {counts['warn']} warned, "
                 f"{counts['pass']} passed, {counts['skip']} not checked.")
    lines.append("")

    order = {"fail": 0, "warn": 1, "pass": 2, "skip": 3}
    findings = sorted(report["findings"], key=lambda f: order[f["status"]])
    for finding in findings:
        if finding["status"] == "skip":
            continue
        if finding["status"] == "pass" and not show_passes:
            continue
        lines.append(_finding_line(finding, width))
        if finding["status"] in ("fail", "warn"):
            for extra in _finding_detail(finding, width):
                lines.append(extra)

    # Checks that had nothing to measure get one line between them rather than
    # one line each. A video target run against an audio file would otherwise
    # bury four real findings under eight dashes.
    skipped = [f for f in findings if f["status"] == "skip"]
    if skipped:
        names = ", ".join(f["label"].lower() for f in skipped[:6])
        more = "" if len(skipped) <= 6 else f", and {len(skipped) - 6} more"
        lines.append(f"· {len(skipped)} not checked — nothing in this file to "
                     f"measure them against: {names}{more}")

    stamps = _all_timestamps(report)
    if stamps:
        lines.append("")
        shown = ", ".join(checks.timecode(t) for t in stamps[:12])
        more = "" if len(stamps) <= 12 else f", and {len(stamps) - 12} more"
        lines.append(f"Problems occur at {shown}{more}")

    notes = [f for f in findings
             if f["note"] and f["status"] in ("fail", "warn")]
    if notes:
        lines.append("")
        for finding in notes:
            lines.append(f"{finding['label']}: {_wrap(finding['note'], width, 2)}")

    if target.get("source"):
        lines.append("")
        lines.append(f"Thresholds from: {target['source']}"
                     + (f" (read {target['checked']})"
                        if target.get("checked") else ""))
    return "\n".join(lines) + "\n"


def _stream_line(file_info):
    bits = []
    if file_info.get("duration_s"):
        bits.append(checks.timecode(file_info["duration_s"]))
    caption = file_info.get("captions") or {}
    # A caption file checked on its own has the caption format as its
    # container, and printing it twice reads as a stutter.
    caption_only = not file_info.get("audio") and not file_info.get("video")
    if file_info.get("container") and not caption_only:
        bits.append(file_info["container"])
    audio = file_info.get("audio")
    if audio:
        rate = f"{audio['sample_rate'] / 1000:g} kHz" if audio.get("sample_rate") else ""
        channels = {1: "mono", 2: "stereo"}.get(audio.get("channels"),
                                                f"{audio.get('channels')} ch")
        bitrate = (f"{audio['bit_rate'] / 1000:.0f} kbps"
                   if audio.get("bit_rate") else "")
        bits.append(" ".join(x for x in [audio.get("codec"), rate, channels,
                                         bitrate] if x))
    if caption and caption.get("cues") is not None:
        where = {"sidecar": "sidecar", "embedded": "embedded"}.get(
            caption.get("origin"), "")
        count = caption["cues"]
        bits.append(" ".join(x for x in [
            caption.get("format"),
            f"{count} cue" + ("" if count == 1 else "s"), where] if x))
    video = file_info.get("video")
    if video:
        size = f"{video['width']}x{video['height']}" if video.get("width") else ""
        fps = f"{video['frame_rate']:.3f} fps".replace(".000 ", " ") \
            if video.get("frame_rate") else ""
        bits.append(" ".join(x for x in [video.get("codec"), size, fps] if x))
    return "  ".join(b for b in bits if b)


def _finding_line(finding, width):
    mark = MARK[finding["status"]]
    left = f"{mark} {finding['label']}: {finding['actual']}"
    right = ""
    if finding["required"] and finding["status"] != "skip":
        right = f"Required: {finding['required']}"
    pad = max(1, width - len(left) - len(right))
    return left + " " * pad + right


def _finding_detail(finding, width):
    out = []
    if finding["intervals"]:
        spans = []
        for interval in finding["intervals"][:6]:
            start = checks.timecode(interval["start"])
            detail = interval.get("detail", "")
            spans.append(f"{start} ({detail})" if detail else start)
        more = ("" if len(finding["intervals"]) <= 6
                else f", +{len(finding['intervals']) - 6} more")
        out.append("    at " + ", ".join(spans) + more)
    elif finding.get("scope") == "file":
        out.append("    measured across the whole file — there is no single "
                   "moment to point at")
    return out


def _all_timestamps(report):
    stamps = sorted({t for f in report["findings"]
                     if f["status"] in ("fail", "warn")
                     for t in f["timestamps"]})
    return stamps


def _wrap(sentence, width, indent):
    words, lines, current = sentence.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width - indent:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    lines.append(current)
    return ("\n" + " " * indent).join(lines)


# ----------------------------------------------------------------- markdown

def markdown(report):
    """The version that gets sent to somebody who did not run it."""
    file_info = report["file"]
    target = report["target"]
    out = [f"# Preflight report — {file_info['name']}", ""]
    out.append(f"**{VERDICT_LINE[report['verdict']]}**")
    out.append("")
    out.append(f"- Target: {target.get('label')}")
    out.append(f"- Checked: {report['generated']}")
    out.append(f"- File: `{file_info['name']}` — {_stream_line(file_info)}")
    out.append(f"- Tool: {NAME} {VERSION}")
    out.append("")

    out.append("## Results")
    out.append("")
    out.append("| | Check | Measured | Required |")
    out.append("|---|---|---|---|")
    order = {"fail": 0, "warn": 1, "pass": 2, "skip": 3}
    for finding in sorted(report["findings"], key=lambda f: order[f["status"]]):
        out.append("| {} | {} | {} | {} |".format(
            MARK[finding["status"]], finding["label"],
            finding["actual"], finding["required"] or "—"))
    out.append("")

    located = [f for f in report["findings"] if f["intervals"]]
    if located:
        out.append("## Where")
        out.append("")
        for finding in located:
            spans = ", ".join(
                f"{checks.timecode(i['start'])}–{checks.timecode(i['end'])}"
                for i in finding["intervals"][:20])
            more = ("" if len(finding["intervals"]) <= 20
                    else f" (+{len(finding['intervals']) - 20} more)")
            out.append(f"- **{finding['label']}** — {spans}{more}")
        out.append("")

    notes = [f for f in report["findings"]
             if f["note"] and f["status"] in ("fail", "warn")]
    if notes:
        out.append("## Notes")
        out.append("")
        for finding in notes:
            out.append(f"- **{finding['label']}**: {finding['note']}")
        out.append("")

    if report.get("corrections"):
        out.append("## Proposed corrections")
        out.append("")
        for step in report["corrections"]:
            out.append(f"- {step['description']}")
        out.append("")
        out.append("Corrections are written to a new file. The source is never "
                   "modified.")
        out.append("")

    out.append("## How these numbers were measured")
    out.append("")
    out.append(f"Loudness is measured to ITU-R BS.1770 by ffmpeg's `ebur128` "
               f"filter; peak and channel statistics by `astats`; silence by "
               f"`silencedetect`. Thresholds for this target come from "
               f"{target.get('source', 'the profile')}"
               + (f", read {target['checked']}" if target.get("checked") else "")
               + ".")
    if target.get("confidence") == "informal":
        out.append("")
        out.append("> This target is **not** a published specification. The "
                   "thresholds are widely reported figures, offered as a "
                   "sanity check rather than as a statement of what the "
                   "platform does today.")
    return "\n".join(out) + "\n"
