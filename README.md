# Media Preflight

Drop in a finished audio or video file, pick where it is going, and get a
plain-English pass/fail report with the exact timestamps of everything that is
wrong — then, if you want, a corrected copy that is measured again from scratch
before it claims to be fixed.

No accounts. No uploads. No credits. No generative AI. MIT licensed.

It is an inspector with a small set of carefully bounded, previewable
transformations. It is not an editor, and it will not invent audio that was
never recorded.

```
Chapter 04.wav
23:41  wav  pcm_s16le 44.1 kHz mono 706 kbps

Target: Audiobook — ACX

✕ Not ready — the failures below would be rejected.
  4 failed, 1 warned, 11 passed, 2 not checked.

✕ RMS level: -29.89 dBFS                   Required: -23 to -18 dBFS
    measured across the whole file — there is no single moment to point at
✕ Peak level: -0.76 dBFS                         Required: ≤ -3 dBFS
    at 03:42 (peak -0.76 dBFS), 18:07 (peak -0.91 dBFS)
✕ Closing room tone: 0 s                          Required: 1 to 5 s
✕ Bitrate mode: vbr                                    Required: cbr
⚠ DC offset: 0.02                                   Required: ≤ 0.01

Problems occur at 03:42, 18:07, 41:16
```

## What it checks

**Loudness and level** — integrated loudness (ITU-R BS.1770), loudness range,
short-term excursions, true peak, sample peak, RMS, noise floor.

**Damage** — clipped samples with the seconds they occur in, DC offset, dead
channels, channel imbalance, out-of-phase stereo that will cancel in mono.

**Shape** — room tone at the head and tail, suspicious mid-file gaps, endings
that stop while the programme is still at full level.

**Declarations** — codec, container, sample rate, channel count, bitrate, and
whether an MP3 is constant or variable bitrate.

Every failure that can be tied to a moment carries one. Failures that are
whole-file measurements say so rather than pointing at a second that means
nothing.

Video and caption checks — frame rate, resolution, variable-frame-rate
warnings, black and frozen frames, caption overlaps and reading speed — are the
next release. The audio path is complete.

## Targets

| id | what it is | thresholds |
| --- | --- | --- |
| `acx` | Audiobook, ACX retail delivery | published |
| `ebu_r128` | Broadcast, EBU R 128 | published |
| `spotify_podcast` | Podcast delivery to Spotify | published |
| `youtube` | YouTube upload | published |
| `social_vertical` | Instagram / TikTok | **informal** |
| `web` | Generic web video | **informal** |

Two of these are marked *informal* because the platforms do not publish a
specification. Those thresholds are the widely reported figures, offered as a
sanity check you should adjust — not as a promise about what the platform does
today. Every profile carries the source its numbers came from and the month
they were read, and the report prints both.

You can write your own: see [docs/profiles.md](docs/profiles.md). A target is a
JSON file, not a patch to this tool.

## Requirements

- Python 3.10 or newer — standard library only, no packages to install
- ffmpeg and ffprobe, built with the `ebur128` filter
  (`brew install ffmpeg`, `winget install Gyan.FFmpeg`, `apt install ffmpeg`)

The tool probes for the filter rather than trusting the first ffmpeg on PATH,
because slim distribution builds omit it and the failure otherwise appears
halfway through an analysis as an unexplained filtergraph error.

## Use it

**The window.** Double-click `Start Media Preflight.command` on macOS,
`start.bat` on Windows, or:

```sh
python3 app.py
```

This opens a page in your browser served from `127.0.0.1`, with a token minted
at startup so nothing else on the machine can drive it. Your file is never
uploaded; a browser cannot tell a local program where a dropped file lives, so
the page asks the tool to open your system's own file dialog and the path never
leaves the machine.

**The command line.**

```sh
python3 preflight.py check finished.mp3 --target acx
python3 preflight.py fix   finished.mp3 --target acx --dry-run
python3 preflight.py fix   finished.mp3 --target acx
python3 preflight.py targets
```

`check` exits 0 when the file passes, 1 when it fails, and 2 when the tool
itself could not run — so it drops into a build script without parsing
anything. `--strict` makes warnings count as failures. `--json` and
`--markdown` write the machine-readable report and the client-facing one.

## The corrected copy

`fix` never touches your file. It writes `yourfile.preflight.ext` beside it and
refuses to overwrite anything that already exists unless told twice.

Before it writes, it shows you the whole plan in sentences and the exact ffmpeg
command it will run:

```
  1. Trim 1.65 s off the front, leaving 0.75 s of room tone before the first word.
  2. Trim the ending to 18.148 s, leaving 3 s of room tone after the last word.
  3. Apply +10.81 dB of gain, moving RMS from -31.31 dBFS to about -20.50 dBFS;
     peak would land near -5.73 dBFS.
  4. Re-encode the audio as mp3, at 192 kbps, constant bitrate.
```

Three things make this more than a wrapper around `ffmpeg -af`:

**It will not create a fault while fixing one.** Raising a quiet recording by
eleven decibels raises its peaks and its DC offset by eleven decibels too. The
planner does that arithmetic in advance and adds a limiter or a high-pass to
the plan, where you can read it before anything runs — and says plainly that
the source was fine and the gain is what would have broken it.

**It measures the result rather than assuming it.** After the copy is written
it is analysed again from nothing, and the report you get is that measurement:

```
Verification — the corrected copy, measured from scratch:

  ✕ → ✓  RMS level: -31.31 dBFS → -19.39 dBFS
  ✕ → ✓  Closing room tone: 8.2 s → 3 s
  ✕ → ✓  Bitrate mode: vbr → cbr
```

**When it lands off target, it rebuilds from the source.** Trimming five
seconds of room tone off an ending changes the RMS the gain was calculated
from; a lossy encoder hands back peaks louder than it was given. Either way the
correction is recomputed and applied to the *original* file again, so however
many attempts it takes, the number of lossy encodes stays at one.

`--recipe out.json` writes what was done as JSON you can read, argue with, and
repeat by hand.

## What it will not do

- Edit, record, or arrange anything
- Publish anywhere
- Repair with generative models — no invented audio, ever
- Remove noise, de-ess, de-click, or otherwise change the recording's character
- Ask you to sign in, upload, or spend a credit

Noise reduction in particular is deliberately absent. A noise floor above the
target is reported with the timestamp of nothing, because the answer is to
treat the room or re-record, and a tool that quietly ran a denoiser over an
audiobook would be doing something its owner did not ask for.

## Development

```sh
python3 -m unittest discover -s tests     # 83 checks, about five seconds
python3 scripts/make_fixtures.py          # build the test media from ffmpeg
```

Test media is generated, not committed: every fixture is built from ffmpeg's
own sources, so it weighs nothing in the repository and its definition reads as
a description of the fault it carries.

The parsers are tested against ffmpeg output recorded verbatim, so a change in
ffmpeg's output shape breaks a test rather than quietly filling a report with
dashes. The end-to-end tests run real files through the whole path and skip
with an install line when ffmpeg is absent.

## Layout

| file | what it does |
| --- | --- |
| `preflight.py` | the command line, and the runner everything else calls |
| `app.py` | the local server behind the browser window |
| `index.html` | the window |
| `probe.py` | what the file *says* it is — ffprobe, normalised |
| `analysis.py` | what the audio *contains* — one decode, every measurement |
| `checks.py` | the rule engine: measurements plus a target, in, findings out |
| `profiles.py` | the delivery targets, as data |
| `corrections.py` | planning, previewing, applying and verifying a fix |
| `report.py` | the same findings as terminal text, Markdown, and JSON |
| `platform_support.py` | finding ffmpeg, native file dialogs, per-platform paths |

## Licence

MIT. See [LICENSE](LICENSE).
