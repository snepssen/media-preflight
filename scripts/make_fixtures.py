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

    return folder, written


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    folder, written = build(folder)
    print(f"Fixtures in {folder}:\n")
    for name, note in written:
        print(f"  {name}\n      {note}")


if __name__ == "__main__":
    main()
