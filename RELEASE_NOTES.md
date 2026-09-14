# Media Preflight 0.2.0

**You no longer have to know where the file is going.**

0.1.0 began by asking for a delivery target. That is the right question for
somebody with a specification open in another window, and the wrong one for
everybody else — whose honest answer is *I don't know, that's what I'm asking
you*. Drop a file in now and it measures once, then reports where that file
stands against every target that applies.

```
A03 Green Aura.wav
03:05  wav  pcm_s16le 48 kHz stereo

What it is
  Loudness               -14.9 LUFS
  True peak              -3.9 dBTP

Ready to deliver
  ✓ Music — streaming master

Not ready
  ✕ Audiobook — ACX        RMS level is -17.22 dBFS, wanted -23 to -18, and 4 others.
  ✕ Broadcast — EBU R128   Integrated loudness is -14.9 LUFS, wanted -24 to -22.
  ✕ Podcast — Spotify      Codec is pcm_s16le, wanted mp3 or aac.
```

Every line that is not ready carries the correction for that destination, so
"not ready for a podcast" and "fix it for a podcast" are the same click.

It is affordable because the measurement never depended on the target. Reading
the file and comparing numbers against a profile are separate jobs, and only a
target's own options change the reading — so targets are grouped by those and
measured once per group. Three passes, about four seconds, on a three-minute
track. Surveying a video costs a full picture pass and says so before it
starts.

## Two targets that had to exist

**`music_streaming` — a song had nowhere to go.** The audio targets were an
audiobook specification, a broadcast specification and a podcast
specification. Meanwhile the −14 LUFS figure everybody wanted was sitting
inside the *podcast* profile with a note explaining that it is really a music
number. It has a home now.

It breaks the pattern of every other target in one way that matters:
**loudness does not fail there.** Streaming platforms normalise — they measure
the file and turn it down — so failing a record for being loud would be
inventing a rule nobody enforces. Loudness warns at the edges and explains
why; true peak fails, because that is the one that distorts after a lossy
encode.

**`lyrics` — lyrics are not subtitles.** A subtitle's timing is a reading
budget its author controls. A lyric's belongs to the song: the line lands when
it is sung and leaves when the next arrives. A subtitle still on screen after
twenty seconds is a fault; a lyric held through an instrumental is the normal
case. Two subtitles at once is an error; karaoke showing the sung line above
the next one is the format working.

So it checks what can still be wrong — a line nobody could see, a line that
never leaves, text too wide for the frame — and leaves the timing to the song.
Its thresholds were set by measuring seventeen real lyric videos and placing
every number outside the range they occupy. That corpus is one tool and one
artist, which is the honest size of the claim.

## Karaoke files were being measured wrong

A lyric video reported **360 characters a second** and a shortest cue of 0.05
seconds. Neither number was about anything anybody read.

Karaoke repaints a line rather than re-writing it, so the file holds the whole
line once per highlight step — "Close your eyes" four times across 2.32
seconds, one of them for 0.16 s. Measured per cue that is four separate
subtitles, one of which flashes fifteen characters up for a sixth of a second.

Runs of the same text in the same place are merged into the span a reader
actually had. The same file now measures 14 characters a second. If you keep
karaoke or lyric caption files, this changes their numbers — for the better,
and the old ones were wrong.

## Guided mode is the whole of it

A path, one button, and a report with a correction offered on every
destination that wants one. No target control at all. Guided still means
automatic planning and not automatic consent: the fix button opens the preview
and writes nothing until you approve it.

The window now says what a survey will cost before you press it — *"Check it —
about 3 min 16 s"* — because surveying a feature reads every frame for every
target that asks about the picture, and the mode built for people who would
not expect a wait was the one place that never warned.

## Changed behaviour

**`--target` has no default.** Its absence is a different question, not a
missing argument.

- `preflight check <file>` with no target now **surveys** instead of silently
  checking against *Generic web video*. On a WAV that used to print
  "✓ Ready to deliver, 0 failed, 16 not checked" — a green tick standing on
  sixteen skipped checks.
- `preflight fix <file>` and `preflight batch <folder>` now **require**
  `--target`, and say which commands answer the question. Correcting towards
  nowhere in particular is not a smaller job, it is a different one.

If you script against this tool, those two commands need a `--target` they did
not need before.

## Also fixed

- The window defaulted to a video target, so dropping in a WAV and pressing
  Run produced an error about video targets.
- A survey headline could contradict its own numbers, quoting a failing range
  back at a value that satisfied it.
- `build.sh` and `release.sh` pinned their own verification to one target
  being listed first, so adding a target turned a good build red.
- The ecosystem cards sized their display type against the window rather than
  the card, and their accent colours failed contrast on a light background —
  between 1.3:1 and 2.2:1 where 4.5:1 is needed.

## Which download

| | |
| --- | --- |
| `media-preflight-0.2.0.pyz` | One file, any platform. `python3 media-preflight-0.2.0.pyz` |
| `Media-Preflight-0.2.0-macOS.zip` | The app bundle, for an icon in /Applications |
| `media-preflight-0.2.0-linux.tar.gz` | The archive, a launcher and a `.desktop` entry |
| `media-preflight-0.2.0-windows.zip` | The archive and a `.bat` launcher |

Verify against `SHA256SUMS`.

## What it needs, and does not ship

**Python 3.10 or newer**, and **ffmpeg built with the ebur128 filter**.

Neither is bundled, and that is a decision rather than an omission. Bundling
Python would take a build-time dependency on a packaging toolchain for a tool
whose whole claim is that it needs nothing installed. Bundling ffmpeg means
eighty megabytes and a redistribution licensing decision that is not ours to
make. When something is missing it says which, and prints the command that
installs it.

    macOS      brew install ffmpeg
    Windows    winget install Gyan.FFmpeg
    Debian     sudo apt install ffmpeg
    Fedora     sudo dnf install ffmpeg

## macOS: the app is not notarised

It is ad-hoc signed, which is enough to run and not enough to satisfy
Gatekeeper on something downloaded. The first launch will be refused. Either
right-click the app and choose **Open**, or:

    xattr -dr com.apple.quarantine "/Applications/Media Preflight.app"

Notarisation needs a paid Apple Developer account.

## Known limits

- **The flashing check is a screening heuristic**, not a photosensitivity
  test. It finds passages worth looking at with human eyes. It cannot clear a
  programme and does not claim to.
- **Interlacing detection reports "inconclusive"** rather than guessing where
  the evidence does not separate real field dominance from noise. That is the
  truthful answer and it is not a verdict.
- **Time estimates are arithmetic over constants measured on one laptop.**
  They are given as a range and have landed within 15% on real files, but a
  very different machine will differ.
- **A survey always exits 0.** "Ready for streaming, not for broadcast" is
  neither a pass nor a failure — it is the answer to the question. For a CI
  gate, name a target.
- **Windows and Linux are supported but lightly travelled.** The code paths
  are there and 442 checks pass; what has not happened is somebody installing
  from a download on a fresh machine of each. If that is you, an issue
  describing what broke is the most useful thing you could send.

## If a number looks wrong

A threshold that has moved, a target that has changed its rules, a measurement
that disagrees with your own meter — all of those are worth an issue. The
profiles carry the month they were read precisely so that they can be argued
with.
