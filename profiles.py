"""Delivery targets, written as data rather than as code.

A profile is a list of rules, each naming a metric, a band, and what to say
when the file falls outside it. Nothing here knows how a metric is obtained —
that is checks.py's table — which is what makes a custom target a JSON file
somebody writes in a text editor rather than a patch to this tool.

On the numbers
--------------
Every built-in profile carries ``source`` and ``checked``. These thresholds are
read off published specifications, and published specifications move. Two of
them — the social platforms — are not formally published at all and are marked
``informal``; they are the widely reported figures, offered as a starting point
you should adjust rather than as a promise about what the platform does today.
The tool's job is to measure your file exactly and compare it against a number
you can see and change. It is not, and cannot be, an oracle for somebody else's
current ingest rules.
"""

from __future__ import annotations

import copy
import json
import os


# ---------------------------------------------------------------- rule kinds
#
# min/max      numeric band, inclusive; outside it is `severity`
# warn_min/max a looser band inside which the finding is a warning
# one_of       the value must appear in this list
# equals       exact match
# forbid       the metric is a flag that must be false
# require      the metric is a flag that must be true
#
# `fix` names a correction corrections.py knows how to plan. A rule with no
# `fix` is reported and left alone, which is the honest answer for most
# structural faults.


ACX = {
    "id": "acx",
    "label": "Audiobook — ACX",
    "kind": "audio",
    "summary": "Retail audiobook delivery: RMS window, hard peak ceiling, "
               "quiet noise floor, room tone at both ends.",
    "source": "ACX audio submission requirements",
    "checked": "2026-09",
    "confidence": "published",
    "options": {"silence_threshold_db": -50.0, "silence_min_s": 0.25},
    "rules": [
        {"id": "rms", "metric": "rms_dbfs", "label": "RMS level",
         "unit": "dBFS", "min": -23.0, "max": -18.0, "severity": "fail",
         "fix": "gain",
         "note": "ACX specifies RMS, not LUFS. The two disagree by several "
                 "decibels on speech, so this is measured as RMS."},
        {"id": "peak", "metric": "peak_dbfs", "label": "Peak level",
         "unit": "dBFS", "max": -3.0, "severity": "fail", "fix": "limit_peak"},
        {"id": "noise_floor", "metric": "noise_floor_dbfs",
         "label": "Noise floor", "unit": "dBFS RMS", "max": -60.0,
         "severity": "fail",
         "note": "Broadband noise reduction is not something this tool will "
                 "do for you — it changes the recording. Re-record or treat "
                 "the room."},
        {"id": "head_room_tone", "metric": "lead_silence_s",
         "label": "Opening room tone", "unit": "s", "min": 0.5, "max": 1.0,
         "severity": "fail", "fix": "room_tone_head"},
        {"id": "tail_room_tone", "metric": "tail_silence_s",
         "label": "Closing room tone", "unit": "s", "min": 1.0, "max": 5.0,
         "severity": "fail", "fix": "room_tone_tail"},
        {"id": "duration", "metric": "duration_min", "label": "File length",
         "unit": "min", "max": 120.0, "severity": "fail"},
        {"id": "codec", "metric": "audio_codec", "label": "Codec",
         "one_of": ["mp3"], "severity": "fail", "fix": "encode"},
        {"id": "bitrate", "metric": "audio_bitrate_kbps", "label": "Bitrate",
         "unit": "kbps", "min": 192.0, "severity": "fail", "fix": "encode"},
        {"id": "bitrate_mode", "metric": "bitrate_mode",
         "label": "Bitrate mode", "one_of": ["cbr"], "severity": "fail",
         "fix": "encode"},
        {"id": "sample_rate", "metric": "sample_rate", "label": "Sample rate",
         "unit": "Hz", "one_of": [44100], "severity": "fail", "fix": "encode"},
        {"id": "channels", "metric": "channels", "label": "Channels",
         "one_of": [1, 2], "severity": "fail", "fix": "encode"},
    ],
}


EBU_R128 = {
    "id": "ebu_r128",
    "label": "Broadcast — EBU R128",
    "kind": "audio",
    "summary": "European broadcast loudness: -23 LUFS programme loudness, "
               "-1 dBTP ceiling.",
    "source": "EBU R 128 (loudness normalisation and permitted maximum level)",
    "checked": "2026-09",
    "confidence": "published",
    "rules": [
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -24.0, "max": -22.0, "warn_min": -23.5, "warn_max": -22.5,
         "severity": "fail", "fix": "loudnorm",
         "note": "R 128 sets -23.0 LUFS with a tolerance of ±0.5 LU; ±1.0 LU "
                 "is treated here as the outer limit."},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "severity": "fail", "fix": "loudnorm"},
        {"id": "range", "metric": "loudness_range_lu", "label": "Loudness range",
         "unit": "LU", "max": 20.0, "severity": "warn",
         "note": "R 128 sets no hard limit on LRA. A very wide range is "
                 "flagged because it usually means the quiet passages will be "
                 "inaudible in a car."},
        {"id": "short_term", "metric": "short_term_excursions",
         "label": "Short-term loudness", "unit": "LUFS", "max": -15.0,
         "severity": "warn",
         "note": "Sustained short-term loudness far above the programme "
                 "target — where a listener reaches for the volume."},
    ],
}


SPOTIFY_PODCAST = {
    "id": "spotify_podcast",
    "label": "Podcast — Spotify",
    "kind": "audio",
    "summary": "-14 LUFS integrated, -1 dBTP ceiling, stereo or mono AAC/MP3.",
    "source": "Spotify loudness normalisation guidance for podcast delivery",
    "checked": "2026-09",
    "confidence": "published",
    "rules": [
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -16.0, "max": -12.0, "warn_min": -15.0, "warn_max": -13.0,
         "severity": "fail", "fix": "loudnorm"},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "severity": "fail", "fix": "loudnorm"},
        {"id": "codec", "metric": "audio_codec", "label": "Codec",
         "one_of": ["mp3", "aac"], "severity": "fail", "fix": "encode"},
        {"id": "bitrate", "metric": "audio_bitrate_kbps", "label": "Bitrate",
         "unit": "kbps", "min": 128.0, "warn_min": 192.0, "severity": "fail",
         "fix": "encode"},
        {"id": "sample_rate", "metric": "sample_rate", "label": "Sample rate",
         "unit": "Hz", "one_of": [44100, 48000], "severity": "fail",
         "fix": "encode"},
        {"id": "silence", "metric": "longest_mid_silence_s",
         "label": "Longest gap", "unit": "s", "max": 8.0, "severity": "warn",
         "note": "A long gap mid-episode is usually a dropped edit rather "
                 "than a pause."},
    ],
}


YOUTUBE = {
    "id": "youtube",
    "label": "YouTube",
    "kind": "audio",
    "summary": "-14 LUFS after normalisation, -1 dBTP ceiling, AAC at 48 kHz.",
    "source": "YouTube loudness normalisation behaviour and recommended "
              "upload encoding settings",
    "checked": "2026-09",
    "confidence": "published",
    "rules": [
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -17.0, "max": -12.0, "warn_min": -15.0, "warn_max": -13.0,
         "severity": "warn", "fix": "loudnorm",
         "note": "YouTube turns loud uploads down rather than rejecting them. "
                 "Mastering above the target buys nothing and costs headroom, "
                 "so this is a warning, not a failure."},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "severity": "fail", "fix": "loudnorm"},
        {"id": "codec", "metric": "audio_codec", "label": "Audio codec",
         "one_of": ["aac"], "severity": "warn", "fix": "encode"},
        {"id": "sample_rate", "metric": "sample_rate", "label": "Sample rate",
         "unit": "Hz", "one_of": [48000, 44100], "severity": "warn",
         "fix": "encode"},
        {"id": "bitrate", "metric": "audio_bitrate_kbps", "label": "Audio bitrate",
         "unit": "kbps", "min": 128.0, "warn_min": 192.0, "severity": "warn",
         "fix": "encode"},
    ],
}


SOCIAL_VERTICAL = {
    "id": "social_vertical",
    "label": "Instagram / TikTok",
    "kind": "audio",
    "summary": "Around -14 LUFS with a -1 dBTP ceiling. Neither platform "
               "publishes a specification.",
    "source": "Not formally published; widely reported behaviour",
    "checked": "2026-09",
    "confidence": "informal",
    "rules": [
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -18.0, "max": -10.0, "warn_min": -15.0, "warn_max": -13.0,
         "severity": "warn", "fix": "loudnorm",
         "note": "Treat this band as a sanity check, not a specification. "
                 "Neither platform documents its ingest loudness."},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "severity": "fail", "fix": "loudnorm"},
        {"id": "channels", "metric": "channels", "label": "Channels",
         "one_of": [1, 2], "severity": "fail", "fix": "encode"},
        {"id": "abrupt_end", "metric": "ends_abruptly",
         "label": "Ending", "forbid": True, "severity": "warn",
         "note": "The file is still at programme level on its last sample; "
                 "on a looping feed that reads as a cut-off."},
    ],
}


GENERIC_WEB = {
    "id": "web",
    "label": "Generic web video",
    "kind": "audio",
    "summary": "The faults that are faults everywhere: clipping, dead "
               "channels, DC offset, silence where there should be sound.",
    "source": "This tool's own defaults",
    "checked": "2026-09",
    "confidence": "informal",
    "rules": [
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "warn_max": -2.0, "severity": "warn",
         "fix": "loudnorm"},
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -24.0, "max": -9.0, "severity": "warn", "fix": "loudnorm"},
    ],
}


# Rules every target inherits. These are not delivery requirements; they are
# the faults that are wrong on any target and that a person would want to know
# about before shipping, so they are never a `fail` on their own account.
UNIVERSAL = [
    {"id": "clipping", "metric": "clipping_seconds",
     "label": "Clipped samples", "unit": "s", "max": 0.0, "severity": "warn",
     "fix": "limit_peak",
     "note": "Consecutive samples pinned at full scale. Once a waveform is "
             "flattened the detail is gone; a limiter stops it getting worse "
             "but does not put it back."},
    {"id": "silent_channel", "metric": "silent_channel_count",
     "label": "Silent channels", "max": 0.0, "severity": "fail",
     "note": "One side of the file carries no sound at all."},
    {"id": "channel_balance", "metric": "channel_rms_spread_db",
     "label": "Channel balance", "unit": "dB", "max": 6.0, "severity": "warn"},
    {"id": "dc_offset", "metric": "dc_offset_max", "label": "DC offset",
     "max": 0.01, "severity": "warn", "fix": "remove_dc",
     "note": "A constant bias in the waveform. It costs headroom and can "
             "click at edit points."},
    {"id": "dual_mono", "metric": "phase_min", "label": "Stereo content",
     "min": -0.5, "severity": "warn",
     "note": "Sustained negative correlation between the two channels: the "
             "sides are out of phase and will cancel in mono."},
    {"id": "av_duration", "metric": "av_duration_gap_s",
     "label": "Audio/video duration", "unit": "s", "max": 0.5,
     "severity": "warn",
     "note": "The picture and the sound do not end together."},
]


BUILT_IN = [ACX, EBU_R128, SPOTIFY_PODCAST, YOUTUBE, SOCIAL_VERTICAL,
            GENERIC_WEB]

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, "profiles")


def all_profiles(include_custom=True):
    """Built-in targets first, then anything dropped into profiles/."""
    found = {p["id"]: with_universal(p) for p in BUILT_IN}
    order = [p["id"] for p in BUILT_IN]
    if include_custom and os.path.isdir(PROFILE_DIR):
        for name in sorted(os.listdir(PROFILE_DIR)):
            if not name.endswith(".json"):
                continue
            try:
                profile = load(os.path.join(PROFILE_DIR, name))
            except (OSError, ValueError):
                continue
            if profile["id"] not in found:
                order.append(profile["id"])
            found[profile["id"]] = profile
    return [found[i] for i in order]


def get(identifier):
    """A profile by id, or a path to a JSON one. Raises ValueError if neither."""
    if identifier and (identifier.endswith(".json") or
                       os.path.sep in str(identifier)):
        return load(identifier)
    for profile in all_profiles():
        if profile["id"] == identifier:
            return profile
    known = ", ".join(p["id"] for p in all_profiles())
    raise ValueError(f"No such target: {identifier}. Available: {known}")


def with_universal(profile):
    """Attach the always-on rules, without letting them shadow the target's."""
    profile = copy.deepcopy(profile)
    taken = {rule["id"] for rule in profile.get("rules", [])}
    profile["rules"] = list(profile.get("rules", [])) + [
        copy.deepcopy(rule) for rule in UNIVERSAL if rule["id"] not in taken]
    profile.setdefault("options", {})
    return profile


def load(path):
    """Read a custom profile, checking it well enough to fail with a sentence."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    validate(data)
    return with_universal(data)


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("A profile must be a JSON object.")
    for field in ("id", "label", "rules"):
        if field not in data:
            raise ValueError(f"A profile needs a '{field}' field.")
    if not isinstance(data["rules"], list) or not data["rules"]:
        raise ValueError("A profile's 'rules' must be a non-empty list.")
    for index, rule in enumerate(data["rules"]):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} is not an object.")
        for field in ("id", "metric", "label"):
            if field not in rule:
                raise ValueError(
                    f"Rule {index} ({rule.get('id', '?')}) needs '{field}'.")
        bounds = ("min", "max", "warn_min", "warn_max", "one_of", "equals",
                  "forbid", "require")
        if not any(field in rule for field in bounds):
            raise ValueError(
                f"Rule '{rule['id']}' states no requirement; give it one of "
                + ", ".join(bounds) + ".")
        severity = rule.get("severity", "fail")
        if severity not in ("fail", "warn", "info"):
            raise ValueError(
                f"Rule '{rule['id']}' has severity '{severity}'; use fail, "
                "warn or info.")
    return True


def save(profile, path):
    """Write a profile out as JSON, universal rules stripped back off."""
    universal = {rule["id"] for rule in UNIVERSAL}
    out = copy.deepcopy(profile)
    out["rules"] = [r for r in out.get("rules", [])
                    if r["id"] not in universal or r.get("customised")]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
        handle.write("\n")
    return path
