# Media Preflight 0.1.0

Will this audio, video or caption package be accepted — and can you safely fix
what is wrong with it?

Drop in a finished file, pick where it is going, and get a plain-English
pass/fail report with the exact timestamps of everything that fails. Then, if
you want, a corrected copy that is measured again from scratch before it
claims to be fixed.

**No accounts. No uploads. No credits. No generative AI. MIT licensed.**

## What is in it

**Seven delivery targets, and your own.** ACX, EBU R 128, Spotify podcast,
YouTube, Instagram/TikTok, generic web, and caption readability. Every
threshold carries where it came from — `published`, `observed` or `house` —
and the month it was last checked, so it can be argued with. Four of them are
marked informal because there is no specification to point at, and the report
says so rather than implying an authority nobody has.

**A profile editor.** Write your own SOP through a wizard and it comes out as
portable JSON in your config directory — a file you can read, diff, mail to a
client, or check into a repository, and the same file the command line takes
with `--target`. Change a threshold you copied from a published spec and the
rule is re-labelled as yours, because a house number wearing a published badge
is a lie in somebody's paperwork.

**Corrections with four promises kept in code.** The source is never modified.
Nothing is applied without a preview. A correction may not create a new fault.
And success is measured, not assumed: the corrected copy is rebuilt from the
original and re-measured, up to two rounds, before it is called fixed.

**Deliveries, not just files.** A folder of numbered chapters is checked
against itself as well as against the target — channel counts, sample rates,
loudness spread, gaps in the numbering. Suggested, never assumed: ten files in
a folder might be one audiobook or ten unrelated episodes, and only you know
which.

**Guided and Professional.** The same measurements either way. Guided asks for
a target once per kind of file; Professional asks once per file and opens the
instrument panel. Switching between them never re-measures anything.

**It says what it will cost before it starts.** Reading the picture means
reading every frame, so the pass carries only the filters your target actually
needs, and both the window and the command line tell you the estimate first. A
broadcast loudness check on a video file skips the picture entirely; a
ninety-minute feature checked against everything is about eighty minutes of
work, and you are told that rather than discovering it.

## Which download

| | |
| --- | --- |
| `media-preflight-0.1.0.pyz` | One file, any platform. `python3 media-preflight-0.1.0.pyz` |
| `Media-Preflight-0.1.0-macOS.zip` | The app bundle, for an icon in /Applications |
| `media-preflight-0.1.0-linux.tar.gz` | The archive, a launcher and a `.desktop` entry |
| `media-preflight-0.1.0-windows.zip` | The archive and a `.bat` launcher |

Verify against `SHA256SUMS`.

## What it needs, and does not ship

**Python 3.10 or newer**, and **ffmpeg built with the ebur128 filter**.

Neither is bundled, and that is a decision rather than an omission. Bundling
Python would take a build-time dependency on a packaging toolchain for a tool
whose whole claim is that it needs nothing installed. Bundling ffmpeg means
eighty megabytes and a redistribution licensing decision that is not ours to
make. So the app removes the terminal, not the prerequisites: when something
is missing it says which, and prints the command that installs it.

    macOS      brew install ffmpeg
    Windows    winget install Gyan.FFmpeg
    Debian     sudo apt install ffmpeg
    Fedora     sudo dnf install ffmpeg

## macOS: the app is not notarised

It is ad-hoc signed, which is enough to run and not enough to satisfy
Gatekeeper on something downloaded from the internet. The first launch will be
refused. Either right-click the app and choose **Open**, or:

    xattr -dr com.apple.quarantine "/Applications/Media Preflight.app"

Notarisation needs a paid Apple Developer account. If that changes, this note
goes away.

## Known limits

- **The flashing check is a screening heuristic**, not a photosensitivity
  test. It counts large frame-to-frame luminance changes and finds passages
  worth looking at with human eyes. It cannot clear a programme and does not
  claim to.
- **Interlacing detection reports "inconclusive"** rather than guessing, on
  material where the evidence does not separate real field dominance from
  noise. That is the truthful answer and it is not a verdict.
- **Time estimates are arithmetic over constants measured on one laptop.**
  They are given as a range and were within 15% on the real files they were
  tested against, but a very different machine will differ.
- **Windows and Linux are supported but lightly travelled.** The code paths
  are there and the tests pass; what has not happened yet is somebody
  installing this from a download on a fresh machine of each. If that is you,
  an issue describing what broke is the most useful thing you could send.

## If a number looks wrong

A threshold that has moved, a target that has changed its rules, a measurement
that disagrees with your own meter — all of those are worth an issue. The
profiles carry the month they were read precisely so that they can be argued
with.
