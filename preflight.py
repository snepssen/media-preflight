#!/usr/bin/env python3
"""Media Preflight — will this file be accepted, and can it be fixed safely?

    python3 preflight.py check  finished.mp3 --target acx
    python3 preflight.py fix    finished.mp3 --target acx --dry-run
    python3 preflight.py fix    finished.mp3 --target acx
    python3 preflight.py targets

`check` measures and reports. `fix` writes a corrected copy beside the source
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


# Everything the picture pass answers. Decoding a ninety-minute film to
# measure black frames is minutes of somebody's time, so it happens only when
# the target actually asks one of these questions.
VIDEO_METRICS = {
    "black_seconds", "longest_black_s", "leading_black_s", "trailing_black_s",
    "frozen_seconds", "longest_frozen_s", "flash_regions",
}
CAPTION_METRICS = {name for name in checks.METRICS if name.startswith("caption_")}


def run(path, target="web", ffmpeg=None, ffprobe=None, progress=None,
        caption_path=None, stage=None):
    """Measure one file against one target. Returns the report envelope.

    ``stage`` is called with 'container', 'audio', 'video', 'captions' or
    'target' as each begins, so a window can show which part of the file is
    being read rather than a bar with no subject.
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

    wants_video = bool(facts.get("video")) and _needs(profile, VIDEO_METRICS)
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
            _scaled(progress, audio_share, 1.0)))
    if _needs(profile, {"frame_rate_mode"}) and facts.get("video"):
        measurements["frame_rate_mode"] = video.frame_rate_mode(
            path, duration, ffprobe)
    if _needs(profile, CAPTION_METRICS):
        announce("captions")
        _add_captions(path, facts, measurements, caption_path, ffmpeg, duration)

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


def _scaled(progress, start, end):
    """A progress callback covering one slice of the whole job."""
    if progress is None:
        return None
    return lambda fraction: progress(start + fraction * (end - start))


def _video_measurements(path, facts, profile, ffmpeg, duration, progress):
    out = video.analyse(path, ffmpeg=ffmpeg, duration_s=duration,
                        options=profile.get("options"), progress=progress)
    # The audio pass already owns "settings"; the picture pass keeps its own
    # under a name of its own rather than overwriting it.
    out["video_settings"] = out.pop("settings", {})
    return out


def _add_captions(path, facts, measurements, caption_path, ffmpeg, duration):
    """Attach a caption track when there is one to attach. Absence is not a fault."""
    try:
        track = captions.find(path, facts, caption_path, ffmpeg)
    except captions.CaptionError as error:
        measurements["caption_error"] = str(error)
        return
    if track:
        measurements.update(captions.measure(track, duration))


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
    facts, measurements, result, profile = run(
        args.file, args.target, progress=_progress(args),
        caption_path=args.captions)
    envelope = report.envelope(facts, measurements, result, profile)
    _write_outputs(args, envelope)
    if not args.quiet:
        sys.stdout.write(report.text(envelope,
                                     show_passes=not args.failures_only))
    return _exit_code(envelope, args.strict)


def command_fix(args):
    facts, measurements, result, profile = run(
        args.file, args.target, progress=_progress(args),
        caption_path=args.captions)
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
    sys.stdout.write("\n" + _verification(envelope, after))
    sys.stdout.write("\n" + report.text(after,
                                        show_passes=not args.failures_only))

    if args.recipe:
        _write(args.recipe, report.data(corrections.recipe(
            args.file, written, prepared, command, after)))
        sys.stdout.write(f"Recipe written to {args.recipe}\n")
    _write_outputs(args, after)
    return _exit_code(after, args.strict)


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


def _write_outputs(args, envelope):
    if getattr(args, "json", None):
        _write(args.json, report.data(envelope))
    if getattr(args, "markdown", None):
        _write(args.markdown, report.markdown(envelope,
                                              _write_chart(args.markdown,
                                                           envelope)))


def _write_chart(markdown_path, envelope):
    """Write the loudness chart beside the report that references it.

    A sibling file rather than an inline ``<svg>``, because almost everything
    that renders Markdown strips inline SVG, and a picture that silently does
    not appear is worse than one that is plainly a separate file.
    """
    drawing = report.chart_svg(envelope)
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

    targets = subparsers.add_parser("targets", help="list delivery targets")
    targets.set_defaults(handler=command_targets)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (PreflightError, platform_support.ToolsMissing, probe.ProbeError,
            analysis.AnalysisError, video.VideoError, captions.CaptionError,
            corrections.CorrectionError, ValueError) as error:
        sys.stderr.write(f"{error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped.\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
