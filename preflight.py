#!/usr/bin/env python3
"""Media Preflight — will this file be accepted, and can it be fixed safely?

    python3 preflight.py check  finished.mp3 --target acx
    python3 preflight.py batch   chapters/    --target acx
    python3 preflight.py fix    finished.mp3 --target acx --dry-run
    python3 preflight.py fix    finished.mp3 --target acx
    python3 preflight.py targets

`check` measures and reports one file; `batch` does the same for a delivery and
then checks the properties the set has and no single file does — every file
sharing a sample rate, say, which is a thing ACX requires and a per-file rule
cannot express. `fix` writes a corrected copy beside the source
and then measures *that*, so what it tells you about the new file is a
measurement rather than an intention. Nothing writes over the original, ever.

Exit codes: 0 the file passes, 1 it fails, 2 the tool could not run.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analysis
import batch
import captions
import checks
import corrections
import platform_support
import probe
import profiles
import report
import video


class PreflightError(RuntimeError):
    """Anything the person running this needs to read as a sentence."""


# Decoding a ninety-minute film to measure black frames is minutes of
# somebody's time, so the picture pass happens only when the target actually
# asks a question about the picture — and then it carries only the filters
# those questions need. video.FILTER_METRICS holds the mapping,
# picture_filters applies it. Interlacing is measured rather than read off the
# header, which is the expensive half of the pass and the point of it, because
# the header is a claim and on this question it is a claim that is often wrong.
#
# How much of the picture to read.
#   selective — only the filters the target's rules actually read. The default.
#   full      — every picture measurement, whether or not anything checks it.
# Full exists because "the target does not ask" and "the file is fine" are
# different sentences, and somebody handing over a master may want both
# answered. It costs what it costs; see picture_plan.
DEPTHS = ("selective", "full")
CAPTION_METRICS = {name for name in checks.METRICS if name.startswith("caption_")}


def run(path, target="web", ffmpeg=None, ffprobe=None, progress=None,
        caption_path=None, stage=None, depth="selective", width_divide=None):
    """Measure one file against one target. Returns the report envelope.

    ``stage`` is called with 'container', 'audio', 'video', 'captions' or
    'target' as each begins, so a window can show which part of the file is
    being read rather than a bar with no subject.

    ``depth`` is 'selective' or 'full'; see DEPTHS.
    """
    if ffmpeg is None or ffprobe is None:
        ffmpeg, ffprobe = platform_support.require_tools()
    profile = profiles.get(target) if not isinstance(target, dict) else target
    announce = stage or (lambda name: None)

    if is_caption_file(path) or caption_path and path == caption_path:
        announce("captions")
        return _run_caption_file(path, profile)

    announce("container")
    facts = probe.inspect(path, ffprobe)
    if not facts.get("audio") and not facts.get("video"):
        raise PreflightError(
            f"{facts['name']} carries neither audio nor picture that this "
            f"tool can measure.")

    audio = facts.get("audio") or {}
    duration = facts["container"]["duration_s"] or audio.get("duration_s")

    picture = picture_filters(profile, depth) if facts.get("video") else set()
    # Checked here rather than where it is used: a request that cannot be
    # honoured should be refused before the audio pass spends minutes on a
    # job that is going to end in an error message anyway.
    video.width_divisor(picture, width_divide)
    wants_video = bool(picture)
    audio_share = 0.5 if wants_video else 1.0

    measurements = {"settings": dict(analysis.DEFAULTS)}
    if audio:
        announce("audio")
        measurements = analysis.analyse(
            path, ffmpeg=ffmpeg, channels=audio.get("channels", 2),
            duration_s=duration, options=profile.get("options"),
            progress=_scaled(progress, 0.0, audio_share))
        _add_declared_extras(path, facts, measurements, profile, ffprobe)
        _add_clipping(path, facts, measurements, ffmpeg)

    if wants_video:
        announce("video")
        measurements.update(_video_measurements(
            path, facts, profile, ffmpeg, duration,
            _scaled(progress, audio_share, 1.0), picture, width_divide))
    if _needs(profile, {"frame_rate_mode"}) and facts.get("video"):
        measurements["frame_rate_mode"] = video.frame_rate_mode(
            path, duration, ffprobe)
    if _needs(profile, CAPTION_METRICS):
        announce("captions")
        _add_captions(path, facts, measurements, caption_path, ffmpeg,
                      duration, profile)

    announce("target")

    def locator(threshold_dbfs):
        return analysis.locate_peaks(
            path, threshold_dbfs, ffmpeg=ffmpeg,
            sample_rate=audio.get("sample_rate") or 48000)

    result = checks.evaluate(facts, measurements, profile, locator)
    return facts, measurements, result, profile


def is_caption_file(path):
    return os.path.splitext(path)[1].lstrip(".").lower() in \
        platform_support.CAPTION_EXTS


def _needs(profile, metrics):
    return any(rule["metric"] in metrics for rule in profile.get("rules", []))


def picture_filters(profile, depth="selective"):
    """Which picture filters this job needs. Empty means skip the pass."""
    if depth not in DEPTHS:
        raise PreflightError(
            "Depth is %s, not %r." % (" or ".join(DEPTHS), depth))
    if depth == "full":
        return set(video.ALL_FILTERS)
    return {name for name, metrics in video.FILTER_METRICS.items()
            if _needs(profile, metrics)}


def picture_plan(path, target="web", depth="selective", ffprobe=None,
                 facts=None):
    """What the picture pass will read, and roughly how long it will take.

    Returned before anything is decoded so the choice between depths can be
    made with a number attached. Everything in it is an estimate except the
    filter list, which is exact.
    """
    profile = profiles.get(target) if not isinstance(target, dict) else target
    if facts is None:
        if ffprobe is None:
            _, ffprobe = platform_support.require_tools()
        facts = probe.inspect(path, ffprobe)
    stream = facts.get("video") or {}
    if not stream:
        return None

    duration = facts["container"]["duration_s"] or stream.get("duration_s")
    plans = {}
    for name in DEPTHS:
        filters = picture_filters(profile, name)
        plans[name] = {
            "filters": sorted(filters),
            "seconds": video.estimate_seconds(
                filters, duration, stream.get("width"), stream.get("height"),
                stream.get("avg_frame_rate")) if filters else 0.0,
        }
    plans["chosen"] = depth
    plans["duration_s"] = duration
    return plans


def _scaled(progress, start, end):
    """A progress callback covering one slice of the whole job."""
    if progress is None:
        return None
    return lambda fraction: progress(start + fraction * (end - start))


def _video_measurements(path, facts, profile, ffmpeg, duration, progress,
                        filters=None, width_divide=None):
    out = video.analyse(path, ffmpeg=ffmpeg, duration_s=duration,
                        options=profile.get("options"), progress=progress,
                        filters=filters, width_divide=width_divide)
    # The audio pass already owns "settings"; the picture pass keeps its own
    # under a name of its own rather than overwriting it.
    out["video_settings"] = out.pop("settings", {})
    return out


def _add_captions(path, facts, measurements, caption_path, ffmpeg, duration,
                  profile=None):
    """Attach a caption track when there is one to attach. Absence is not a fault."""
    try:
        track = captions.find(path, facts, caption_path, ffmpeg)
    except captions.CaptionError as error:
        measurements["caption_error"] = str(error)
        return
    if track:
        measurements.update(captions.measure(track, duration))
        # Where the captions are, against where the sound is. The audio pass
        # already measured the silence, so this costs nothing to ask.
        measurements.update(captions.align(
            measurements.get("cues"), measurements.get("silences"),
            duration, (profile or {}).get("options")))


def _run_caption_file(path, profile):
    """A subtitle file checked on its own, with no media beside it."""
    track = captions.load(path)
    facts = {
        "path": os.path.abspath(path), "name": os.path.basename(path),
        "size_bytes": os.path.getsize(path),
        "container": {"format_name": track.get("format", ""),
                      "format_long_name": "", "duration_s": None,
                      "bit_rate": None, "tags": {}},
        "chapters": [], "streams": [], "audio": None, "audio_streams": [],
        "video": None, "video_streams": [], "cover_art": False,
        "subtitle_streams": [],
    }
    measurements = captions.measure(track, None)
    result = checks.evaluate(facts, measurements, profile)
    return facts, measurements, result, profile


def _add_declared_extras(path, facts, measurements, profile, ffprobe):
    """Answers that cost a probe of their own, fetched only when a rule asks."""
    if _needs(profile, {"bitrate_mode"}):
        measurements["bitrate_mode"] = probe.bitrate_mode(
            path, facts["container"]["duration_s"], ffprobe)


def _add_clipping(path, facts, measurements, ffmpeg):
    """Clipping costs a second decode, so first ask whether it can exist.

    A file whose loudest sample is below the clipping threshold has no clipped
    samples — that is arithmetic, not an estimate — so the common case is
    answered without reading the audio again.
    """
    threshold = measurements["settings"]["clip_threshold_dbfs"]
    peak = measurements.get("peak_dbfs")
    if peak is None or peak < threshold:
        measurements["clipping_seconds"] = 0.0
        measurements["clipped_samples"] = 0
        measurements["clipping_windows"] = []
        return
    windows = analysis.locate_peaks(
        path, threshold, ffmpeg=ffmpeg,
        sample_rate=(facts.get("audio") or {}).get("sample_rate") or 48000)
    measurements.update(analysis.summarise_clipping(windows))
    measurements["peak_windows"] = windows


# ----------------------------------------------------------------- commands

def command_check(args):
    if not args.quiet:
        sys.stderr.write(_picture_notice(args.file, args.target, args.picture))
    facts, measurements, result, profile = run(
        args.file, args.target, progress=_progress(args),
        caption_path=args.captions, depth=args.picture,
        width_divide=args.width_divide)
    envelope = report.envelope(facts, measurements, result, profile)
    _write_outputs(args, envelope)
    if not args.quiet:
        sys.stdout.write(report.text(envelope,
                                     show_passes=not args.failures_only))
    return _exit_code(envelope, args.strict)


def command_fix(args):
    if not args.quiet:
        sys.stderr.write(_picture_notice(args.file, args.target, args.picture))
    facts, measurements, result, profile = run(
        args.file, args.target, progress=_progress(args),
        caption_path=args.captions, depth=args.picture,
        width_divide=args.width_divide)
    envelope = report.envelope(facts, measurements, result, profile)

    planned = corrections.plan(facts, measurements, result, profile)
    steps = planned["steps"]
    if not steps:
        sys.stdout.write(report.text(envelope,
                                     show_passes=not args.failures_only))
        sys.stdout.write("\nNothing to correct: no failing check has a safe "
                         "automatic fix.\n")
        return _exit_code(envelope, args.strict)

    destination = args.output or corrections.output_path(
        args.file, steps, directory=args.directory)

    if not args.quiet:
        sys.stdout.write(_preview(steps, planned["unfixable"], destination))
    ffmpeg, _ = platform_support.require_tools()

    if args.dry_run:
        prepared = corrections.prepare(args.file, steps, ffmpeg)
        command = corrections.build_command(args.file, destination, prepared,
                                            facts, ffmpeg)
        sys.stdout.write("\nCommand that would run:\n  "
                         + " ".join(_quote(c) for c in command) + "\n")
        return 0

    if not args.yes and sys.stdin.isatty():
        answer = input("\nWrite the corrected copy? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            sys.stdout.write("Nothing was written.\n")
            return 0

    written, command, prepared = corrections.apply(
        args.file, steps, facts, destination=destination, ffmpeg=ffmpeg,
        overwrite=args.overwrite, directory=args.directory)
    if not args.quiet:
        sys.stdout.write(f"\nWrote {written}\n")

    # The claim that the copy is fixed is a measurement, not a hope.
    after_facts, after_measurements, after_result, _ = run(
        written, profile, progress=_progress(args))

    # At most two corrective rebuilds, each one from the source rather than
    # from the last attempt, so the number of lossy encodes never grows.
    for _ in range(2 if args.refine else 0):
        if after_result["verdict"] != "fail":
            break
        adjusted, why = corrections.refine(
            prepared, after_measurements, after_result, profile)
        if not adjusted:
            break
        if not args.quiet:
            sys.stdout.write(f"  Rebuilding from the source: {why}.\n")
        written, command, prepared = corrections.apply(
            args.file, adjusted, facts, destination=destination,
            ffmpeg=ffmpeg, overwrite=True, directory=args.directory)
        after_facts, after_measurements, after_result, _ = run(
            written, profile, progress=_progress(args))
    after = report.envelope(after_facts, after_measurements, after_result,
                            profile,
                            corrections=[{"description": s["description"]}
                                         for s in prepared])
    if not args.quiet:
        sys.stdout.write("\n" + _verification(envelope, after))
        sys.stdout.write("\n" + report.text(
            after, show_passes=not args.failures_only))

    if args.recipe:
        _write(args.recipe, report.data(corrections.recipe(
            args.file, written, prepared, command, after)))
        sys.stdout.write(f"Recipe written to {args.recipe}\n")
    _write_outputs(args, after, baseline=envelope)
    return _exit_code(after, args.strict)


def command_batch(args):
    """Check a folder, or a list of files, as one delivery."""
    if not args.quiet:
        sys.stderr.write(_delivery_notice(args))
    result = batch.run(args.files, args.target, recursive=args.recursive,
                       progress=None, on_file=_file_progress(args),
                       depth=args.picture, width_divide=args.width_divide)
    envelope = report.set_envelope(result)

    if args.fix:
        return _correct_delivery(args, result, envelope)

    if args.json:
        _write(args.json, report.data(envelope))
    if args.markdown:
        name = None
        drawing = report.set_chart_svg(result)
        if drawing:
            stem = os.path.splitext(args.markdown)[0]
            _write(stem + ".loudness.svg", drawing)
            name = os.path.basename(stem) + ".loudness.svg"
        _write(args.markdown, report.set_markdown(envelope, name))
    if not args.quiet:
        sys.stdout.write(report.set_text(envelope,
                                         show_passes=args.show_passes))
    return _exit_code(envelope, args.strict)


def _correct_delivery(args, result, before):
    """Correct a whole delivery, then measure the delivery it produced."""
    planned = batch.plan(result)
    if not planned["files"]:
        if not args.quiet:
            sys.stdout.write(report.set_text(before,
                                             show_passes=args.show_passes))
            sys.stdout.write("\nNothing to correct: no failing check here "
                             "has a safe automatic fix.\n")
        return _exit_code(before, args.strict)

    if not args.quiet:
        sys.stdout.write(_delivery_preview(planned))
    if args.dry_run:
        return 0
    if not args.yes and sys.stdin.isatty():
        answer = input("\nWrite the corrected copies? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            sys.stdout.write("Nothing was written.\n")
            return 0

    def say(message):
        if not args.quiet:
            sys.stdout.write(f"  {message}\n")

    done = batch.correct(result, args.target, overwrite=args.overwrite,
                         directory=args.directory,
                         on_file=_file_progress(args), on_stage=say)
    written = done["written"]
    for failure in (written or {}).get("failed", []):
        sys.stderr.write(f"  {failure['name']}: {failure['error']}\n")
    if not done["after"]:
        raise PreflightError("No corrected copy could be written.")

    after = report.set_envelope(done["after"])
    if not args.quiet:
        count = len(written["written"])
        sys.stdout.write(f"\nWrote {count} corrected "
                         f"{'copy' if count == 1 else 'copies'}\n")
        sys.stdout.write("\n" + _delivery_verification(before, after))
        sys.stdout.write("\n" + report.set_text(
            after, show_passes=args.show_passes))

    if args.recipe:
        _write(args.recipe, report.data({
            "schema": 1, "tool": report.NAME,
            "target": before["target"],
            "consensus": done["planned"]["consensus"],
            "files": [{"source": entry["source"], "output": entry["output"],
                       "operations": [{"id": step["id"],
                                       "description": step["description"]}
                                      for step in entry["steps"]],
                       "command": entry["command"]}
                      for entry in written["written"]],
        }))
        sys.stdout.write(f"Recipe written to {args.recipe}\n")
    return _exit_code(after, args.strict)


def _delivery_preview(planned):
    lines = ["", "Proposed corrections — nothing has been written yet.", ""]
    for reason in planned["consensus"]["reasons"]:
        lines.append(f"  · {reason}")
    lines.append("")
    for entry in planned["files"]:
        because = (" (because of the delivery)" if entry["because_of_the_set"]
                   else "")
        lines.append(f"  {entry['name']}{because}")
        for step in entry["steps"]:
            lines.append(f"      {step['description']}")
            if step.get("caveat"):
                lines.append(f"      ⚠ {step['caveat']}")
    if planned["untouched"]:
        lines.append("")
        lines.append("  Left alone: " + ", ".join(planned["untouched"]))
    if planned["unaddressed"]:
        lines.append("")
        lines.append("  Not corrected by this tool:")
        for finding in planned["unaddressed"]:
            lines.append(f"    · {finding['label']} — {finding['detail']}")
    lines.append("")
    lines.append("  Sources: unchanged")
    return "\n".join(lines) + "\n"


def _delivery_verification(before, after):
    """What changed about the delivery, which is not what changed about a file."""
    was = {f["id"]: f for f in before["set"]["findings"]}
    lines = ["Verification — the corrected delivery, measured from scratch:", ""]
    changed = False
    for finding in after["set"]["findings"]:
        previous = was.get(finding["id"])
        if not previous or (previous["status"] == finding["status"] == "pass"):
            continue
        if previous["status"] == finding["status"] and \
                previous["actual"] == finding["actual"]:
            continue
        changed = True
        arrow = (f"{report.MARK[previous['status']]} → "
                 f"{report.MARK[finding['status']]}")
        lines.append(f"  {arrow}  {finding['label']}: "
                     f"{previous['actual']} → {finding['actual']}")
    if not changed:
        lines.append("  Nothing measurable changed about the delivery.")
    lines.append("")
    return "\n".join(lines)


def _file_progress(args):
    """One line per file, so a folder of thirty does not look like a hang."""
    if args.quiet or not sys.stderr.isatty():
        return None

    def show(index, total, name):
        sys.stderr.write(f"\r  {index + 1}/{total}  {name[:44]:<46}")
        sys.stderr.flush()
        if index + 1 == total:
            sys.stderr.write("\r" + " " * 56 + "\r")
    return show


def command_targets(args):
    for profile in profiles.all_profiles():
        flag = " (informal)" if profile.get("confidence") == "informal" else ""
        print(f"{profile['id']:<16} {profile['label']}{flag}")
        print(f"{'':<16} {profile['summary']}")
        print(f"{'':<16} source: {profile.get('source', '—')}")
        print()
    return 0


# ------------------------------------------------------------------ helpers

def _preview(steps, unfixable, destination):
    lines = ["", "Proposed corrections — nothing has been written yet.", ""]
    for index, step in enumerate(steps, 1):
        lines.append(f"  {index}. {step['description']}")
        if step.get("caveat"):
            lines.append(f"     ⚠ {step['caveat']}")
    lines.append("")
    lines.append(f"  Output:  {destination}")
    lines.append(f"  Source:  unchanged")
    if unfixable:
        lines.append("")
        lines.append("  Not corrected by this tool:")
        for item in unfixable:
            lines.append(f"    · {item['label']} — {item['detail']}")
    return "\n".join(lines) + "\n"


def _verification(before, after):
    """What actually changed, check by check."""
    was = {f["id"]: f for f in before["findings"]}
    lines = ["Verification — the corrected copy, measured from scratch:", ""]
    changed = False
    for finding in after["findings"]:
        previous = was.get(finding["id"])
        if not previous or previous["status"] == finding["status"] == "pass":
            continue
        if previous["status"] == finding["status"] and \
                previous["actual"] == finding["actual"]:
            continue
        changed = True
        arrow = f"{report.MARK[previous['status']]} → {report.MARK[finding['status']]}"
        lines.append(f"  {arrow}  {finding['label']}: "
                     f"{previous['actual']} → {finding['actual']}")
    if not changed:
        lines.append("  Nothing measurable changed.")
    lines.append("")
    return "\n".join(lines)


def _progress(args):
    if getattr(args, "quiet", False) or not sys.stderr.isatty():
        return None
    state = {"last": -1}

    def show(fraction):
        percent = int(fraction * 100)
        if percent != state["last"]:
            state["last"] = percent
            sys.stderr.write(f"\r  measuring… {percent:3d}%")
            sys.stderr.flush()
            if percent >= 100:
                sys.stderr.write("\r" + " " * 24 + "\r")
    return show


def _write_outputs(args, envelope, baseline=None):
    if getattr(args, "json", None):
        _write(args.json, report.data(envelope))
    if getattr(args, "markdown", None):
        _write(args.markdown,
               report.markdown(envelope,
                               _write_chart(args.markdown, envelope,
                                            baseline)))


def _write_chart(markdown_path, envelope, baseline=None):
    """Write the loudness chart beside the report that references it.

    A sibling file rather than an inline ``<svg>``, because almost everything
    that renders Markdown strips inline SVG, and a picture that silently does
    not appear is worse than one that is plainly a separate file.
    """
    drawing = report.chart_svg(envelope, baseline=baseline)
    if not drawing:
        return None
    stem = os.path.splitext(markdown_path)[0]
    _write(stem + ".loudness.svg", drawing)
    return os.path.basename(stem) + ".loudness.svg"


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _quote(part):
    return f'"{part}"' if " " in part or ";" in part else part


def _exit_code(envelope, strict=False):
    if envelope["verdict"] == "fail":
        return 1
    if strict and envelope["verdict"] == "warn":
        return 1
    return 0


def _picture_flags(sub):
    sub.add_argument("--picture", choices=DEPTHS, default="selective",
                     help="how much of the picture to read: 'selective' "
                          "(default) measures only what the target checks; "
                          "'full' measures everything, which on a long file "
                          "is minutes rather than seconds")
    sub.add_argument("--half-width", dest="width_divide", action="store_const",
                     const=2, default=None,
                     help="read the picture at half width, roughly halving "
                          "the time; refused with the interlacing checks, "
                          "which need the full picture")
    sub.add_argument("--quarter-width", dest="width_divide",
                     action="store_const", const=4,
                     help="as --half-width, but a quarter")


def _picture_notice(path, target, depth, ffprobe=None, facts=None):
    """The sentence warning somebody that this will take a while.

    Only when it will. A pass that finishes before anybody looks up does not
    need announcing, and a warning printed every time is a warning nobody
    reads.
    """
    try:
        plan = picture_plan(path, target, depth, ffprobe=ffprobe, facts=facts)
    except (probe.ProbeError, ValueError, PreflightError):
        return ""
    if not plan:
        return ""
    chosen = plan[depth]
    if not chosen["filters"] or chosen["seconds"] < 30:
        return ""

    lines = ["Reading the picture: %s (%s)."
             % (", ".join(chosen["filters"]), depth)]
    lines.append("  Roughly %s on a recent laptop — it reads every frame, and "
                 "a slower machine will take longer." % _duration(chosen["seconds"]))
    other = "full" if depth == "selective" else "selective"
    if plan[other]["filters"] != chosen["filters"]:
        if other == "selective":
            if plan[other]["filters"]:
                lines.append("  --picture selective would read only %s, in "
                             "about %s."
                             % (", ".join(plan[other]["filters"]),
                                _duration(plan[other]["seconds"])))
            else:
                lines.append("  --picture selective would skip the picture "
                             "entirely: this target checks nothing about it.")
        else:
            lines.append("  --picture full would also read %s, in about %s."
                         % (", ".join(sorted(set(plan[other]["filters"])
                                             - set(chosen["filters"]))),
                            _duration(plan[other]["seconds"])))
    if "fields" not in chosen["filters"]:
        lines.append("  --half-width would roughly halve it.")
    return "\n".join(lines) + "\n"


def _delivery_notice(args):
    """One notice for the whole delivery, summed across its files.

    A per-file notice twenty times over is a wall nobody reads, and the first
    file's figure is not the answer to "how long will this take" when there
    are twenty of them. ffprobe on each is milliseconds against a job about
    to decode all of them.
    """
    try:
        files = batch.collect(args.files, args.recursive)
    except Exception:                          # noqa: BLE001 — reported later
        return ""

    _, ffprobe = platform_support.require_tools()
    totals = {name: 0.0 for name in DEPTHS}
    filters, counted = set(), 0
    for path in files:
        try:
            plan = picture_plan(path, args.target, args.picture, ffprobe)
        except Exception:                      # noqa: BLE001 — reported later
            continue
        if not plan:
            continue
        counted += 1
        filters |= set(plan[args.picture]["filters"])
        for name in DEPTHS:
            totals[name] += plan[name]["seconds"]

    if not counted or totals[args.picture] < 30:
        return ""
    lines = ["Reading the picture in %d of %d files: %s (%s)."
             % (counted, len(files), ", ".join(sorted(filters)), args.picture)]
    lines.append("  Roughly %s in total on a recent laptop — every frame of "
                 "each — and longer on a slower machine."
                 % _duration(totals[args.picture]))
    other = "full" if args.picture == "selective" else "selective"
    if totals[other] < 1:
        lines.append("  --picture selective would skip the picture entirely: "
                     "this target checks nothing about it.")
    elif abs(totals[other] - totals[args.picture]) > 30:
        lines.append("  --picture %s would take about %s."
                     % (other, _duration(totals[other])))
    return "\n".join(lines) + "\n"


def _duration(seconds):
    seconds = int(round(seconds))
    if seconds < 90:
        return "%d seconds" % seconds
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return "%d min %02d s" % (minutes, rest)
    hours, minutes = divmod(minutes, 60)
    return "%d h %02d min" % (hours, minutes)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="preflight",
        description="Check a finished audio or video file against a delivery "
                    "target, and optionally write a corrected copy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def shared(sub):
        sub.add_argument("file")
        sub.add_argument("--target", "-t", default="web",
                         help="target profile id, or a path to a JSON profile")
        sub.add_argument("--json", help="write the machine-readable report here")
        sub.add_argument("--markdown",
                         help="write the client-facing report here")
        sub.add_argument("--quiet", "-q", action="store_true")
        sub.add_argument("--failures-only", action="store_true",
                         help="hide the checks that passed")
        sub.add_argument("--strict", action="store_true",
                         help="treat warnings as failures in the exit code")
        sub.add_argument("--captions",
                         help="caption file to check with this media; by "
                              "default a sidecar beside it, then an embedded "
                              "subtitle stream")
        _picture_flags(sub)

    check = subparsers.add_parser("check", help="measure and report")
    shared(check)
    check.set_defaults(handler=command_check)

    fix = subparsers.add_parser(
        "fix", help="write a corrected copy beside the source")
    shared(fix)
    fix.add_argument("--output", "-o", help="write the copy here")
    fix.add_argument("--directory", "-d", help="write the copy into this folder")
    fix.add_argument("--recipe", help="write the JSON recipe here")
    fix.add_argument("--dry-run", action="store_true",
                     help="show the plan and the command, write nothing")
    fix.add_argument("--yes", "-y", action="store_true",
                     help="do not ask before writing")
    fix.add_argument("--overwrite", action="store_true",
                     help="replace the output file if it already exists")
    fix.add_argument("--no-refine", dest="refine", action="store_false",
                     help="do not rebuild once from the source when the "
                          "first corrected copy lands off target")
    fix.set_defaults(handler=command_fix)

    batch_command = subparsers.add_parser(
        "batch", help="check a folder, or several files, as one delivery")
    batch_command.add_argument("files", nargs="+",
                               help="files and/or folders to check")
    batch_command.add_argument("--target", "-t", default="web",
                               help="target profile id, or a path to a JSON "
                                    "profile")
    batch_command.add_argument("--recursive", "-r", action="store_true",
                               help="descend into sub-folders")
    batch_command.add_argument("--json", help="write the machine-readable "
                                              "delivery report here")
    batch_command.add_argument("--markdown",
                               help="write the client-facing report here")
    batch_command.add_argument("--quiet", "-q", action="store_true")
    batch_command.add_argument("--show-passes", action="store_true",
                               help="list the delivery checks that passed too")
    batch_command.add_argument("--strict", action="store_true",
                               help="treat warnings as failures in the exit "
                                    "code")
    batch_command.add_argument("--fix", action="store_true",
                               help="write corrected copies of the files that "
                                    "need them, including for faults only the "
                                    "delivery has")
    batch_command.add_argument("--dry-run", action="store_true",
                               help="with --fix: show the plan, write nothing")
    batch_command.add_argument("--yes", "-y", action="store_true",
                               help="with --fix: do not ask before writing")
    batch_command.add_argument("--overwrite", action="store_true",
                               help="replace corrected copies that exist")
    batch_command.add_argument("--directory", "-d",
                               help="write the corrected copies into this "
                                    "folder")
    _picture_flags(batch_command)
    batch_command.add_argument("--recipe",
                               help="with --fix: write the JSON recipe here")
    batch_command.set_defaults(handler=command_batch)

    targets = subparsers.add_parser("targets", help="list delivery targets")
    targets.set_defaults(handler=command_targets)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (PreflightError, platform_support.ToolsMissing, probe.ProbeError,
            analysis.AnalysisError, video.VideoError, captions.CaptionError,
            corrections.CorrectionError, batch.BatchError,
            ValueError) as error:
        sys.stderr.write(f"{error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped.\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
