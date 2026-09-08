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
#
# `basis` says where a threshold came from, per rule rather than per profile —
# because within one target some numbers are published and some are not.
# YouTube publishes its encoding settings and no loudness figure at all; the
# -14 LUFS everybody quotes is measured behaviour. A report that presented both
# as equally authoritative would be misleading about the more important one.
#
#   published   quoted from the target's own specification
#   observed    measured behaviour the target does not publish
#   house       this tool's own threshold, offered as a starting point
#
# A rule with no `basis` inherits the profile's `confidence`.


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
         "basis": "published", "fix": "gain",
         "note": "ACX specifies RMS, not LUFS. The two disagree by several "
                 "decibels on speech, so this is measured as RMS."},
        {"id": "peak", "metric": "peak_dbfs", "label": "Peak level",
         "unit": "dBFS", "max": -3.0, "severity": "fail",
         "basis": "published", "fix": "limit_peak"},
        {"id": "noise_floor", "metric": "noise_floor_dbfs",
         "label": "Noise floor", "unit": "dBFS RMS", "max": -60.0,
         "severity": "fail", "basis": "published",
         "note": "Broadband noise reduction is not something this tool will "
                 "do for you — it changes the recording. Re-record or treat "
                 "the room."},
        {"id": "head_room_tone", "metric": "lead_silence_s",
         "label": "Opening room tone", "unit": "s", "min": 1.0, "max": 5.0,
         "target": 1.5, "severity": "warn", "basis": "published",
         "fix": "room_tone_head",
         "note": "ACX's page says: \"We recommend between 1 and 5 seconds of "
                 "room tone at the beginning and end of each file.\" It is a "
                 "recommendation, so this warns rather than fails. Much "
                 "guidance elsewhere says 0.5 to 1 second at the head; that "
                 "figure appears nowhere on ACX's own page."},
        {"id": "tail_room_tone", "metric": "tail_silence_s",
         "label": "Closing room tone", "unit": "s", "min": 1.0, "max": 5.0,
         "target": 3.0, "severity": "warn", "basis": "published",
         "fix": "room_tone_tail",
         "note": "Same sentence as the opening. Five seconds is also the "
                 "stated maximum spacing, so the upper end is firmer than the "
                 "lower."},
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
    "set_rules": [
        {"id": "channels", "metric": "set_channels_distinct",
         "label": "Channel count across the title", "max": 1.0,
         "severity": "fail", "basis": "published",
         "note": "ACX's page says: \"All files must be in the same channel "
                 "format (all mono or all stereo).\" A title where one chapter "
                 "is stereo passes every per-file check and is rejected on "
                 "submission."},
        {"id": "sample_rate", "metric": "set_sample_rate_distinct",
         "label": "Sample rate across the title", "max": 1.0,
         "severity": "fail"},
        {"id": "codec", "metric": "set_codec_distinct",
         "label": "Codec across the title", "max": 1.0, "severity": "fail"},
        {"id": "bitrate_mode", "metric": "set_bitrate_mode_distinct",
         "label": "Bitrate mode across the title", "max": 1.0,
         "severity": "warn"},
        {"id": "loudness", "metric": "set_loudness_spread_db",
         "label": "Loudness spread across the title", "unit": "dB",
         "max": 3.0, "severity": "warn", "basis": "house",
         "note": "ACX asks that a title be \"consistent in sound and "
                 "formatting … including audio levels\", without stating a "
                 "number; three decibels is this tool's. A listener who "
                 "adjusts the volume between chapters has been given a reason "
                 "to."},
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
         "severity": "fail", "basis": "published", "fix": "loudnorm",
         "note": "R 128 sets -23.0 LUFS with a normal tolerance of ±0.5 LU — "
                 "the inner band here — and permits ±1.0 LU for live "
                 "programmes where the normal tolerance is not practical, "
                 "which is the outer one."},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "severity": "fail",
         "basis": "published", "fix": "loudnorm"},
        {"id": "range", "metric": "loudness_range_lu", "label": "Loudness range",
         "unit": "LU", "max": 20.0, "severity": "warn", "basis": "house",
         "note": "R 128 sets no hard limit on LRA. A very wide range is "
                 "flagged because it usually means the quiet passages will be "
                 "inaudible in a car."},
        {"id": "short_term", "metric": "short_term_excursions",
         "label": "Short-term loudness", "unit": "LUFS", "max": -15.0,
         "severity": "warn", "basis": "house",
         "note": "Sustained short-term loudness far above the programme "
                 "target — where a listener reaches for the volume."},
    ],
}


SPOTIFY_PODCAST = {
    "id": "spotify_podcast",
    "label": "Podcast — Spotify",
    "kind": "audio",
    "summary": "-14 LUFS integrated, -1 dBTP ceiling, stereo or mono AAC/MP3. "
               "Spotify's published figure is for music playback, not podcast "
               "delivery.",
    "source": "Spotify loudness normalisation (published for music playback); "
              "Spotify publishes no separate podcast delivery figure",
    "checked": "2026-09",
    "confidence": "informal",
    "rules": [
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -16.0, "max": -12.0, "warn_min": -15.0, "warn_max": -13.0,
         "severity": "warn", "basis": "observed", "fix": "loudnorm",
         "note": "Spotify publishes \"we adjust tracks to -14 dB LUFS\" as "
                 "music playback normalisation, not as a delivery requirement "
                 "for podcasts, and does not publish a podcast figure at all. "
                 "Common practice finishes speech nearer -16 LUFS. Treat this "
                 "band as a sanity check and move it if your publisher says "
                 "otherwise."},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "warn_max": -2.0, "severity": "fail",
         "basis": "published", "fix": "loudnorm",
         "note": "Spotify asks for true peak below -1 dBTP, and below -2 dBTP "
                 "for masters louder than -14 LUFS — which is why anything "
                 "between the two warns rather than passing quietly."},
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
    "set_rules": [
        {"id": "loudness", "metric": "set_loudness_spread_db",
         "label": "Loudness across the season", "unit": "dB", "max": 3.0,
         "severity": "warn", "basis": "house",
         "note": "Episodes are played back to back in an app that normalises "
                 "each one; a wide spread between them is audible on the "
                 "handover."},
        {"id": "channels", "metric": "set_channels_distinct",
         "label": "Channel count across the season", "max": 1.0,
         "severity": "warn"},
    ],
}


YOUTUBE = {
    "id": "youtube",
    "label": "YouTube",
    "kind": "audio",
    "summary": "Published upload encoding settings, plus the -14 LUFS "
               "normalisation everybody measures and YouTube does not publish.",
    "source": "YouTube recommended upload encoding settings (published); the "
              "loudness figure is measured behaviour, not documented",
    "checked": "2026-09",
    "confidence": "published",
    "rules": [
        {"id": "integrated", "metric": "integrated_lufs",
         "label": "Integrated loudness", "unit": "LUFS",
         "min": -17.0, "max": -12.0, "warn_min": -15.0, "warn_max": -13.0,
         "severity": "warn", "basis": "observed", "fix": "loudnorm",
         "note": "YouTube's own encoding guide states no loudness figure; "
                 "-14 LUFS is what its normalisation is measured to do. It "
                 "turns loud uploads down rather than rejecting them, so "
                 "mastering above the target buys nothing and costs headroom."},
        {"id": "true_peak", "metric": "true_peak_dbfs", "label": "True peak",
         "unit": "dBTP", "max": -1.0, "severity": "fail", "basis": "house",
         "fix": "loudnorm",
         "note": "YouTube does not publish a peak ceiling. A decibel of "
                 "headroom is what stops its AAC encode handing peaks back "
                 "above full scale."},
        {"id": "codec", "metric": "audio_codec", "label": "Audio codec",
         "one_of": ["aac", "opus"], "severity": "warn", "basis": "published",
         "fix": "encode",
         "note": "YouTube's guide names AAC-LC, Opus or Eclipsa Audio."},
        {"id": "sample_rate", "metric": "sample_rate", "label": "Sample rate",
         "unit": "Hz", "one_of": [48000, 44100], "severity": "warn",
         "basis": "published", "fix": "encode",
         "note": "48 kHz is what the guide recommends; 44.1 is accepted here "
                 "because it is what most music-led uploads arrive as."},
        {"id": "bitrate", "metric": "audio_bitrate_kbps", "label": "Audio bitrate",
         "unit": "kbps", "min": 128.0, "severity": "warn",
         "basis": "published", "fix": "encode",
         "note": "YouTube recommends 128 kbps for mono and 384 for stereo. A "
                 "rule cannot vary with channel count, so this checks the "
                 "lower of the two; a stereo upload at 128 meets the letter "
                 "of this check and not the recommendation."},
        {"id": "fast_start", "metric": "fast_start", "label": "Fast start",
         "require": True, "severity": "warn", "basis": "published",
         "note": "YouTube's guide asks for the moov atom at the front of the "
                 "file. Behind it, nothing can start playing until the whole "
                 "file has downloaded. ffmpeg writes it there with "
                 "-movflags +faststart."},
        {"id": "video_codec", "metric": "video_codec", "label": "Video codec",
         "one_of": ["h264", "vp9", "av1", "hevc"], "severity": "warn",
         "basis": "house",
         "note": "The guide recommends H.264 High profile; the others are "
                 "accepted here because YouTube ingests them happily."},
        {"id": "pix_fmt", "metric": "pix_fmt", "label": "Pixel format",
         "one_of": ["yuv420p", "yuv420p10le"], "severity": "warn",
         "basis": "house",
         "note": "Anything else is re-encoded on ingest, and 4:2:2 and 4:4:4 "
                 "sources gain nothing by being uploaded that way."},
        {"id": "height", "metric": "video_height", "label": "Frame height",
         "unit": "px", "min": 720, "severity": "warn", "basis": "house",
         "note": "Below 720 lines the encoder is given less to work with than "
                 "the platform's own presets expect."},
        {"id": "frame_rate_mode", "metric": "frame_rate_mode",
         "label": "Frame rate mode", "one_of": ["cfr"], "severity": "warn",
         "basis": "house",
         "note": "YouTube asks only that you upload at the rate you recorded "
                 "at. Variable frame rate satisfies that and quietly ruins "
                 "anything downstream assuming a constant one — most editing "
                 "software, and every burn-in workflow."},
        {"id": "interlaced", "metric": "interlaced", "label": "Interlacing",
         "forbid": True, "severity": "fail", "basis": "published",
         "note": "YouTube's guide is explicit: interlaced content must be "
                 "deinterlaced before uploading. Left alone it is "
                 "deinterlaced on ingest anyway, by a filter you did not "
                 "choose. This is measured from the picture as well as read "
                 "from the header, because the header is often wrong."},
        {"id": "field_order", "metric": "field_order_disagrees",
         "label": "Field order flag", "forbid": True, "severity": "warn",
         "basis": "house",
         "note": "The header and the picture disagree about interlacing. "
                 "Whichever is wrong, something downstream will believe the "
                 "header and comb or deinterlace accordingly."},
        {"id": "telecine", "metric": "telecine_ratio",
         "label": "Telecine", "max": 0.05, "severity": "warn",
         "basis": "house",
         "note": "Repeated fields: film shot at 24 frames pulled up to 30 "
                 "and left that way. It judders, and it survives every "
                 "re-encode that does not undo it."},
        {"id": "trailing_black", "metric": "trailing_black_s",
         "label": "Black at the end", "unit": "s", "max": 3.0,
         "severity": "warn", "basis": "house",
         "note": "A long black tail is usually a render that ran past the "
                 "edit, and it is what the thumbnail picker sees."},
        {"id": "frozen", "metric": "longest_frozen_s",
         "label": "Frozen picture", "unit": "s", "max": 4.0,
         "severity": "warn", "basis": "house",
         "note": "A still frame is legitimate over a title card and a fault "
                 "in the middle of a shot; this cannot tell them apart."},
        {"id": "uncaptioned", "metric": "caption_uncaptioned_speech_s",
         "label": "Sound with no caption", "unit": "s", "max": 0.0,
         "severity": "warn", "basis": "house",
         "note": "Stretches of sound longer than six seconds that no cue "
                 "covers. Music and atmosphere are legitimately uncaptioned, "
                 "so this finds passages to look at rather than faults — but "
                 "it finds them in a two-hour recording in seconds, which is "
                 "the part nobody does by hand."},
        {"id": "caption_drift", "metric": "caption_drift_s",
         "label": "Caption timing", "unit": "s", "min": -0.4, "max": 0.4,
         "severity": "warn", "basis": "house",
         "note": "Every cue matched to the nearest moment sound starts, and "
                 "the median of those offsets taken. A whole file out by the "
                 "same amount is a sync error; a scattering of cues that sit "
                 "mid-sentence is not, which is why this is a median and "
                 "carries a confidence figure."},
        {"id": "caption_orphans", "metric": "caption_over_silence",
         "label": "Captions over silence", "max": 0.0, "severity": "warn",
         "basis": "house",
         "note": "Cues that play while nothing is audible — what drift looks "
                 "like from the other end."},
        {"id": "flashing", "metric": "flash_regions",
         "label": "Flashing risk", "max": 0.0, "severity": "warn",
         "basis": "house",
         "note": "A screening heuristic, not a compliance test: it counts "
                 "large frame-to-frame changes in average luminance and flags "
                 "any second holding three or more, which is where WCAG draws "
                 "its general flash threshold. It does no spatial analysis and "
                 "knows nothing about the separate red-flash rule. Look at "
                 "these passages; do not treat silence here as a pass."},
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
        {"id": "height", "metric": "video_height", "label": "Frame height",
         "unit": "px", "min": 1080, "severity": "warn"},
        {"id": "aspect", "metric": "aspect_ratio", "label": "Aspect ratio",
         "min": 0.5, "max": 0.5626, "severity": "warn",
         "note": "Between 9:16 and 9:16-ish. A landscape upload is letterboxed "
                 "into a fraction of the screen."},
        {"id": "frame_rate_mode", "metric": "frame_rate_mode",
         "label": "Frame rate mode", "one_of": ["cfr"], "severity": "warn"},
        {"id": "leading_black", "metric": "leading_black_s",
         "label": "Black at the start", "unit": "s", "max": 0.2,
         "severity": "warn",
         "note": "The first frame is the thumbnail and the first thing a "
                 "scrolling viewer sees. Black is a wasted second."},
        {"id": "flashing", "metric": "flash_regions",
         "label": "Flashing risk", "max": 0.0, "severity": "warn",
         "note": "A screening heuristic, not a compliance test — see the "
                 "documentation before relying on it either way."},
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
        {"id": "fast_start", "metric": "fast_start", "label": "Fast start",
         "require": True, "severity": "warn",
         "note": "The index sits behind the media, so nothing can start "
                 "playing until the whole file has downloaded. Rewrite it "
                 "with -movflags +faststart."},
        {"id": "frame_rate_mode", "metric": "frame_rate_mode",
         "label": "Frame rate mode", "one_of": ["cfr"], "severity": "warn"},
        {"id": "leading_black", "metric": "leading_black_s",
         "label": "Black at the start", "unit": "s", "max": 1.0,
         "severity": "warn"},
        {"id": "trailing_black", "metric": "trailing_black_s",
         "label": "Black at the end", "unit": "s", "max": 3.0,
         "severity": "warn"},
        {"id": "frozen", "metric": "longest_frozen_s",
         "label": "Frozen picture", "unit": "s", "max": 5.0,
         "severity": "warn"},
        {"id": "field_order", "metric": "field_order_disagrees",
         "label": "Field order flag", "forbid": True, "severity": "warn",
         "note": "The header and the picture disagree about interlacing."},
        {"id": "telecine", "metric": "telecine_ratio",
         "label": "Telecine", "max": 0.05, "severity": "warn",
         "note": "Repeated fields — 24-frame film pulled up to 30 and left "
                 "that way."},
        {"id": "uncaptioned", "metric": "caption_uncaptioned_speech_s",
         "label": "Sound with no caption", "unit": "s", "max": 0.0,
         "severity": "warn", "basis": "house",
         "note": "Stretches of sound longer than six seconds that no cue "
                 "covers. Music and atmosphere are legitimately uncaptioned, "
                 "so this finds passages to look at rather than faults — but "
                 "it finds them in a two-hour recording in seconds, which is "
                 "the part nobody does by hand."},
        {"id": "caption_drift", "metric": "caption_drift_s",
         "label": "Caption timing", "unit": "s", "min": -0.4, "max": 0.4,
         "severity": "warn", "basis": "house",
         "note": "Every cue matched to the nearest moment sound starts, and "
                 "the median of those offsets taken. A whole file out by the "
                 "same amount is a sync error; a scattering of cues that sit "
                 "mid-sentence is not, which is why this is a median and "
                 "carries a confidence figure."},
        {"id": "caption_orphans", "metric": "caption_over_silence",
         "label": "Captions over silence", "max": 0.0, "severity": "warn",
         "basis": "house",
         "note": "Cues that play while nothing is audible — what drift looks "
                 "like from the other end."},
        {"id": "flashing", "metric": "flash_regions",
         "label": "Flashing risk", "max": 0.0, "severity": "warn",
         "note": "A screening heuristic, not a compliance test — it finds the "
                 "passages worth looking at, and cannot clear a programme."},
    ],
}


# Rules every target inherits. These are not delivery requirements; they are
# the faults that are wrong on any target and that a person would want to know
# about before shipping, so they are never a `fail` on their own account.
UNIVERSAL = [
    {"id": "clipping", "metric": "clipping_seconds",
     "label": "Clipped samples", "unit": "s", "max": 0.0, "severity": "warn", "basis": "house",
     "fix": "limit_peak",
     "note": "Consecutive samples pinned at full scale. Once a waveform is "
             "flattened the detail is gone; a limiter stops it getting worse "
             "but does not put it back."},
    {"id": "silent_channel", "metric": "silent_channel_count",
     "label": "Silent channels", "max": 0.0, "severity": "fail", "basis": "house",
     "note": "One side of the file carries no sound at all."},
    {"id": "channel_balance", "metric": "channel_rms_spread_db",
     "label": "Channel balance", "unit": "dB", "max": 6.0, "severity": "warn", "basis": "house"},
    {"id": "dc_offset", "metric": "dc_offset_max", "label": "DC offset",
     "max": 0.01, "severity": "warn", "basis": "house", "fix": "remove_dc",
     "note": "A constant bias in the waveform. It costs headroom and can "
             "click at edit points."},
    {"id": "dual_mono", "metric": "phase_min", "label": "Stereo content",
     "min": -0.5, "severity": "warn", "basis": "house",
     "note": "Sustained negative correlation between the two channels: the "
             "sides are out of phase and will cancel in mono."},
    {"id": "av_duration", "metric": "av_duration_gap_s",
     "label": "Audio/video duration", "unit": "s", "max": 0.5,
     "severity": "warn", "basis": "house",
     "note": "The picture and the sound do not end together."},
    {"id": "caption_overlaps", "metric": "caption_overlaps",
     "label": "Overlapping captions", "max": 0.0, "severity": "fail", "basis": "house",
     "note": "Two cues on screen at once. Players resolve this differently "
             "and none of them resolve it the way you meant."},
    {"id": "caption_bad_timing", "metric": "caption_bad_timing",
     "label": "Impossible caption timings", "max": 0.0, "severity": "fail", "basis": "house",
     "note": "A cue that ends before it starts, or lasts no time at all."},
    {"id": "caption_past_end", "metric": "caption_past_end_s",
     "label": "Captions past the end", "unit": "s", "max": 0.5,
     "severity": "warn", "basis": "house",
     "note": "Cues timed beyond the last frame — usually a caption file left "
             "over from a longer cut."},
    {"id": "caption_missing_fonts", "metric": "caption_missing_fonts",
     "label": "Missing subtitle fonts", "max": 0.0, "severity": "warn", "basis": "house",
     "note": "Named by the subtitle file and not installed on this machine. "
             "Whether it matters depends on where it will be rendered."},
]


SUBTITLES = {
    "id": "subtitles",
    "label": "Subtitles — readability",
    "kind": "captions",
    "summary": "Reading speed, line length and cue timing, against the "
               "conventions most subtitling guidance is written in.",
    "source": "Common subtitling practice, not a platform requirement",
    "checked": "2026-09",
    "confidence": "informal",
    "rules": [
        {"id": "reading_speed", "metric": "caption_max_cps",
         "label": "Reading speed", "unit": "chars/s", "max": 21.0,
         "warn_max": 17.0, "severity": "fail", "basis": "house",
         "note": "Characters per second is a proxy and knows nothing about "
                 "vocabulary, language, or who is watching. Seventeen is "
                 "comfortable for most adult viewers in English and far too "
                 "fast for a children's programme. Change the number."},
        {"id": "line_length", "metric": "caption_max_line_length",
         "label": "Longest line", "unit": "chars", "max": 42.0,
         "warn_max": 37.0, "severity": "fail", "basis": "house",
         "note": "Forty-two characters is the width most players and most "
                 "broadcast guidance assume."},
        {"id": "line_count", "metric": "caption_max_lines",
         "label": "Lines per cue", "max": 2.0, "severity": "fail", "basis": "house",
         "note": "A third line covers picture and is read as an error by "
                 "most viewers before they finish it."},
        {"id": "min_duration", "metric": "caption_shortest_cue_s",
         "label": "Shortest cue", "unit": "s", "min": 1.0, "warn_min": 1.4,
         "severity": "fail", "basis": "house",
         "note": "Below a second a cue registers as a flicker rather than as "
                 "words, however few characters it holds."},
        {"id": "max_duration", "metric": "caption_longest_cue_s",
         "label": "Longest cue", "unit": "s", "max": 7.0, "severity": "warn", "basis": "house",
         "note": "Past about seven seconds a viewer has read it twice and "
                 "started to wonder whether the player has frozen."},
        {"id": "empty", "metric": "caption_empty_cues",
         "label": "Empty cues", "max": 0.0, "severity": "warn", "basis": "house"},
        {"id": "cue_count", "metric": "caption_cue_count",
         "label": "Cues", "min": 1.0, "severity": "fail", "basis": "house"},
    ],
}


# Cross-file rules every delivery gets. Like the per-file universal set these
# are faults rather than requirements, so they warn — but a delivery whose
# files disagree about something this basic is nearly always a mistake, and it
# is the kind of mistake nobody notices until an ingest queue does.
UNIVERSAL_SET = [
    {"id": "set_channels", "metric": "set_channels_distinct",
     "label": "Channel count across the set", "max": 1.0, "severity": "warn", "basis": "house",
     "note": "Some files are mono and some are stereo."},
    {"id": "set_sample_rate", "metric": "set_sample_rate_distinct",
     "label": "Sample rate across the set", "max": 1.0, "severity": "warn", "basis": "house"},
    {"id": "set_numbering", "metric": "set_missing_files",
     "label": "Gaps in the numbering", "max": 0.0, "severity": "warn",
     "basis": "house",
     "note": "A numbered sequence with a number absent from the middle of it. "
             "Nothing is wrong with any file here; the delivery is simply "
             "short one, which is the kind of thing found at submission "
             "rather than at export."},
    {"id": "set_loudness", "metric": "set_loudness_spread_db",
     "label": "Loudness spread across the set", "unit": "dB", "max": 6.0,
     "severity": "warn", "basis": "house",
     "note": "Six decibels is where a listener starts reaching for the "
             "volume control between one file and the next."},
]


BUILT_IN = [ACX, EBU_R128, SPOTIFY_PODCAST, YOUTUBE, SOCIAL_VERTICAL,
            GENERIC_WEB, SUBTITLES]

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
    # A target that states a cross-file rule of its own replaces the universal
    # one for that metric, rather than being checked twice at two thresholds.
    stated = {rule["metric"] for rule in profile.get("set_rules", [])}
    profile["set_rules"] = list(profile.get("set_rules", [])) + [
        copy.deepcopy(rule) for rule in UNIVERSAL_SET
        if rule["metric"] not in stated]
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
    if not isinstance(data.get("set_rules", []), list):
        raise ValueError("A profile's 'set_rules' must be a list.")
    for index, rule in enumerate(list(data["rules"])
                                 + list(data.get("set_rules") or [])):
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
        basis = rule.get("basis")
        if basis is not None and basis not in ("published", "observed",
                                               "house"):
            raise ValueError(
                f"Rule '{rule['id']}' has basis '{basis}'; use published, "
                "observed or house.")
    return True


def save(profile, path):
    """Write a profile out as JSON, universal rules stripped back off."""
    universal = {rule["id"] for rule in UNIVERSAL}
    universal_set = {rule["id"] for rule in UNIVERSAL_SET}
    out = copy.deepcopy(profile)
    out["rules"] = [r for r in out.get("rules", [])
                    if r["id"] not in universal or r.get("customised")]
    out["set_rules"] = [r for r in out.get("set_rules", [])
                        if r["id"] not in universal_set or r.get("customised")]
    if not out["set_rules"]:
        del out["set_rules"]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
        handle.write("\n")
    return path
