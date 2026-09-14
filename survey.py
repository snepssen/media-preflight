"""Where does this file stand — without being asked where it is going first.

Every other entry point into this tool begins with a question: pick a target.
That is the right question for somebody with a delivery specification open in
another window, and the wrong one for somebody holding a finished song. Their
honest answer is "I don't know, that's what I'm asking you", and until this
existed the tool had no way to hear it.

So: measure the file once, then compare it against every target that could
apply and report where it stands in all of them at once. "Ready for streaming,
eight decibels too loud for broadcast, wrong codec for an audiobook" is an
answer somebody can act on. "Pick a target" is a form.

Why this is affordable
----------------------
The measurement does not depend on the target. `analysis.analyse` reads the
file; `checks.evaluate` compares the numbers against a profile. Comparing
against seven profiles instead of one costs seven dictionary walks.

The only thing a profile changes about the *measuring* is its `options` — ACX
alone moves the silence threshold, because room tone is measured differently
from a gap in a podcast. So this groups the applicable targets by the options
they ask for, and measures once per distinct group. On the audio targets that
is three passes of about two seconds each on a three-minute track.

Picture is the exception and it is not free. Surveying a video means every
target's picture question gets asked, which is the full pass — eighty minutes
on a feature. `plan` says so before anything starts.
"""

from __future__ import annotations

import copy
import json

import analysis
import checks
import platform_support
import preflight
import probe
import profiles
import report
import video


class SurveyError(RuntimeError):
    """A file this cannot survey, in a sentence."""


def applicable(kind, include_custom=True):
    """Every target worth comparing a file of this kind against.

    A person's own SOPs are included: somebody who has written one wants to
    know where a file stands against it just as much as against YouTube.
    """
    return [p for p in profiles.for_kind(kind, include_custom)
            if p.get("id") != "house" or include_custom]


def _options_key(profile):
    return json.dumps(profile.get("options") or {}, sort_keys=True)


def _union(group, kind):
    """One profile carrying every rule in the group, for the measuring pass.

    `preflight.run` decides what to measure from the rules it is given —
    which caption metrics are wanted, whether the picture is read at all and
    with which filters. Handing it the union means one pass covers every
    target in the group, and nothing is measured that no target asked for.
    """
    rules, seen = [], set()
    for profile in group:
        for rule in profile.get("rules", []):
            if rule["metric"] in seen:
                continue
            seen.add(rule["metric"])
            rules.append(copy.deepcopy(rule))
    return {
        "id": "_survey_union",
        "label": "Survey",
        "kind": kind,
        "rules": rules,
        "set_rules": [],
        "options": group[0].get("options") or {},
    }


def plan(path, ffprobe=None):
    """What a survey will read and roughly what it will cost, before it runs."""
    if ffprobe is None:
        _, ffprobe = platform_support.require_tools()
    facts = probe.inspect(path, ffprobe)
    kind = ("captions" if preflight.is_caption_file(path)
            else probe.kind_of(facts))
    if not kind:
        raise SurveyError(
            "%s carries neither sound nor moving picture that this tool can "
            "measure." % facts.get("name", path))

    targets = applicable(kind)
    groups = {}
    for profile in targets:
        groups.setdefault(_options_key(profile), []).append(profile)

    duration = (facts.get("container") or {}).get("duration_s") or 0.0
    stream = facts.get("video") or {}
    seconds = 0.0
    for group in groups.values():
        union = _union(group, kind)
        seconds += analysis.estimate_seconds(duration) or 0.0
        if stream:
            filters = preflight.picture_filters(union)
            seconds += video.estimate_seconds(
                filters, duration, stream.get("width"), stream.get("height"),
                stream.get("avg_frame_rate")) or 0.0
    return {
        "kind": kind,
        "targets": [{"id": p["id"], "label": p["label"]} for p in targets],
        "passes": len(groups),
        "seconds": round(seconds, 1),
        "duration_s": duration,
    }


def run(path, ffmpeg=None, ffprobe=None, progress=None, caption_path=None,
        depth="selective", include_custom=True):
    """Measure once per option set, then judge against every applicable target.

    Returns the survey envelope: what the file is, what was measured, and a
    verdict for each target with the one sentence that explains it.
    """
    if ffmpeg is None or ffprobe is None:
        ffmpeg, ffprobe = platform_support.require_tools()

    facts = probe.inspect(path, ffprobe)
    kind = ("captions" if preflight.is_caption_file(path)
            else probe.kind_of(facts))
    if not kind:
        raise SurveyError(
            "%s carries neither sound nor moving picture that this tool can "
            "measure." % facts.get("name", path))

    targets = applicable(kind, include_custom)
    if not targets:
        raise SurveyError("There are no targets to compare a %s file against."
                          % kind)

    groups = {}
    for profile in targets:
        groups.setdefault(_options_key(profile), []).append(profile)

    audio = facts.get("audio") or {}

    def locator(threshold_dbfs):
        return analysis.locate_peaks(
            path, threshold_dbfs, ffmpeg=ffmpeg,
            sample_rate=audio.get("sample_rate") or 48000)

    envelopes, done = [], 0
    first_measurements = None
    for group in groups.values():
        share = 1.0 / len(groups)
        facts, measurements, _, _ = preflight.run(
            path, _union(group, kind), ffmpeg, ffprobe,
            progress=preflight._scaled(progress, done, done + share),
            caption_path=caption_path, depth=depth)
        done += share
        if first_measurements is None:
            first_measurements = measurements
        for profile in group:
            result = checks.evaluate(facts, measurements, profile, locator)
            envelope = report.envelope(facts, measurements, result, profile)
            envelope["headline"] = headline(envelope)
            envelope["fixable"] = _fixable(facts, measurements, result, profile)
            envelopes.append(envelope)

    order = {"fail": 0, "warn": 1, "pass": 2}
    envelopes.sort(key=lambda e: (order.get(e["verdict"], 3),
                                  e["target"].get("label", "")))
    ready = [e for e in envelopes if e["verdict"] == "pass"]

    return {
        "file": envelopes[0]["file"],
        "kind": kind,
        "measurements": envelopes[0]["measurements"],
        "timeline": envelopes[0].get("timeline"),
        "picture": envelopes[0].get("picture"),
        "targets": envelopes,
        "ready": [e["target"]["id"] for e in ready],
        "counts": {
            "ready": len(ready),
            "warn": sum(1 for e in envelopes if e["verdict"] == "warn"),
            "fail": sum(1 for e in envelopes if e["verdict"] == "fail"),
        },
    }


def headline(envelope):
    """One sentence saying where this file stands with this target.

    The report already lists every check; what somebody scanning a list of
    seven destinations needs is the reason, not the table. Failures speak
    first because they are what stops the delivery, and the count carries the
    rest rather than a second sentence nobody reads.
    """
    findings = envelope.get("findings") or []
    bad = [f for f in findings if f["status"] == "fail"]
    warned = [f for f in findings if f["status"] == "warn"]

    if not bad and not warned:
        return "Ready to deliver."
    worst = (bad or warned)[0]
    # The label is used as written. Lower-casing it to fit the sentence and
    # re-capitalising turns "RMS level" into "Rms level" and "DC offset" into
    # "Dc offset", which is the kind of small wrongness that makes a tool look
    # like it does not know what it is talking about.
    said = "%s is %s, wanted %s" % (worst["label"], worst["actual"],
                                    worst["required"])
    rest = len(bad) + len(warned) - 1
    if rest:
        said += ", and %d other%s" % (rest, "" if rest == 1 else "s")
    return said + "."


def _fixable(facts, measurements, result, profile):
    """Whether a corrected copy would actually change anything here.

    Asked now rather than when somebody clicks, so a button that cannot do
    anything is never offered. corrections.plan is arithmetic over the
    findings — it decodes nothing.
    """
    import corrections
    try:
        planned = corrections.plan(facts, measurements, result, profile)
    except Exception:                       # noqa: BLE001 — offered, not run
        return False
    return bool(planned.get("steps"))
