"""What each file in a selection actually is, before anything is measured.

Both modes start here. Guided mode turns this into three plain questions;
Professional mode hangs a profile selector off every row. The sorting itself
is the same either way, because what a file *is* does not depend on who is
looking at it.

This stage reads containers and never decodes. One ffprobe per media file, a
read of each caption file, and nothing else — the point is to put a list in
front of somebody in the time it takes to let go of the mouse, so that the
expensive question ("shall I measure all this?") is asked with the answer
already visible.

Three rules earn their place here:

**Cover art is not a picture.** An MP3 with embedded artwork has a video
stream, and a naive reading files it under video and offers to check its
frame rate. `probe.inspect` already separates the moving picture from the
attached still; this trusts that separation and says audio.

**A sidecar belongs to its media.** `film.srt` beside `film.mp4` is not a
second deliverable, it is part of the first one, and checking it twice
produces two reports that can disagree about the same file. It is nested
underneath instead, and can be detached by anybody who genuinely wanted a
standalone caption report.

**Unreadable is a finding, not an omission.** A selection that quietly loses
four files is worse than one that lists them with a sentence saying why. The
batch collector normally filters to extensions it knows; here it is asked for
everything, and what this cannot classify still appears.
"""

from __future__ import annotations

import os
import re

import batch
import captions
import platform_support
import probe

KINDS = ("video", "audio", "captions", "unsupported")

# Enough of a sequence to be worth suggesting as one delivery. Two files that
# happen to share a prefix are a coincidence; this is batch.numbering's
# threshold and the same reasoning applies.
_MIN_GROUP = 3


class IntakeError(RuntimeError):
    """A selection that could not be read at all."""


def classify(paths, recursive=False, ffprobe=None, progress=None):
    """Sort a selection into what each file is. Returns the intake envelope.

    ``progress`` is called with (done, total) as each file is identified, so a
    folder of four hundred files can show something moving. Identifying one is
    milliseconds; four hundred is not.
    """
    if ffprobe is None:
        _, ffprobe = platform_support.require_tools()
    try:
        found = batch.collect(paths, recursive, batch.ANY_EXTENSION)
    except batch.BatchError as error:
        raise IntakeError(str(error)) from error

    items = []
    for index, path in enumerate(found):
        items.append(_identify(path, ffprobe))
        if progress:
            progress(index + 1, len(found))

    items = _attach_companions(items)
    return {
        "items": items,
        "groups": suggest_groups(items),
        "counts": _counts(items),
    }


# ------------------------------------------------------------ one file

def _identify(path, ffprobe):
    name = os.path.basename(path)
    extension = os.path.splitext(path)[1].lstrip(".").lower()

    if extension in platform_support.CAPTION_EXTS:
        return _identify_caption(path, name)

    # Asked of the name before the file, because the alternative is running
    # ffprobe over every README and cover image in a delivery folder to be
    # told what the extension already said. The real album this was built
    # against holds 68 such files against 34 real ones. A media file wearing
    # the wrong extension is missed by this, which is the trade: it would
    # otherwise be found at the cost of seconds on every intake.
    if extension not in platform_support.MEDIA_EXTS:
        return _unsupported(
            path, name,
            "Not an audio, video or caption file — nothing here to check."
            if extension else "No file extension to go on.")

    try:
        facts = probe.inspect(path, ffprobe)
    except probe.ProbeError as error:
        return _unsupported(path, name, _plain_error(error, path))

    if facts.get("video") and not probe.is_still(facts):
        return _item(path, name, "video", facts=facts,
                     summary=_picture_summary(facts))
    if facts.get("video") and not facts.get("audio"):
        return _unsupported(
            path, name,
            "A still image rather than a moving picture — there is no "
            "timeline here to measure.")
    if facts.get("audio"):
        return _item(path, name, "audio", facts=facts,
                     summary=_sound_summary(facts))
    return _unsupported(
        path, name,
        "Opened, but carries neither sound nor moving picture.")


# The rule lives with the prober, so that what intake calls a video and what
# a check refuses to run a caption profile against are the same judgement.
_is_still = probe.is_still


def _identify_caption(path, name):
    try:
        track = captions.load(path)
    except captions.CaptionError as error:
        return _unsupported(path, name, str(error))
    cues = len(track.get("cues") or [])
    return _item(path, name, "captions",
                 summary="%s, %d cue%s" % (track.get("format", "captions"),
                                           cues, "" if cues == 1 else "s"),
                 duration_s=_caption_duration(track))


def _item(path, name, kind, facts=None, summary="", duration_s=None):
    container = (facts or {}).get("container") or {}
    return {
        "path": path,
        "name": name,
        "kind": kind,
        "summary": summary,
        "duration_s": (duration_s if duration_s is not None
                       else container.get("duration_s")),
        "size_bytes": (facts or {}).get("size_bytes"),
        "reason": None,
        "companions": [],
        "facts": facts,
    }


def _plain_error(error, path):
    """ffmpeg's complaint without the path it already knows it is about."""
    said = str(error).strip()
    for prefix in (path + ": ", os.path.basename(path) + ": "):
        if said.startswith(prefix):
            said = said[len(prefix):]
    if said and not said.endswith("."):
        said += "."
    return said[:1].upper() + said[1:] if said else "Could not be read."


def _unsupported(path, name, reason):
    item = _item(path, name, "unsupported")
    item["reason"] = reason
    return item


def _sound_summary(facts):
    audio = facts["audio"]
    parts = [audio.get("codec") or "audio"]
    if audio.get("sample_rate"):
        parts.append("%g kHz" % (audio["sample_rate"] / 1000.0))
    parts.append(_channels(audio.get("channels")))
    if facts.get("cover_art"):
        # Said out loud because it is the reason this row is not under video,
        # and somebody who expected it there should be able to see why.
        parts.append("cover art")
    return " ".join(p for p in parts if p)


def _picture_summary(facts):
    video = facts["video"]
    parts = [video.get("codec") or "video"]
    if video.get("width") and video.get("height"):
        parts.append("%dx%d" % (video["width"], video["height"]))
    if video.get("avg_frame_rate"):
        parts.append("%g fps" % video["avg_frame_rate"])
    if facts.get("audio"):
        parts.append("+ %s" % (facts["audio"].get("codec") or "audio"))
    else:
        parts.append("silent")
    return " ".join(p for p in parts if p)


def _channels(count):
    if not count:
        return ""
    return {1: "mono", 2: "stereo"}.get(count, "%d channels" % count)


def _caption_duration(track):
    cues = track.get("cues") or []
    return max((c.get("end") or 0.0) for c in cues) if cues else None


# ------------------------------------------------------------ companions

def _attach_companions(items):
    """Nest each sidecar caption under the media file it belongs to.

    Matched on the stem, the same way captions.find looks for one, so that
    what intake shows and what the check actually reads are the same file.
    """
    media = {}
    for item in items:
        if item["kind"] in ("audio", "video"):
            media.setdefault(_stem(item["path"]), item)

    kept = []
    for item in items:
        owner = media.get(_stem(item["path"])) if item["kind"] == "captions" \
            else None
        if owner is not None and owner is not item:
            owner["companions"].append({
                "path": item["path"],
                "name": item["name"],
                "kind": "captions",
                "summary": item["summary"],
            })
        else:
            kept.append(item)
    return kept


def _stem(path):
    return os.path.splitext(path)[0]


# ------------------------------------------------------------ deliveries

def suggest_groups(items):
    """Folders whose files look like one delivery rather than several jobs.

    A suggestion and never an assumption. Ten MP3s in a folder might be one
    audiobook that has to be consistent with itself, or ten unrelated podcast
    episodes that have no business being compared — and the set-level rules
    mean the difference is not cosmetic. What can be observed is that they
    share a folder, a kind, and a numbering scheme; what that means is for
    the person who made them to say.
    """
    folders = {}
    for item in items:
        if item["kind"] == "unsupported":
            continue
        numbered = _leading_number(item["name"])
        if not numbered:
            continue
        prefix, number, width = numbered
        key = (os.path.dirname(item["path"]), item["kind"], prefix, width)
        folders.setdefault(key, []).append((number, item))

    groups = []
    for (folder, kind, prefix, width), members in sorted(folders.items()):
        if len(members) < _MIN_GROUP:
            continue
        numbers = sorted(n for n, _ in members)
        groups.append({
            "folder": folder,
            "kind": kind,
            "count": len(members),
            "sequence": "%s%0*d–%s%0*d" % (prefix, width, numbers[0],
                                           prefix, width, numbers[-1]),
            "range": [numbers[0], numbers[-1]],
            "missing": ["%s%0*d" % (prefix, width, n)
                        for n in range(numbers[0], numbers[-1] + 1)
                        if n not in set(numbers)],
            "paths": [i["path"] for _, i in sorted(members,
                                                   key=lambda m: m[0])],
        })
    return groups


# A name that opens with an optional non-digit prefix and then digits. What
# comes after is deliberately ignored: "A01 Black Aura" and "A02 White Aura"
# are one delivery with two titles, and batch.numbering cannot say so because
# it keys on the suffix as well — correctly, since it is looking for a missing
# file in a strict template rather than for a delivery in a folder. The two
# questions want different rules, so they get them.
_LEADING = re.compile(r"^(\D*?)(\d+)")


def _leading_number(name):
    stem = os.path.splitext(name)[0]
    found = _LEADING.match(stem)
    if not found:
        return None
    prefix, digits = found.groups()
    return prefix, int(digits), len(digits)


def _counts(items):
    counts = {kind: 0 for kind in KINDS}
    for item in items:
        counts[item["kind"]] += 1
    counts["companions"] = sum(len(i["companions"]) for i in items)
    return counts


def without_facts(envelope):
    """The same envelope with the probe output dropped, for sending to a page.

    The facts are needed to decide the kind and useful to keep for the check
    that follows; they are several kilobytes a file and nothing in the intake
    list draws them.
    """
    lean = dict(envelope)
    lean["items"] = [{k: v for k, v in item.items() if k != "facts"}
                     for item in envelope["items"]]
    return lean
