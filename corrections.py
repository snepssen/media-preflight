"""Making a corrected copy, and saying exactly what that would do first.

Three rules govern everything in this module.

**The source is never touched.** Every correction writes a new file beside the
original, and refuses to overwrite anything that already exists unless told
twice.

**Nothing is generative.** These are arithmetic operations on a signal: a gain
change, a limiter, a trim, a re-encode. Nothing here invents audio that was not
recorded, removes noise by guessing what was underneath it, or otherwise makes
the file into something that was never delivered to it.

**Every operation is previewable.** ``plan`` returns sentences and
``build_command`` returns the exact argv, both before anything runs. The
recipe written alongside the output is the same plan, so a correction can be
read, argued with, and repeated by hand.

And one rule about honesty: after a corrected copy is written it is measured
again from scratch. A correction is reported as having worked only when the new
file passes the check that the old one failed.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess

import platform_support


class CorrectionError(RuntimeError):
    """A correction could not be planned or applied, with the reason."""


# Encoder settings per codec, kept here so that adding a target's codec is one
# entry rather than a branch in the middle of the command builder.
ENCODERS = {
    "mp3": {"codec": "libmp3lame", "ext": "mp3"},
    "aac": {"codec": "aac", "ext": "m4a"},
    "flac": {"codec": "flac", "ext": "flac"},
    "pcm_s16le": {"codec": "pcm_s16le", "ext": "wav"},
    "opus": {"codec": "libopus", "ext": "opus"},
}

# Two orders, because a fix and the step it produces are not the same thing:
# one fix name — "room tone at the tail" — becomes a trim or a pad depending on
# which side of the requirement the file sits.
#
# FIX_ORDER is the order fixes are considered. STEP_ORDER is the order the
# resulting operations must run in: trimming changes where things are, loudness
# changes how loud they are, and the encode is last because it is the only step
# that decides what the file *is*.
FIX_ORDER = ["room_tone_head", "room_tone_tail", "remove_dc",
             "gain", "loudnorm", "limit_peak", "encode"]

STEP_ORDER = ["trim_head", "pad_head", "trim_tail", "pad_tail", "remove_dc",
              "gain", "loudnorm", "limit_peak", "encode"]


def plan(facts, measurements, result, profile):
    """Turn the failing findings into an ordered list of proposed operations.

    Findings with no ``fix`` are not silently dropped: they come back as
    ``unfixable`` so the caller can say what the corrected copy will still not
    solve.
    """
    failing = [f for f in result["findings"]
               if f["status"] in ("fail", "warn")]
    wanted = {}
    unfixable = []
    for finding in failing:
        if finding.get("fix"):
            wanted.setdefault(finding["fix"], []).append(finding)
        else:
            unfixable.append(finding)

    rules = {rule["id"]: rule for rule in profile.get("rules", [])}
    steps = []
    # Steps run in order and some of them move the audio about. The context
    # carries what has already happened, so the step that trims the ending
    # knows the beginning is no longer where it was.
    context = {"head_trim_s": 0.0}

    for kind in FIX_ORDER:
        if kind not in wanted:
            continue
        builder = _BUILDERS[kind]
        step = builder(facts, measurements, wanted[kind], rules, profile,
                       context)
        if step:
            step["addresses"] = [f["id"] for f in wanted[kind]]
            steps.append(step)

    # A codec or bitrate change and a loudness change both need an encode; when
    # only loudness changed, the encode step still has to exist, because a
    # filtered stream cannot be copied.
    if steps and not any(s["kind"] == "encode" for s in steps):
        steps.append(_passthrough_encode(facts, profile))

    steps = _guard(steps, measurements, rules)

    return {"steps": steps,
            "unfixable": [{"id": f["id"], "label": f["label"],
                           "detail": f["detail"] or f["note"]}
                          for f in unfixable]}


# ------------------------------------------------------------------ builders

def _target_band(rule):
    """Where a correction should aim inside a rule's band.

    The middle of the band, unless the rule names somewhere better: the middle
    of ACX's one-to-five-second room tone is three seconds, which is a sensible
    ending and an absurd opening.
    """
    if rule.get("target") is not None:
        return rule["target"]
    low, high = rule.get("min"), rule.get("max")
    if low is not None and high is not None:
        return (low + high) / 2.0
    return high if high is not None else low


def _gain(facts, measurements, findings, rules, profile,
          context=None):
    """Move the whole file by one number of decibels to land in an RMS band.

    Used for targets written in RMS — ACX is the one that matters — where
    loudnorm's LUFS model would answer a question nobody asked.
    """
    rule = rules.get(findings[0]["id"])
    measured = measurements.get("rms_dbfs")
    aim = _target_band(rule)
    if measured is None or aim is None:
        return None
    gain_db = round(aim - measured, 2)
    if abs(gain_db) < 0.05:
        return None

    # Predict where the peak lands, and say so, because a gain that pushes the
    # peak over the ceiling is the reason a limiter appears in the plan.
    peak = measurements.get("peak_dbfs")
    predicted = None if peak is None else round(peak + gain_db, 2)
    caveat = None
    if predicted is not None and predicted > 0:
        caveat = (f"Gain alone would put the peak {predicted:.1f} dB past full "
                  f"scale, so a limiter has to take it back down. That is a "
                  f"lot of limiting; listen to the result before delivering "
                  f"it, and consider re-recording or re-mixing instead.")
    return {
        "id": "gain",
        "kind": "filter",
        "filters": [f"volume={gain_db:+g}dB"],
        "description": (
            f"Apply {gain_db:+g} dB of gain, moving RMS from "
            f"{measured:.2f} dBFS to about {aim:.2f} dBFS"
            + (f"; peak would land near {predicted:.2f} dBFS." if predicted
               is not None else ".")),
        "caveat": caveat,
        "predicted_peak_dbfs": predicted,
    }


def _limiter_step(ceiling, margin=0.1, reason=None):
    """A peak limiter aimed just under a ceiling.

    alimiter takes linear amplitude. The margin exists for two reasons: a
    limiter that lands exactly on the limit fails a check reading "at or below"
    as soon as anything rounds, and a lossy encoder downstream can hand back
    peaks slightly louder than the ones it was given.
    """
    limit = 10 ** ((ceiling - margin) / 20.0)
    description = (f"Limit peaks to {ceiling - margin:g} dBFS. Anything "
                   f"already flattened by clipping stays flattened — a limiter "
                   f"stops it getting worse, it does not restore the waveform.")
    if reason:
        description = f"{description} {reason}"
    return {
        "id": "limit_peak",
        "kind": "filter",
        "ceiling": ceiling,
        "margin": margin,
        "filters": [f"alimiter=limit={limit:.6f}:level=disabled"],
        "description": description,
        "caveat": ("Limiting changes the shape of the loudest moments. Listen "
                   "to the corrected copy before delivering it."),
    }


def _limit_peak(facts, measurements, findings, rules, profile,
                context=None):
    """A peak limiter, set to the ceiling the target asks for."""
    rule = rules.get(findings[0]["id"])
    ceiling = rule.get("max")
    if ceiling is None:
        return None
    return _limiter_step(ceiling)


def _loudnorm(facts, measurements, findings, rules, profile,
              context=None):
    """Two-pass EBU R128 normalisation to a LUFS target and a peak ceiling."""
    integrated_rule = rules.get("integrated") or {}
    peak_rule = rules.get("true_peak") or {}
    target_i = _target_band(integrated_rule)
    target_tp = peak_rule.get("max", -1.0)
    if target_i is None:
        return None
    target_lra = integrated_rule.get("target_lra", 11.0)
    # loudnorm resamples internally and leaves its output at 192 kHz, which
    # then becomes whatever the encoder will accept — a file that arrived at
    # 48 kHz leaves at 96 kHz and fails a sample-rate rule it passed before.
    # Resampling straight back is the documented remedy.
    rate = (facts.get("audio") or {}).get("sample_rate") or 48000
    return {
        "id": "loudnorm",
        "kind": "filter",
        "needs_measurement": True,
        "loudnorm": {"I": target_i, "TP": target_tp, "LRA": target_lra},
        "post_filters": [f"aresample={int(rate)}"],
        "filters": [],   # filled in by measure_loudnorm before the file is written
        "description": (
            f"Normalise loudness to {target_i:g} LUFS integrated with a "
            f"{target_tp:g} dBTP ceiling, measuring the file first so the "
            f"correction is a single known gain rather than a guess."),
        "caveat": None,
    }


def _remove_dc(facts, measurements, findings, rules, profile,
               context=None):
    return {
        "id": "remove_dc",
        "kind": "filter",
        "filters": ["highpass=f=15:poles=1"],
        "description": ("Remove the DC offset with a gentle 15 Hz high-pass. "
                        "Nothing audible lives below it."),
        "caveat": None,
    }


def _trim_head(facts, measurements, findings, rules, profile,
               context=None):
    rule = rules.get(findings[0]["id"])
    have = measurements.get("lead_silence_s") or 0.0
    want = _target_band(rule)
    if want is None:
        return None
    if have > (rule.get("max") or want):
        cut = round(have - want, 3)
        if context is not None:
            context["head_trim_s"] = cut
        return {
            "id": "trim_head",
            "kind": "filter",
            "filters": [f"atrim=start={cut}", "asetpts=PTS-STARTPTS"],
            "description": (f"Trim {cut:g} s off the front, leaving {want:g} s "
                            f"of room tone before the first word."),
            "caveat": None,
        }
    return None


def _room_tone_head(facts, measurements, findings, rules, profile, context=None):
    return _room_tone_fix(facts, measurements, findings, rules, profile,
                          "head", context)


def _room_tone_tail(facts, measurements, findings, rules, profile, context=None):
    return _room_tone_fix(facts, measurements, findings, rules, profile,
                          "tail", context)


def _room_tone_fix(facts, measurements, findings, rules, profile, where,
                   context=None):
    """Too much quiet at an end is trimmed; too little is added.

    One fix name covers both directions because they are the same requirement
    read from either side, and a plan that could only shorten would leave every
    abruptly-ending file unfixable.
    """
    rule = rules.get(findings[0]["id"]) or {}
    key = "lead_silence_s" if where == "head" else "tail_silence_s"
    have = measurements.get(key) or 0.0
    ceiling, floor = rule.get("max"), rule.get("min")
    if ceiling is not None and have > ceiling:
        return (_trim_head if where == "head" else _trim_tail)(
            facts, measurements, findings, rules, profile, context)
    if floor is not None and have < floor:
        return _pad(facts, measurements, findings, rules, profile, where,
                    context)
    return None


def _trim_tail(facts, measurements, findings, rules, profile,
               context=None):
    rule = rules.get(findings[0]["id"])
    have = measurements.get("tail_silence_s") or 0.0
    want = _target_band(rule)
    ceiling = rule.get("max")
    duration = measurements.get("duration_s")
    if want is None or duration is None or ceiling is None or have <= ceiling:
        return None
    # The head may already have been trimmed, and this filter sees the stream
    # that step produced, not the file on disk.
    already = (context or {}).get("head_trim_s", 0.0)
    keep = round(duration - already - (have - want), 3)
    if keep <= 0:
        return None
    return {
        "id": "trim_tail",
        "kind": "filter",
        "filters": [f"atrim=end={keep}"],
        "description": (f"Trim the ending to {keep:g} s, leaving {want:g} s of "
                        f"room tone after the last word."),
        "caveat": None,
    }


def _pad(facts, measurements, findings, rules, profile, where,
         context=None):
    """Extend head or tail to the required length.

    Room tone, not digital silence, wherever the file contains some to copy: a
    stretch of pure zeroes after a recorded voice is audible as a hole, and the
    targets that ask for a quiet ending are asking for the room, not for
    nothing.
    """
    rule = rules.get(findings[0]["id"])
    key = "lead_silence_s" if where == "head" else "tail_silence_s"
    have = measurements.get(key) or 0.0
    want = _target_band(rule)
    if want is None or have >= (rule.get("min") or want):
        return None
    needed = round(want - have, 3)

    source = _room_tone_source(measurements)
    if source:
        start, length = source
        copies = int(needed // length) + 1
        return {
            "id": "pad_" + where,
            "kind": "concat",
            "room_tone": {"start": round(start, 3), "length": round(length, 3),
                          "copies": copies, "needed": needed, "where": where},
            "filters": [],
            "description": (
                f"Add {needed:g} s of room tone to the {where}, copied from "
                f"the quiet stretch at {start:.2f} s rather than padding with "
                f"digital silence."),
            "caveat": None,
        }

    filters = ([f"adelay=all=1:delays={int(needed * 1000)}"] if where == "head"
               else [f"apad=pad_dur={needed}"])
    return {
        "id": "pad_" + where,
        "kind": "filter",
        "filters": filters,
        "description": (f"Add {needed:g} s of digital silence to the {where}."),
        "caveat": ("The file contains no quiet stretch long enough to copy as "
                   "room tone, so this pads with silence. Some targets — ACX "
                   "among them — ask for room tone specifically."),
    }


def _room_tone_source(measurements, minimum=0.4):
    """The longest usable stretch of near-silence, as (start, length)."""
    best = None
    for gap in measurements.get("silences") or []:
        length = gap["duration"]
        if length < minimum:
            continue
        # Leave the edges alone: the first and last tenth of a gap usually
        # carries the tail of a word or the breath before one.
        usable = length - 0.2
        if usable < minimum:
            continue
        if best is None or usable > best[1]:
            best = (gap["start"] + 0.1, usable)
    return best


# When a rule allows several codecs, the container decides which one is
# sensible. MP3 inside an MP4 is legal and nothing plays it happily.
CONTAINER_PREFERENCE = {
    "mp4": ["aac", "opus"], "mov": ["aac"], "m4v": ["aac"], "m4a": ["aac"],
    "mkv": ["opus", "aac", "flac", "mp3"], "webm": ["opus"],
    "wav": ["pcm_s16le"], "flac": ["flac"],
}


def _choose_codec(allowed, facts):
    """The codec to encode to: what the file already has when the target allows
    it, otherwise the first allowed codec the container will carry."""
    audio = facts.get("audio") or {}
    current = audio.get("codec")
    if not allowed:
        return current
    if current in allowed:
        return current
    container = (facts.get("container", {}).get("format_name") or "").split(",")[0]
    for candidate in CONTAINER_PREFERENCE.get(container, []):
        if candidate in allowed:
            return candidate
    return allowed[0]


# Constant-bitrate encoders write the number they are given. The variable-rate
# ones treat it as an average and routinely land a few per cent under, which is
# how a file encoded "at 128 kbps" arrives measuring 119 and fails a floor of
# 128. Ask those for a little more.
_APPROXIMATE_ENCODERS = ("aac", "libopus", "libvorbis", "libfdk_aac")


def _bitrate_for(encoder, floor, headroom=1.15, step=8):
    if encoder not in _APPROXIMATE_ENCODERS:
        return int(floor)
    return int(-(-floor * headroom // step) * step)


def _extension(facts, codec_extension):
    """A file that carries picture keeps its container; only an audio-only
    correction is free to change what kind of file it is."""
    if facts.get("video_streams"):
        existing = os.path.splitext(facts.get("path", ""))[1].lstrip(".")
        return existing or codec_extension
    return codec_extension


def _encode(facts, measurements, findings, rules, profile,
            context=None):
    """Container and codec settings, derived from the rules that failed."""
    audio = facts.get("audio") or {}
    codec_rule = rules.get("codec") or {}
    wanted_codec = _choose_codec(codec_rule.get("one_of"), facts)
    settings = ENCODERS.get(wanted_codec)
    if not settings:
        raise CorrectionError(
            f"This tool has no encoder configured for '{wanted_codec}'. "
            f"Known: {', '.join(sorted(ENCODERS))}.")

    args = ["-c:a", settings["codec"]]
    described = ([f"Re-encode the audio as {wanted_codec}"]
                 if wanted_codec != audio.get("codec")
                 else [f"Re-encode the audio, still as {wanted_codec}"])

    bitrate_rule = rules.get("bitrate") or {}
    floor = bitrate_rule.get("min")
    if floor:
        asked = _bitrate_for(settings["codec"], floor)
        args += ["-b:a", f"{asked}k"]
        if asked == int(floor):
            described.append(f"at {asked} kbps")
        else:
            described.append(
                f"at {asked} kbps — a little over the {int(floor)} kbps floor, "
                f"because this encoder treats a bitrate as a target it aims "
                f"near rather than a number it hits")
    mode_rule = rules.get("bitrate_mode") or {}
    if "cbr" in (mode_rule.get("one_of") or []) and \
            settings["codec"] == "libmp3lame":
        # ffmpeg's libmp3lame wrapper writes constant bitrate when it is given
        # a bitrate and no quality target; -abr 0 says so explicitly rather
        # than relying on that staying true.
        args += ["-abr", "0"]
        described.append("constant bitrate")

    rate_rule = rules.get("sample_rate") or {}
    allowed_rates = rate_rule.get("one_of") or []
    if allowed_rates and audio.get("sample_rate") not in allowed_rates:
        args += ["-ar", str(int(allowed_rates[0]))]
        described.append(f"resampled to {int(allowed_rates[0])} Hz")

    channel_rule = rules.get("channels") or {}
    allowed_channels = channel_rule.get("one_of") or []
    if allowed_channels and audio.get("channels") not in allowed_channels:
        args += ["-ac", str(int(allowed_channels[0]))]
        described.append(f"as {int(allowed_channels[0])} channel(s)")

    caveat = None
    if audio.get("codec") not in ("pcm_s16le", "pcm_s24le", "flac", "alac") \
            and wanted_codec not in ("flac", "pcm_s16le"):
        caveat = ("The source is already lossy, so this is a second lossy "
                  "encode. Where you still have the original master, "
                  "correcting that instead will sound better.")

    return {
        "id": "encode",
        "kind": "encode",
        "args": args,
        "extension": _extension(facts, settings["ext"]),
        "description": ", ".join(described) + ".",
        "caveat": caveat,
    }


def _passthrough_encode(facts, profile):
    """Filtered audio has to be encoded; keep the format the file already has."""
    audio = facts.get("audio") or {}
    codec = audio.get("codec")
    settings = ENCODERS.get(codec)
    if settings is None:
        # An unknown source codec becomes 16-bit PCM: lossless, universally
        # readable, and it does not pretend to be the original format.
        settings = ENCODERS["pcm_s16le"]
        args = ["-c:a", settings["codec"]]
        note = ("Written as 16-bit WAV: the corrected audio cannot be copied "
                f"through unchanged, and this tool has no encoder for "
                f"'{codec}'.")
    else:
        args = ["-c:a", settings["codec"]]
        note = None
        if audio.get("bit_rate") and codec in ("mp3", "aac", "opus"):
            args += ["-b:a", f"{int(audio['bit_rate'] / 1000)}k"]
    return {
        "id": "encode",
        "kind": "encode",
        "args": args,
        "extension": _extension(facts, settings["ext"]),
        "addresses": [],
        "description": (f"Re-encode as {codec or settings['codec']}, matching "
                        f"the source." if note is None else note),
        "caveat": ("Changing the audio means it must be encoded again; a "
                   "lossy source re-encoded is lossy twice.")
        if codec in ("mp3", "aac", "opus") else None,
    }


_BUILDERS = {
    "gain": _gain,
    "limit_peak": _limit_peak,
    "loudnorm": _loudnorm,
    "remove_dc": _remove_dc,
    "room_tone_head": _room_tone_head,
    "room_tone_tail": _room_tone_tail,
    "encode": _encode,
}


def in_order(steps):
    """Steps sorted into the order they have to run in."""
    return sorted(steps, key=lambda s: STEP_ORDER.index(s["id"])
                  if s["id"] in STEP_ORDER else len(STEP_ORDER))


def _guard(steps, measurements, rules):
    """Stop a correction from creating a fault the source did not have.

    Raising a quiet recording by seventeen decibels raises its peaks and its DC
    offset by seventeen decibels too. Both can cross a line the original was
    comfortably inside, and a tool that fixed the check you asked about while
    breaking two you did not would be worse than useless. So the arithmetic is
    done in advance and the guard steps are added to the plan, where they can
    be read before anything runs.
    """
    steps = _gain_guards(steps, measurements, rules)
    return in_order(_lossy_headroom(steps))


def _gain_guards(steps, measurements, rules):
    """The two failures a large gain can create on its own."""
    gain = next((s for s in steps if s["id"] == "gain"), None)
    if gain is None:
        return steps
    gain_db = float(gain["filters"][0].split("=")[1].rstrip("dB"))
    have = {s["id"] for s in steps}

    peak_rule = rules.get("peak") or rules.get("true_peak") or {}
    ceiling = peak_rule.get("max")
    predicted = gain.get("predicted_peak_dbfs")
    if ceiling is not None and predicted is not None and \
            predicted > ceiling and "limit_peak" not in have:
        steps.append(_limiter_step(
            ceiling, reason="The source's peaks were within the ceiling; it is "
                            "the gain above that would push them over."))

    dc_rule = rules.get("dc_offset") or {}
    dc_limit = dc_rule.get("max")
    offsets = [abs(c["dc_offset"]) for c in (measurements.get("channels") or [])
               if c.get("dc_offset") is not None]
    if dc_limit is not None and offsets and "remove_dc" not in have:
        after = max(offsets) * (10 ** (gain_db / 20.0))
        if after > dc_limit:
            step = _remove_dc(None, measurements, [], rules, None)
            step["description"] += (
                f" The offset is only {max(offsets):.4f} now, but the gain "
                f"above would take it to about {after:.4f}.")
            step["addresses"] = []
            steps.append(step)

    return steps


# A lossy encoder reconstructs the waveform from a frequency-domain
# approximation, and the reconstruction routinely peaks a little higher than
# what went in. A ceiling met exactly before the encode is missed after it.
LOSSY_ENCODERS = ("aac", "libmp3lame", "libopus", "libvorbis", "libfdk_aac")
LOSSY_HEADROOM_DB = 1.0


def _lossy_headroom(steps):
    """Aim a decibel lower when a lossy encode comes after the ceiling is set."""
    encode = next((s for s in steps if s["kind"] == "encode"), None)
    if encode is None:
        return steps
    codec = encode["args"][encode["args"].index("-c:a") + 1] \
        if "-c:a" in encode["args"] else ""
    if codec not in LOSSY_ENCODERS:
        return steps

    out = []
    for step in steps:
        if step["id"] == "loudnorm" and step.get("loudnorm"):
            step = copy.deepcopy(step)
            step["loudnorm"]["TP"] = round(
                step["loudnorm"]["TP"] - LOSSY_HEADROOM_DB, 2)
            step["description"] += (
                f" The peak ceiling is set {LOSSY_HEADROOM_DB:g} dB low, "
                f"because the {codec} encode that follows will push peaks back "
                f"up by roughly that much.")
        elif step["id"] == "limit_peak":
            step = _limiter_step(
                step["ceiling"], margin=step["margin"] + LOSSY_HEADROOM_DB,
                reason=(f"The extra {LOSSY_HEADROOM_DB:g} dB is headroom for "
                        f"the {codec} encode that follows, which will push "
                        f"peaks back up."))
        out.append(step)
    return out


# ------------------------------------------------------------- the command

def output_path(source, steps, suffix=".preflight", directory=None):
    """Where the corrected copy goes: beside the source unless told otherwise."""
    folder = directory or os.path.dirname(os.path.abspath(source))
    stem, ext = os.path.splitext(os.path.basename(source))
    for step in steps:
        if step.get("extension"):
            ext = "." + step["extension"]
    return os.path.join(folder, stem + suffix + ext)


def build_command(source, destination, steps, facts, ffmpeg="ffmpeg"):
    """The exact argv. Returned before anything runs, and recorded afterwards."""
    filters, concat = [], None
    encode_args = []
    for step in steps:
        if step["kind"] == "filter":
            filters.extend(step["filters"])
        elif step["kind"] == "concat":
            concat = step
        elif step["kind"] == "encode":
            encode_args = list(step["args"])

    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", source]

    if concat:
        graph = _concat_graph(concat["room_tone"], filters)
        command += ["-filter_complex", graph, "-map", "[out]"]
    else:
        command += ["-map", "0:a:0"]
        if filters:
            command += ["-af", ",".join(filters)]

    # Anything that is not the audio being corrected rides along untouched: a
    # video track is copied rather than re-encoded, and cover art stays cover
    # art rather than becoming a one-frame video.
    if facts.get("video_streams"):
        command += ["-map", "0:v", "-c:v", "copy"]
    elif facts.get("cover_art"):
        command += ["-map", "0:v", "-c:v", "copy", "-disposition:v:0",
                    "attached_pic"]
    command += ["-map_metadata", "0", "-map_chapters", "0"]
    command += encode_args
    command.append(destination)
    return command


def _concat_graph(tone, filters):
    """Room tone copied from the file itself, joined onto one end of it.

    The tone is split into as many copies as are needed to cover the gap and
    then trimmed back to length, so a half-second of room is enough to build a
    five-second ending without looping audibly at a fixed period — the trim
    lands mid-copy.
    """
    start, length = tone["start"], tone["length"]
    copies, needed, where = max(1, tone["copies"]), tone["needed"], tone["where"]

    parts = ["[0:a]asplit=2[body][src]"]
    labels = "".join(f"[tone{i}]" for i in range(copies))
    parts.append(
        f"[src]atrim=start={start}:end={round(start + length, 3)},"
        f"asetpts=PTS-STARTPTS,asplit={copies}{labels}")
    if copies > 1:
        parts.append(f"{labels}concat=n={copies}:v=0:a=1,"
                     f"atrim=end={needed},asetpts=PTS-STARTPTS[pad]")
    else:
        parts.append(f"[tone0]atrim=end={needed},asetpts=PTS-STARTPTS[pad]")

    body = "[body]"
    if filters:
        parts.append(f"[body]{','.join(filters)}[bodyf]")
        body = "[bodyf]"
    if where == "head":
        parts.append(f"[pad]{body}concat=n=2:v=0:a=1[out]")
    else:
        parts.append(f"{body}[pad]concat=n=2:v=0:a=1[out]")
    return ";".join(parts)


def measure_loudnorm(source, step, ffmpeg="ffmpeg"):
    """loudnorm's own first pass, whose numbers it needs for the second.

    ebur128 has already measured this file, but loudnorm will not accept
    another filter's figures — it wants its own threshold and offset — so this
    is the one place a second read of the audio is unavoidable.
    """
    target = step["loudnorm"]
    probe_filter = ("loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json"
                    .format(**target))
    command = [ffmpeg, "-hide_banner", "-nostdin", "-i", source,
               "-map", "0:a:0", "-af", probe_filter, "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True,
                            **platform_support.no_console())
    if result.returncode != 0:
        raise CorrectionError("loudnorm's measurement pass failed.")
    measured = _last_json_object(result.stderr)
    if not measured:
        raise CorrectionError(
            "loudnorm did not report its measurements; the file may be too "
            "short to normalise.")
    applied = (
        "loudnorm=I={I}:TP={TP}:LRA={LRA}"
        ":measured_I={input_i}:measured_TP={input_tp}"
        ":measured_LRA={input_lra}:measured_thresh={input_thresh}"
        ":offset={target_offset}:linear=true:print_format=summary"
    ).format(**target, **measured)
    step = copy.deepcopy(step)
    step["filters"] = [applied] + list(step.get("post_filters", []))
    step["measured"] = measured
    step["needs_measurement"] = False
    return step


def _last_json_object(text):
    """loudnorm prints its JSON at the end of a noisy stderr."""
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def prepare(source, steps, ffmpeg="ffmpeg"):
    """Resolve any step that needs its own measurement pass before running."""
    prepared = []
    for step in steps:
        if step.get("needs_measurement"):
            step = measure_loudnorm(source, step, ffmpeg)
        prepared.append(step)
    return prepared


def apply(source, steps, facts, destination=None, ffmpeg="ffmpeg",
          overwrite=False, directory=None, progress=None):
    """Write the corrected copy. Returns (path, command, steps).

    Refuses to write over the source, and refuses to write over anything else
    unless ``overwrite`` says so. Both refusals are deliberate: this tool's one
    promise is that the file you dropped on it is the file you still have.
    """
    if not steps:
        raise CorrectionError("Nothing to correct.")
    steps = prepare(source, steps, ffmpeg)
    destination = destination or output_path(source, steps, directory=directory)

    if os.path.abspath(destination) == os.path.abspath(source):
        raise CorrectionError(
            "The corrected copy would overwrite the source. Choose another "
            "name or folder.")
    if os.path.exists(destination) and not overwrite:
        raise CorrectionError(
            f"{os.path.basename(destination)} already exists. Move it, or ask "
            f"for it to be replaced.")

    command = build_command(source, destination, steps, facts, ffmpeg)
    result = subprocess.run(command, capture_output=True, text=True,
                            **platform_support.no_console())
    if result.returncode != 0:
        detail = [l for l in result.stderr.strip().splitlines() if l.strip()]
        raise CorrectionError(
            "ffmpeg refused the correction: "
            + (detail[-1] if detail else "no reason given."))
    if not os.path.isfile(destination) or os.path.getsize(destination) == 0:
        raise CorrectionError(
            "ffmpeg reported success but wrote nothing usable.")
    return destination, command, steps


def refine(steps, after_measurements, result_after, profile):
    """One corrective iteration, for the step whose effect other steps change.

    A gain is worked out from the whole file's RMS, and then a trim removes
    five seconds of room tone from the end — so the file that comes out is a
    little louder than the file that was aimed at. Rather than pretend the
    first answer was right, the corrected copy is measured and the gain is
    adjusted by exactly the error, and the copy is rebuilt **from the source**.
    Rebuilding from the source rather than from the corrected copy matters: it
    keeps the number of lossy encodes at one however many times this runs.

    Returns (steps, description) or (None, why not).
    """
    rules = {rule["id"]: rule for rule in profile.get("rules", [])}

    # A lossy encoder can hand back peaks a little louder than it was given, so
    # a limiter aimed at the ceiling can still land above it. Aim lower by
    # exactly the overshoot and build again from the source.
    over = [f for f in result_after["findings"]
            if f["status"] == "fail" and f["metric"] in
            ("peak_dbfs", "true_peak_dbfs")]
    limiters = [s for s in steps if s["id"] == "limit_peak"]
    if over:
        finding = over[0]
        ceiling = (rules.get(finding["id"]) or {}).get("max")
        if ceiling is not None and finding["value"] is not None:
            overshoot = finding["value"] - ceiling
            margin = round((limiters[0].get("margin", 0.1) if limiters else 0.1)
                           + overshoot + 0.1, 2)
            replacement = _limiter_step(
                ceiling, margin=margin,
                reason=(f"Aimed {margin:g} dB under the ceiling because the "
                        f"first corrected copy came back {overshoot:+.2f} dB "
                        f"over it — a lossy encoder can hand back peaks a "
                        f"little louder than it was given."))
            if limiters:
                out = [replacement if s["id"] == "limit_peak" else s
                       for s in steps]
            else:
                out = in_order(list(steps) + [replacement])
            return out, f"limiter aimed {margin:g} dB under the ceiling"

    gain_steps = [s for s in steps if s["id"] == "gain"]
    if not gain_steps:
        return None, "no gain step to adjust"

    still_failing = [f for f in result_after["findings"]
                     if f["status"] == "fail" and f.get("fix") == "gain"]
    if not still_failing:
        return None, "nothing a different gain would fix"

    rule = rules.get(still_failing[0]["id"]) or {}
    aim = _target_band(rule)
    measured = after_measurements.get("rms_dbfs")
    if aim is None or measured is None:
        return None, "the corrected copy could not be measured"

    error = round(aim - measured, 2)
    if abs(error) < 0.05:
        return None, "already within a twentieth of a decibel"

    step = gain_steps[0]
    current = float(step["filters"][0].split("=")[1].rstrip("dB"))
    adjusted = round(current + error, 2)
    step = copy.deepcopy(step)
    step["filters"] = [f"volume={adjusted:+g}dB"]
    step["description"] = (
        f"Apply {adjusted:+g} dB of gain (adjusted by {error:+g} dB after "
        f"measuring the first corrected copy, which the trims had left "
        f"{-error:+g} dB off target).")
    out = [step if s["id"] == "gain" else s for s in steps]
    return out, f"gain adjusted by {error:+g} dB"


def recipe(source, destination, steps, command, report_envelope=None):
    """The JSON record of what was done, readable and repeatable by hand."""
    return {
        "schema": 1,
        "tool": "Media Preflight",
        "source": os.path.abspath(source),
        "output": os.path.abspath(destination) if destination else None,
        "target": (report_envelope or {}).get("target"),
        "operations": [
            {"id": step["id"], "description": step["description"],
             "caveat": step.get("caveat"),
             "filters": step.get("filters", []),
             "args": step.get("args", []),
             "addresses": step.get("addresses", [])}
            for step in steps],
        "command": command,
    }
