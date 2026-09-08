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

import chart
import checks

NAME = "Media Preflight"
VERSION = "0.1.0"
SCHEMA = 1

MARK = {"pass": "✓", "fail": "✕", "warn": "⚠", "skip": "·", "info": "i"}

# What a threshold rests on, said in front of the note rather than left for
# somebody to assume. Only the two that are not a specification say anything:
# a published figure needs no apology.
BASIS = {
    "observed": "(measured behaviour, not a published figure.)",
    "house": "(this tool's own threshold, not a rule of the target.)",
}
WORD = {"pass": "passes", "fail": "fails", "warn": "passes with a warning",
        "skip": "not checked", "info": "worth knowing"}

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
        "picture": _picture_summary(measurements),
        "corrections": corrections or [],
        "chapters": chart.chapters(facts.get("chapters"),
                                   measurements.get("timeline")),
        "band": chart.band_for(profile),
        "timeline": chart.reduce(measurements.get("timeline")),
        "caption_track": chart.caption_coverage(measurements.get("cues")),
        "events": chart.events(measurements, result["findings"]),
    }


def _measurement_summary(m):
    """The measured numbers, without the second-by-second timeline.

    The full timeline is thousands of rows. A reduced one lives at the top of
    the envelope, next to the things that draw it; this block is the scalars.
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


# What the picture pass read, and what it found. Kept apart from the audio
# scalars because the two passes answer to different filters and because this
# block is the only place a measurement nobody checked can be seen: a target
# that says nothing about black frames still leaves somebody wanting to know
# there are ninety seconds of them at the end.
PICTURE_FIELDS = (
    ("black_seconds", "Black", "s"),
    ("longest_black_s", "Longest black run", "s"),
    ("leading_black_s", "Black at the head", "s"),
    ("trailing_black_s", "Black at the tail", "s"),
    ("frozen_seconds", "Frozen", "s"),
    ("longest_frozen_s", "Longest frozen run", "s"),
    ("flash_regions", "Flashing passages", ""),
    ("interlace_detected", "Fields", ""),
    ("telecine_ratio", "Repeated fields", ""),
)


def _picture_summary(m):
    """None when no picture pass ran. Otherwise what it was asked to read."""
    if not m.get("picture_filters"):
        return None
    out = {"filters": list(m["picture_filters"]),
           "width_divide": m.get("width_divide", 1),
           "frames_measured": m.get("frames_measured")}
    for key, _label, _unit in PICTURE_FIELDS:
        out[key] = _plain(m.get(key))
    return out


def picture_lines(report):
    """The picture numbers no finding already reports.

    A measurement the target checked is already on the page above with a
    verdict beside it; repeating it here without one would be worse than
    silence. What is left is the reason somebody asked for the full pass.
    """
    picture = report.get("picture")
    if not picture:
        return []
    checked = {f.get("metric") for f in report.get("findings", [])
               if f.get("status") != "skip"}
    rows = []
    for key, label, unit in PICTURE_FIELDS:
        if key in checked:
            continue
        value = picture.get(key)
        if value is None:
            continue
        rows.append("  %-24s %s%s" % (label, value, unit))
    if not rows:
        return []
    head = "Also measured in the picture, against nothing:"
    return [head] + rows


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


def chart_svg(report, theme="light", baseline=None):
    """The loudness chart for a finished report, or '' when there is none.

    ``baseline`` is an earlier report of the same file — the reading taken
    before a correction — drawn faintly behind, so that what changed is
    visible and not only listed.

    Exports default to light: an SVG loaded through an ``<img>`` resolves
    prefers-color-scheme against the reader's operating system rather than the
    document it sits in, so a themed chart lands dark inside a light report on
    anybody whose laptop is in dark mode. Only a page that inlines the drawing
    and controls its own background asks for 'auto'.
    """
    return chart.svg(report.get("timeline"), report.get("band"),
                     report.get("events"), report.get("chapters"),
                     duration=report["file"].get("duration_s"),
                     title=f"Loudness over time — {report['file'].get('name')}",
                     theme=theme, caption_runs=report.get("caption_track"),
                     baseline=(baseline or {}).get("timeline"),
                     baseline_duration=(baseline or {}).get(
                         "file", {}).get("duration_s"))


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

    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "skip": 4}
    findings = sorted(report["findings"],
                      key=lambda f: order.get(f["status"], 5))
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

    measured = picture_lines(report)
    if measured:
        lines.append("")
        lines.extend(measured)

    picture = chart.strip(report.get("timeline"), report.get("band"),
                          report.get("events"))
    if picture:
        lines.append("")
        lines.extend(picture)

    if report.get("chapters"):
        lines.append("")
        lines.extend(_chapter_lines(report["chapters"], width))

    stamps = _all_timestamps(report)
    if stamps:
        lines.append("")
        shown = ", ".join(checks.timecode(t) for t in stamps[:12])
        more = "" if len(stamps) <= 12 else f", and {len(stamps) - 12} more"
        lines.append(f"Problems occur at {shown}{more}")

    notes = [f for f in findings
             if (f["note"] or BASIS.get(f.get("basis")))
             and f["status"] in ("fail", "warn")]
    if notes:
        lines.append("")
        for finding in notes:
            said = " ".join(x for x in (BASIS.get(finding.get("basis")),
                                        finding["note"]) if x)
            lines.append(f"{finding['label']}: {_wrap(said, width, 2)}")

    if target.get("source"):
        lines.append("")
        lines.append(f"Thresholds from: {target['source']}"
                     + (f" (read {target['checked']})"
                        if target.get("checked") else ""))
    return "\n".join(lines) + "\n"


def _chapter_lines(chapters, width):
    """Loudest short-term per chapter — the column that finds the odd one out."""
    lines = ["Chapters, by loudest short-term loudness:"]
    known = sorted(c["loudest_short_term"] for c in chapters
                   if c["loudest_short_term"] is not None)
    # Only point at a chapter that actually stands out. Marking the loudest of
    # a set that agrees within a decibel would be pointing at nothing.
    outlier = None
    if len(known) > 2:
        middle = known[len(known) // 2]
        if known[-1] - middle >= 1.0:
            outlier = known[-1]
    for chapter in chapters:
        value = chapter["loudest_short_term"]
        shown = "—" if value is None else f"{value:.1f} LUFS"
        title = chapter["title"][:32]
        mark = ("  ←" if outlier is not None and value is not None
                and value >= outlier - 0.05 else "")
        left = f"  {checks.timecode(chapter['start_s'])}  {title}"
        lines.append(left + " " * max(2, width - len(left) - len(shown)
                                      - len(mark)) + shown + mark)
    lines.append("  Short-term, not integrated: integrated loudness is gated "
                 "over a whole")
    lines.append("  programme and cannot be re-derived per chapter from "
                 "per-second values.")
    return lines


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

def markdown(report, chart_name=None):
    """The version that gets sent to somebody who did not run it.

    ``chart_name`` is the filename of a sibling SVG the caller has written.
    The chart is referenced rather than inlined because an inline ``<svg>`` is
    stripped by most things that render Markdown, and a broken picture is
    worse than a link to a working one.
    """
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

    if chart_name:
        out.append(f"![Loudness over time]({chart_name})")
        out.append("")
        band = report.get("band") or {}
        if band.get("absent"):
            out.append(f"*No target band is drawn: {band['absent']}*")
            out.append("")

    out.append("## Results")
    out.append("")
    out.append("| | Check | Measured | Required |")
    out.append("|---|---|---|---|")
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "skip": 4}
    for finding in sorted(report["findings"],
                          key=lambda f: order.get(f["status"], 5)):
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

    if report.get("chapters"):
        out.append("## Chapters")
        out.append("")
        out.append("| # | Chapter | Starts | Loudest short-term |")
        out.append("|---|---|---|---|")
        for chapter in report["chapters"]:
            value = chapter["loudest_short_term"]
            out.append("| {} | {} | {} | {} |".format(
                chapter["number"], chapter["title"] or "—",
                checks.timecode(chapter["start_s"]),
                "—" if value is None else f"{value:.1f} LUFS"))
        out.append("")
        out.append("Short-term rather than integrated: integrated loudness is "
                   "gated over a whole programme and cannot be re-derived per "
                   "chapter from per-second values. Measuring it properly "
                   "would mean one decode per chapter.")
        out.append("")

    notes = [f for f in report["findings"]
             if (f["note"] or BASIS.get(f.get("basis")))
             and f["status"] in ("fail", "warn")]
    if notes:
        out.append("## Notes")
        out.append("")
        for finding in notes:
            said = " ".join(x for x in (BASIS.get(finding.get("basis")),
                                        finding["note"]) if x)
            out.append(f"- **{finding['label']}**: {said}")
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


# ------------------------------------------------------------------- the set

def set_envelope(result):
    """One structure for a whole delivery, shaped like the single-file one."""
    profile = result["profile"]
    measurements = result["set_measurements"]
    return {
        "schema": SCHEMA,
        "tool": {"name": NAME, "version": VERSION},
        "generated": datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "target": {k: profile.get(k) for k in
                   ("id", "label", "summary", "source", "checked",
                    "confidence")},
        "verdict": result["verdict"],
        "set": {
            "verdict": result["set_result"]["verdict"],
            "counts": result["set_result"]["counts"],
            "findings": result["set_result"]["findings"],
        },
        "measurements": _set_summary(measurements),
        "band": chart.band_for(profile),
        # Which loudness the files are compared in — LUFS for a target written
        # in LUFS, RMS for one written in RMS. Every renderer reads it from
        # here rather than guessing.
        "loudness_metric": (measurements.get("loudness") or {}).get("metric"),
        "loudness_unit": (measurements.get("loudness") or {}).get("unit"),
        "files": [_file_summary(entry) for entry in result["files"]],
        "unreadable": result["unreadable"],
    }


def _set_summary(measurements):
    """The delivery's own numbers, without the per-file bulk repeated twice."""
    keep = ("file_count", "failing_files", "warning_files",
            "total_duration_s", "longest_file_s", "groups")
    out = {k: _plain(measurements[k]) for k in keep if k in measurements}
    for name in ("loudness", "peak"):
        block = dict(measurements.get(name) or {})
        block.pop("per_file", None)
        out[name] = block
    for key, value in measurements.items():
        if key.endswith("_distinct") or key.endswith("_odd"):
            out[key] = value
    return out


def _file_summary(entry):
    """What a delivery report needs about one of its files.

    The whole per-file envelope would be accurate and unreadable — thirty of
    them is a megabyte of JSON nobody scrolls through. This is the verdict, the
    numbers somebody compares between files, and the checks that did not pass.
    """
    envelope = entry["envelope"]
    findings = [f for f in envelope["findings"]
                if f["status"] in ("fail", "warn")]
    return {
        "name": entry["name"],
        "path": entry["path"],
        "verdict": envelope["verdict"],
        "counts": envelope["counts"],
        "duration_s": envelope["file"].get("duration_s"),
        "audio": envelope["file"].get("audio"),
        "measurements": {k: envelope["measurements"].get(k) for k in
                         ("integrated_lufs", "rms_dbfs", "true_peak_dbfs",
                          "peak_dbfs")},
        "problems": [{"id": f["id"], "label": f["label"], "status": f["status"],
                      "actual": f["actual"], "required": f["required"],
                      "timestamps": f["timestamps"]} for f in findings],
    }


def set_chart_svg(result, theme="light"):
    """One bar per file against the target band, or '' when there is nothing."""
    return chart.set_svg((result["set_measurements"].get("loudness") or {}),
                         chart.band_for(result["profile"]), theme=theme,
                         title="Loudness across the delivery")


def set_text(envelope, width=68, show_passes=False):
    """The delivery report, for a terminal."""
    lines = []
    target = envelope["target"]
    counts = envelope["set"]["counts"]
    files = envelope["files"]

    lines.append(f"{len(files)} files — {target.get('label', target.get('id'))}")
    if envelope["unreadable"]:
        lines.append(f"{len(envelope['unreadable'])} could not be read")
    lines.append("")

    verdict = envelope["verdict"]
    failing = sum(1 for f in files if f["verdict"] == "fail")
    warning = sum(1 for f in files if f["verdict"] == "warn")
    lines.append(f"{MARK[verdict]} {VERDICT_LINE[verdict]}")
    own = []
    if counts["fail"]:
        own.append(f"{counts['fail']} failure" + ("" if counts["fail"] == 1
                                                  else "s"))
    if counts["warn"]:
        own.append(f"{counts['warn']} warning" + ("" if counts["warn"] == 1
                                                  else "s"))
    tail = (" and ".join(own) + " of its own." if own
            else "nothing wrong of its own.")
    lines.append(f"  {failing} of {len(files)} files fail, {warning} warn; "
                 f"the delivery itself has {tail}")
    lines.append("")

    lines.append("Across the delivery")
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "skip": 4}
    shown = 0
    for finding in sorted(envelope["set"]["findings"],
                          key=lambda f: order.get(f["status"], 5)):
        if finding["status"] in ("pass", "skip") and not show_passes:
            continue
        shown += 1
        lines.append("  " + _finding_line(finding, width - 2))
        if finding["files"]:
            named = ", ".join(finding["files"][:5])
            more = ("" if len(finding["files"]) <= 5
                    else f", +{len(finding['files']) - 5} more")
            lines.append(f"      {named}{more}")
    if not shown:
        lines.append("  ✓ Nothing wrong with the delivery as a whole.")
    lines.append("")

    lines.append("Files")
    for entry in files:
        lines.append("  " + _file_line(entry, width - 2,
                                       envelope.get("loudness_metric"),
                                       envelope.get("loudness_unit")))
    for entry in envelope["unreadable"]:
        lines.append(f"  ✕ {entry['name']}: {entry['error']}")

    notes = [f for f in envelope["set"]["findings"]
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


def _file_line(entry, width, metric=None, unit=None):
    """A file per line: what it is, and either its level or what is wrong."""
    mark = MARK[entry["verdict"]]
    duration = checks.timecode(entry["duration_s"])
    left = f"{mark} {entry['name']}"
    problems = [p["label"] for p in entry["problems"]
                if p["status"] == "fail"] or \
               [p["label"] for p in entry["problems"]]
    if problems:
        right = ", ".join(problems[:2])
        if len(problems) > 2:
            right += f", +{len(problems) - 2}"
    else:
        right = _loudness_of(entry, metric, unit)
    right = f"{duration}  {right}".strip()
    pad = max(2, width - len(left) - len(right))
    return left + " " * pad + right


def _loudness_of(entry, metric=None, unit=None):
    """A file's level, in the quantity the target states rather than whichever
    one happens to have been measured."""
    order = ([metric] if metric else []) + ["integrated_lufs", "rms_dbfs"]
    units = {"integrated_lufs": "LUFS", "rms_dbfs": "dBFS"}
    for name in order:
        value = entry["measurements"].get(name)
        if isinstance(value, (int, float)):
            return f"{value:.1f} {unit if name == metric and unit else units.get(name, '')}".strip()
    return ""


def set_markdown(envelope, chart_name=None):
    """The delivery report somebody sends on."""
    target = envelope["target"]
    files = envelope["files"]
    out = [f"# Delivery report — {len(files)} files", ""]
    out.append(f"**{VERDICT_LINE[envelope['verdict']]}**")
    out.append("")
    out.append(f"- Target: {target.get('label')}")
    out.append(f"- Checked: {envelope['generated']}")
    out.append(f"- Tool: {NAME} {VERSION}")
    out.append("")

    if chart_name:
        out.append(f"![Loudness across the delivery]({chart_name})")
        out.append("")

    out.append("## Across the delivery")
    out.append("")
    out.append("| | Check | Measured | Required | Files |")
    out.append("|---|---|---|---|---|")
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "skip": 4}
    for finding in sorted(envelope["set"]["findings"],
                          key=lambda f: order.get(f["status"], 5)):
        named = ", ".join(finding["files"][:6]) or "—"
        out.append("| {} | {} | {} | {} | {} |".format(
            MARK.get(finding["status"], "·"), finding["label"],
            finding["actual"], finding["required"] or "—", named))
    out.append("")

    out.append("## Files")
    out.append("")
    out.append("| | File | Length | Loudness | Problems |")
    out.append("|---|---|---|---|---|")
    for entry in files:
        shown = _loudness_of(entry, envelope.get("loudness_metric"),
                             envelope.get("loudness_unit")) or "—"
        problems = "; ".join(f"{p['label']} ({p['actual']})"
                             for p in entry["problems"]) or "—"
        out.append("| {} | `{}` | {} | {} | {} |".format(
            MARK[entry["verdict"]], entry["name"],
            checks.timecode(entry["duration_s"]), shown, problems))
    out.append("")

    if envelope["unreadable"]:
        out.append("## Could not be read")
        out.append("")
        for entry in envelope["unreadable"]:
            out.append(f"- `{entry['name']}` — {entry['error']}")
        out.append("")

    notes = [f for f in envelope["set"]["findings"]
             if f["note"] and f["status"] in ("fail", "warn")]
    if notes:
        out.append("## Notes")
        out.append("")
        for finding in notes:
            out.append(f"- **{finding['label']}**: {finding['note']}")
        out.append("")
    return "\n".join(out) + "\n"
