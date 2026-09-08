#!/usr/bin/env python3
"""Build the test media, rather than committing it.

Fixtures are generated from ffmpeg's own sources, so they are byte-identical on
every machine that has the same ffmpeg, weigh nothing in the repository, and
can be read as a description of the fault each one carries.

    python3 scripts/make_fixtures.py [folder]

Each fixture is named for what is wrong with it.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import platform_support  # noqa: E402


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(HERE, "fixtures")

# A stand-in for speech: pink noise gated into syllable-length bursts. It is
# not speech, but it has speech's shape — bursts with gaps — which is what the
# silence and loudness measurements react to.
SPEECH = ("(0.6*sin(2*PI*180*t)+0.25*sin(2*PI*430*t)"
          "+0.12*(1-2*random(1)))"
          "*(0.55+0.45*sin(2*PI*3.1*t))*gt(sin(2*PI*1.7*t)\\,-0.35)")


def _room_tone(level=0.00018):
    return f"{level}*(1-2*random(2))"


FIXTURES = {
    # Quiet, hot-peaked, no room tone at either end, and the wrong format for
    # ACX in every respect. The one everything else is measured against.
    "acx-quiet-and-hot.wav": {
        "expr": f"0.055*({SPEECH}) + 0.9*sin(2*PI*180*t)*between(t\\,9\\,9.05)",
        "duration": 20,
        "rate": 48000,
        "args": ["-ac", "1"],
        "note": "RMS about 15 dB below the ACX window, one transient "
                "nearly at full scale, and no room tone at either end. "
                "Correcting the level here would clip without a limiter.",
    },
    # Correct room tone at both ends, so the padding logic has something real
    # to copy and the trim logic something to shorten.
    "acx-room-tone.wav": {
        "expr": (f"{_room_tone()} + 0.154*({SPEECH})*between(t\\,2.4\\,17)"),
        "duration": 25,
        "rate": 44100,
        "args": ["-ac", "1"],
        "note": "Quiet but otherwise sound: too much room tone at both "
                "ends and an RMS a little under the window. Everything "
                "wrong with it is correctable.",
    },
    "clipped.wav": {
        "expr": "1.6*sin(2*PI*440*t)*between(t\\,2\\,3) + 0.3*sin(2*PI*180*t)",
        "duration": 6,
        "rate": 48000,
        "args": ["-ac", "1"],
        "note": "One second of hard clipping in the middle of a clean tone.",
    },
    "silent-right.wav": {
        "expr": None,
        "duration": 5,
        "rate": 48000,
        "args": None,
        "complex": True,
        "note": "Stereo with nothing at all in the right channel.",
    },
    "dc-offset.wav": {
        "expr": f"0.25*({SPEECH}) + 0.06",
        "duration": 8,
        "rate": 48000,
        "args": ["-ac", "1"],
        "note": "A constant 0.06 bias on the waveform.",
    },
    "abrupt-end.wav": {
        "expr": f"0.3*({SPEECH})",
        "duration": 6,
        "rate": 48000,
        "args": ["-ac", "1"],
        "note": "Programme level right up to the final sample.",
    },
}


def build(folder=DEFAULT_DIR):
    ffmpeg, _ = platform_support.require_tools()
    os.makedirs(folder, exist_ok=True)
    written = []

    for name, spec in FIXTURES.items():
        path = os.path.join(folder, name)
        if spec.get("complex"):
            command = [ffmpeg, "-y", "-v", "error",
                       "-f", "lavfi", "-i",
                       f"aevalsrc=0.3*({SPEECH})|0:d={spec['duration']}"
                       f":s={spec['rate']}",
                       path]
        else:
            command = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                       f"aevalsrc={spec['expr']}:d={spec['duration']}"
                       f":s={spec['rate']}"] + (spec["args"] or []) + [path]
        subprocess.run(command, check=True, **platform_support.no_console())
        written.append((name, spec["note"]))

    # A low-bitrate variable-rate MP3, made from one of the WAVs, so the
    # container and bitrate-mode checks have something real to read.
    source = os.path.join(folder, "acx-quiet-and-hot.wav")
    vbr = os.path.join(folder, "acx-quiet-and-hot-96k-vbr.mp3")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", source,
                    "-c:a", "libmp3lame", "-q:a", "7", "-ar", "48000", vbr],
                   check=True, **platform_support.no_console())
    written.append((os.path.basename(vbr),
                    "The same fault, delivered as a variable-bitrate MP3 at "
                    "roughly 96 kbps and the wrong sample rate."))

    # A short video whose audio is fine and whose picture simply exists, so the
    # audio path can be exercised on a container that also carries video.
    video = os.path.join(folder, "video-with-audio.mp4")
    subprocess.run([ffmpeg, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:d=5",
                    "-f", "lavfi", "-i",
                    f"aevalsrc=0.2*({SPEECH}):d=5:s=48000",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", video],
                   check=True, **platform_support.no_console())
    written.append((os.path.basename(video),
                    "Five seconds of picture with acceptable audio."))

    written += build_video(folder, ffmpeg)
    written += build_captions(folder)
    written += build_chaptered(folder, ffmpeg)
    written += build_title(folder, ffmpeg)
    return folder, written


# ------------------------------------------------------------------ chapters

CHAPTER_METADATA = """;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=20000
title=Opening, quiet
[CHAPTER]
TIMEBASE=1/1000
START=20000
END=40000
title=The loud one
[CHAPTER]
TIMEBASE=1/1000
START=40000
END=60000
title=Back to normal
"""


def build_chaptered(folder, ffmpeg):
    """One chapter markedly louder than its neighbours, which is the fault the
    per-chapter table exists to find."""
    path = os.path.join(folder, "chaptered-uneven.m4a")
    metadata = os.path.join(folder, ".chapters.txt")
    with open(metadata, "w", encoding="utf-8") as handle:
        handle.write(CHAPTER_METADATA)
    expression = (f"0.05*({SPEECH})*lt(t\\,20) + 0.4*({SPEECH})"
                  f"*between(t\\,20\\,40) + 0.05*({SPEECH})*gt(t\\,40)")
    subprocess.run([ffmpeg, "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"aevalsrc={expression}:d=60:s=44100",
                    "-i", metadata, "-map_metadata", "1",
                    "-ac", "1", "-c:a", "aac", "-b:a", "128k", path],
                   check=True, **platform_support.no_console())
    os.remove(metadata)
    return [(os.path.basename(path),
             "Three chapters, the middle one about eighteen decibels louder "
             "than the two around it.")]


# --------------------------------------------------------------------- video

def _encode(ffmpeg, path, *inputs, filters=None):
    command = [ffmpeg, "-y", "-v", "error"]
    for source in inputs:
        command += ["-f", "lavfi", "-i", source]
    if filters:
        command += ["-filter_complex", filters]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(command, check=True, **platform_support.no_console())
    return path


# Ten alternations a second, well past the three-per-second threshold the
# flashing screen is written around. The comma inside geq's expression is
# escaped because a filtergraph splits its options on commas first.
STROBE = ("color=c=white:size=320x180:rate=30:d=4,"
          "geq=lum='if(lt(mod(floor(T*10)\\,2)\\,1)\\,235\\,16)'"
          ":cb=128:cr=128")


def build_video(folder, ffmpeg):
    written = []
    size = "size=320x180"

    _encode(ffmpeg, os.path.join(folder, "video-black-tail.mp4"),
            f"testsrc2={size}:rate=25:d=3", f"color=black:{size}:rate=25:d=4",
            filters="[0:v][1:v]concat=n=2:v=1:a=0")
    written.append(("video-black-tail.mp4",
                    "Three seconds of picture and four of black, which is what "
                    "a render that ran past the edit looks like."))

    _encode(ffmpeg, os.path.join(folder, "video-frozen.mp4"),
            f"testsrc2={size}:rate=25:d=3", f"color=c=gray:{size}:rate=25:d=6",
            filters="[0:v][1:v]concat=n=2:v=1:a=0")
    written.append(("video-frozen.mp4",
                    "The picture stops moving six seconds before the file does."))

    _encode(ffmpeg, os.path.join(folder, "video-strobe.mp4"), STROBE)
    written.append(("video-strobe.mp4",
                    "A ten-hertz full-frame flash: what the flashing screen "
                    "is meant to find."))

    # Variable frame rate has to be built rather than asked for: two segments
    # at different rates, concatenated without re-timing, in a container that
    # records per-frame durations honestly.
    first = _encode(ffmpeg, os.path.join(folder, ".vfr-25.mp4"),
                    f"testsrc2={size}:rate=25:d=2")
    second = _encode(ffmpeg, os.path.join(folder, ".vfr-50.mp4"),
                     f"testsrc2={size}:rate=50:d=2")
    listing = os.path.join(folder, ".vfr-list.txt")
    with open(listing, "w", encoding="utf-8") as handle:
        for part in (first, second):
            handle.write("file '%s'\n" % os.path.basename(part))
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", listing, "-c", "copy", "-fps_mode", "passthrough",
                    os.path.join(folder, "video-variable-rate.mkv")],
                   check=True, **platform_support.no_console())
    for temporary in (first, second, listing):
        os.remove(temporary)
    written.append(("video-variable-rate.mkv",
                    "Twenty-five frames a second for two seconds, then fifty. "
                    "Plays fine; ruins anything downstream that assumed one rate."))

    # Genuinely interlaced, and honest about it in the header.
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2={size}:rate=50:d=3",
         "-vf", "tinterlace=mode=interleave_top,setfield=tff",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-flags", "+ilme+ildct",
         os.path.join(folder, "video-interlaced.mp4")],
        check=True, **platform_support.no_console())
    written.append(("video-interlaced.mp4",
                    "Top-field-first interlaced, and flagged as such."))

    # Telecined: 24-frame material pulled up to 30 with repeated fields, and a
    # header that calls the result progressive. The header-trusting check this
    # replaced passed this file.
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2={size}:rate=24:d=4", "-vf", "telecine=pattern=23",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         os.path.join(folder, "video-telecined.mp4")],
        check=True, **platform_support.no_console())
    written.append(("video-telecined.mp4",
                    "Repeated fields from a 3:2 pulldown, in a file whose "
                    "header says progressive — which is what makes reading "
                    "the header alone insufficient."))

    # Smooth progressive motion: the control. idet reads this correctly, and
    # video-strobe.mp4 above is the case where it does not.
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
         f"gradients={size}:rate=25:d=3:speed=0.05",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         os.path.join(folder, "video-progressive.mp4")],
        check=True, **platform_support.no_console())
    written.append(("video-progressive.mp4",
                    "Smooth progressive motion, for the interlace check to "
                    "not find anything in."))
    return written


# ------------------------------------------------------------------- a title

# One delivery, with the two faults that only exist across a set: a chapter in
# stereo where the rest are mono, and a chapter recorded several decibels
# hotter than its neighbours. Every one of these files passes a per-file check
# for channel count on its own.
# Amplitudes chosen so that every chapter passes ACX *on its own* — RMS inside
# the window, peak under the ceiling, room tone at both ends. The only faults
# left are the two that exist across the set and nowhere in it.
TITLE_CHAPTERS = [
    ("chapter-01.mp3", 0.392, 1),
    ("chapter-02.mp3", 0.392, 1),
    ("chapter-03.mp3", 0.621, 1),     # four decibels above its neighbours
    ("chapter-09.mp3", 0.392, 2),     # the stereo one
    ("chapter-10.mp3", 0.392, 1),     # sorts before 09 unless sorted naturally
]


def build_title(folder, ffmpeg):
    title = os.path.join(folder, "title")
    os.makedirs(title, exist_ok=True)
    for name, amplitude, channels in TITLE_CHAPTERS:
        # Room tone under everything, speech between 0.75 s and 11 s: three
        # quarters of a second of tone at the head and three at the tail, which
        # is what ACX asks for.
        expression = (f"{_room_tone()} + {amplitude}*({SPEECH})"
                      f"*between(t\\,0.75\\,11)")
        # Two channels are generated as two expressions rather than upmixed
        # from one: ffmpeg's mono-to-stereo matrix attenuates, which would make
        # the stereo chapter three decibels quieter and confuse the very
        # comparison this fixture exists to demonstrate.
        source = expression if channels == 1 else f"{expression}|{expression}"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
             f"aevalsrc={source}:d=14:s=44100",
             "-c:a", "libmp3lame", "-b:a", "192k",
             "-abr", "0", "-ar", "44100", os.path.join(title, name)],
            check=True, **platform_support.no_console())
    return [("title/",
             f"{len(TITLE_CHAPTERS)} chapters that each pass ACX on their own, "
             f"carrying the two faults only a set can have: one is stereo "
             f"where the rest are mono, and one is four decibels louder than "
             f"its neighbours.")]


# ------------------------------------------------------------------ captions

# Timed the way a caption actually has to be: about fifty characters wants
# three and a half seconds, or nobody finishes reading it.
CLEAN_SRT = """1
00:00:00,500 --> 00:00:04,000
A line somebody can read
in the time they are given.

2
00:00:04,500 --> 00:00:08,000
And a second one, comfortably
under the width limit.
"""

# The sidecar has to fit inside the five-second video it sits beside, or the
# "captions past the end" check is right to complain about it.
SIDECAR_SRT = """1
00:00:00,500 --> 00:00:04,000
A line somebody can read
in the time it is given.
"""

BROKEN_SRT = """1
00:00:00,200 --> 00:00:00,700
This single line is far too long to be read in seven tenths of a second by anybody at all

2
00:00:00,500 --> 00:00:02,000
Starting before the one above it has finished

3
00:00:02,100 --> 00:00:02,050
Ending before it starts

4
00:00:03,000 --> 00:00:12,000
Running well past the end of the picture it belongs to
"""

MISSING_FONT_ASS = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour
Style: Default,A Font Nobody Has Installed,48,&H00FFFFFF

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:02.50,Default,,0,0,0,,Styled in a font that is not here
Dialogue: 0,0:00:03.00,0:00:04.50,Default,,0,0,0,,{\\fnAnother Absent Face}And another
"""


def build_captions(folder):
    written = []
    for name, body, note in (
        ("captions-clean.srt", CLEAN_SRT,
         "Two well-formed cues, comfortably inside every convention."),
        ("captions-broken.srt", BROKEN_SRT,
         "One cue of each fault: unreadable speed, an overlap, a negative "
         "duration, and a cue running past the end."),
        ("captions-missing-font.ass", MISSING_FONT_ASS,
         "Names two fonts no machine is likely to have, one in a style and "
         "one inline."),
    ):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        written.append((name, note))

    # A sidecar found by name rather than by being pointed at: the same stem as
    # video-with-audio.mp4, which is how a delivery actually arrives.
    path = os.path.join(folder, "video-with-audio.srt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(SIDECAR_SRT)
    written.append((os.path.basename(path),
                    "Clean captions beside the video, discovered by name."))
    return written


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    folder, written = build(folder)
    print(f"Fixtures in {folder}:\n")
    for name, note in written:
        print(f"  {name}\n      {note}")


if __name__ == "__main__":
    main()
