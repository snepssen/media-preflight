# Media Preflight — working context

A local-first inspector for finished media — audio, picture and captions.
Drop a file, pick a delivery target, get a pass/fail report with exact
timestamps; optionally get a corrected copy that is measured again from scratch
before it claims to be fixed. Python 3 standard library only, ffmpeg and
ffprobe as external programs, a browser page on `127.0.0.1` as the window. MIT.

There is a project page in `docs/`, published by GitHub Pages, sharing
`ecosystem.css` and `ecosystem.js` with the other snepssen sites. That folder
is the site and nothing else; prose documentation lives at the top level
(`PROFILES.md`), as it does in the sibling repositories.

Read `README.md` for what it does and `PROFILES.md` for how targets are
written. This file is the short version: what will bite you if you don't know
it.

---

## The scope fence

**Do not build these. They are not oversights.**

- Editing, recording, or timeline tools
- Cloud publishing, uploads, accounts, credits, usage metering
- Generative repair — no model ever touches the media
- Noise reduction, de-essing, de-clicking, or anything that changes the
  character of a recording
- Retiming, rewriting or reflowing captions. They are measured and reported;
  correcting them is editing, and editing is somebody else's tool
- Any claim to clear a programme for photosensitivity. The flashing check is a
  screening heuristic and every place it surfaces says so

The last one comes up every time somebody reads the noise-floor check and asks
why it has no fix. The answer is in the profile's own note: the remedy is to
treat the room or re-record, and a tool that quietly ran a denoiser over
somebody's audiobook would be doing something its owner did not ask for.

## Four promises the code keeps

**The source is never modified.** `corrections.apply` refuses to write over the
source, and refuses to write over anything else unless told twice. There is a
test for the bytes of the source file being identical afterwards; keep it.

**Nothing is applied unpreviewed.** `plan` returns sentences, `build_command`
returns argv, and both are shown before anything runs. A correction that cannot
be described in a sentence does not belong in the planner.

**A correction never creates a fault.** `_guard` does the arithmetic in
advance: a gain that would push peaks past the ceiling brings a limiter with
it, a gain that would push DC over the line brings a high-pass, and both say
in writing that the source was fine and the gain is what would have broken it.

**Success is measured, not assumed.** After a copy is written it is analysed
again from nothing and the report is that measurement. When it lands off target
the correction is recomputed and applied *to the original file again* — never
to the corrected copy — so however many attempts it takes, the number of lossy
encodes stays at one. Bounded to two rebuilds.

## The one-decode-per-stream rule

`analysis.analyse` gets everything the audio report needs from a single pass:

```
ebur128 -> [aphasemeter] -> ametadata(print) -> silencedetect -> astats
```

ebur128 and aphasemeter inject per-frame metadata, ametadata prints it to
stdout, silencedetect and astats write to stderr. **Both streams are read at
once**, in a thread — a filled pipe nobody is draining is a deadlock, and it
looks exactly like a slow file.

Two things are allowed a second pass, and only on condition:

- **Peak and clipping localisation** (`locate_peaks`), when the whole-file peak
  says a peak problem can exist. Below the clipping threshold there are no
  clipped samples; that is arithmetic, not an estimate.
- **loudnorm's own measurement pass**, because loudnorm will not accept another
  filter's figures.

Do not add a third without a reason of that kind.

`video.analyse` does the same for the picture:

```
blackdetect -> freezedetect -> signalstats -> metadata(print)
```

It is a **separate** decode, deliberately. The two read different streams, and
combining them would put two metadata printers on one pipe with nothing keeping
their blocks apart. It runs only when the target has a rule that needs it —
`preflight.VIDEO_METRICS` is the list — because decoding a feature to count
black frames is minutes of somebody's time and a podcast profile has no reason
to spend them. `parse_luma_stream` keeps only `YAVG`: signalstats prints
fifteen fields a frame, and a ninety-minute film is 130,000 frames.

Captions cost no decode at all unless they are embedded, in which case one
`ffmpeg -f ass -` extraction reads them.

## Things measured the hard way, for a reason

- **The timeline is bucketed to one second.** ebur128 reports every 100 ms; the
  report quotes seconds. A ten-hour audiobook costs 36,000 rows instead of
  360,000, and nothing is lost that survives to the page.
- **Timestamps only when the quantity matches.** `checks.LOCATABLE` is the
  whole rule: short-term loudness can locate an integrated failure, and cannot
  locate an RMS failure. Adding an entry there is claiming two measurements are
  the same measurement. Be sure.
- **`bitrate_mode` samples three windows** rather than reading every packet of
  a ten-hour file. A VBR encoder that produced identical packet sizes in three
  separated windows would have had to be fed three identical stretches of audio.
- **ffmpeg is probed for `ebur128`, not trusted.** Slim builds omit it and the
  failure otherwise appears halfway through an analysis as a filtergraph error
  that says nothing about why.
- **Frame rate mode is sampled, not read.** No header records whether a file is
  constant or variable, so `video.classify_frame_durations` compares frame
  durations against the median across three windows and allows 5% to disagree.
  Demanding exactness would report most of the world's video as variable: a
  correct constant-rate file can end on a short frame, and NTSC rates drift by
  microseconds.
- **Flashing is screened, not tested.** Three large luminance transitions in a
  sliding second, which is where WCAG's general flash threshold sits. No
  spatial analysis, no red-flash rule, no proportion-of-screen test. It finds
  passages to look at and cannot clear anything — and the rule's `note` says
  exactly that in every report it appears in.

## Checking a set

`batch.py` runs the per-file check over many files and then measures the
delivery itself. The point is the requirements a per-file rule cannot express:
ACX asks that every file in a title share a channel count and a sample rate, so
a title where one chapter is stereo passes every individual check and is
rejected on submission.

- **`set_rules` sit beside `rules` in a profile**, same shape, checked by
  `checks.evaluate_set` against `batch.measure_set` output. Set findings name
  offending **files** where per-file findings name timestamps.
- **`*_distinct` findings name the minority.** `batch.odd_ones_out` returns the
  files outside the largest group — and, when two groups tie, everybody, rather
  than picking a side arbitrarily.
- **Spread findings name outliers, not extremes.** `batch.outliers` returns
  files further from the median than half the spread; when nobody qualifies
  (a smooth ramp) it falls back to both ends. Naming the quietest of four
  agreeing chapters as a fault would be wrong: it is the reference.
- **Loudness is compared in the unit the target states** — the same rule as
  `chart.band_for` and `checks.LOCATABLE`.
- **`OUTPUT_MARKER` keeps the tool's own output out of its input.** A folder
  checked twice would otherwise start checking its corrected copies.
- **Natural sort.** `Chapter 10` after `Chapter 2`, or a delivery report is
  something somebody has to re-sort in their head.
- **One unreadable file is reported, not fatal.** Only a delivery where
  *nothing* could be read raises.

## Drawing what was measured

`chart.py` reduces the one-second timeline and renders it three ways: SVG for
the window and the client report, one line of block characters for the
terminal, and the reduced points themselves in the JSON.

- **Buckets keep the extremes, never the average.** Averaging hides a spike
  next to a hole, which is the pair this tool exists to find. The SVG draws the
  area between the bucket's lowest and highest value so a reduced chart is
  honest about its own reduction.
- **A band is only drawn in the unit the chart is in.** `chart.band_for`
  returns a band for a LUFS rule and, for an RMS target like ACX, a *reason*
  instead — which the chart prints. Shading a loudness chart with an RMS band
  would be the same invention refused in `checks.LOCATABLE`.
- **Exports are light, the window is auto.** An SVG behind an `<img>` resolves
  `prefers-color-scheme` against the reader's operating system, not the
  document around it, so a themed chart lands dark inside a light report.
  `report.chart_svg(theme=...)` is the switch; only the window, which inlines
  the drawing, asks for `auto`.
- **Markdown references the chart, never inlines it.** Inline `<svg>` is
  stripped by most Markdown renderers, GitHub included.
- **Chapters skip their first three seconds.** Short-term loudness looks three
  seconds back, so the opening of a quiet chapter still carries the loud one
  before it — and making a quiet chapter look loud would break exactly the
  comparison the table exists for. A chapter too short for a clean window keeps
  everything rather than reporting nothing.

## Building something to double-click

`./build.sh` makes `Media Preflight.app`, `media-preflight.pyz` and a
`.desktop` file, and verifies each — the .pyz is executed and asked to list
targets before the build calls itself finished.

**It bundles neither Python nor ffmpeg, deliberately.** Bundling Python means a
build-time dependency (PyInstaller, py2app) on a tool whose claim is that it
needs nothing installed; bundling ffmpeg means eighty megabytes and somebody
else's licensing decision. The bundle removes the terminal, not the
prerequisites: `packaging/launcher.sh` finds a Python of at least 3.10 and asks
`tools/check_ffmpeg.py` the same question the application will, and turns
either absence into an osascript dialog carrying the install command. If you
are ever tempted to make the launcher import the application to check
something, do not — it must run before there is a Python that can.

**`build.sh`'s `MODULES` list is checked by a test** against the modules that
exist, because a bundle missing a file fails on somebody else's machine. Add a
top-level module and that test fails until the build copies it.

**The icon is drawn, not stored.** `tools/icon.py` is a PNG writer and a
rasteriser in the standard library, oversampling four times below 256 pixels
and twice above — the full factor at 1024 costs fifteen seconds and buys
nothing visible. Rendering is deterministic; a test asserts it, because a build
that differs run to run is a build nobody can verify.

**The window can stop itself.** A double-clicked app has no terminal to
interrupt, so `/api/quit` shuts the server down and the page offers it.

## Traps

- **JSON has no infinity.** Digital silence measures as `-inf` and `json.dumps`
  writes `-Infinity`, which the browser refuses. Everything user-facing goes
  through `report.jsonable`. The `default=` argument does *not* help: floats are
  serialisable, so it is never called for them.
- **loudnorm leaves its output at 192 kHz.** A 48 kHz file comes back at 96 kHz
  and fails a sample-rate rule it passed before. Every loudnorm step carries an
  `aresample` back to where it started.
- **Lossy encoders overshoot.** A ceiling met exactly before the encode is
  missed after it, and `aac` lands a few per cent under a requested bitrate.
  `_lossy_headroom` and `_bitrate_for` exist for those two facts.
- **`alimiter` limits sample peak, not true peak.** For a true-peak ceiling the
  work is done by loudnorm's `TP`, with the limiter as a backstop.
- **Two orders, not one.** `FIX_ORDER` is the order fixes are considered;
  `STEP_ORDER` is the order the resulting operations run in. One fix — "room
  tone at the tail" — becomes a trim or a pad depending on which side of the
  requirement the file sits, so the ids differ. Sorting steps by the fix order
  silently mis-sequences the filter chain.
- **A trim moves everything after it.** The plan threads a `context` carrying
  `head_trim_s` so the tail trim measures against the stream the head trim
  produced, not the file on disk.
- **Cover art is a video stream.** `probe.normalise` splits `attached_pic`
  streams out of `video_streams`, or every podcast episode becomes a video that
  fails every video rule.
- **Absence is not a pass.** A metric that returns `0` when there is nothing to
  measure turns a subtitle file into one that passed the silent-channel check.
  Return `None` and let it skip. `silent_channel_count` had exactly this bug.
- **ASS dialogue is not in time order.** Sorting on parse is what keeps every
  cue in a re-ordered file from reporting as an overlap.
- **A cue can start after the last frame.** `caption_past_end_s` compares
  `max(start, end)`, because a cue whose own timing is backwards still sits
  past the end of the picture.
- **Odd frame dimensions break x264.** 4:2:0 chroma cannot represent an odd
  number of lines; a 240x135 test fixture fails to encode. Keep fixtures even.
- **`facts["chapters"]` is a list, not a count.** It used to be `len(...)`;
  anything constructing facts by hand needs `[]`, not `0`.
- **`info` is a valid severity.** A profile may state it, so every renderer
  needs it in its `MARK` and sort-order tables; `report` used to raise a
  `KeyError` on one.
- **ffmpeg's mono-to-stereo upmix attenuates.** A fixture built with `-ac 2`
  from one expression comes out three decibels quieter than its mono siblings,
  which silently ruins a loudness comparison. Generate `expr|expr` instead.
- **The app cache is keyed on size and mtime**, so it must refuse directories:
  `os.stat` succeeds on a folder and would cache a delivery under a key that
  never invalidates.

## The numbers in profiles.py

Every built-in target carries `source`, `checked` and `confidence`. Two are
marked `informal` because the platforms publish no specification; those
thresholds are widely reported figures, and both the report and the README say
so. **When you change a threshold, change `checked` too.** This tool measures
exactly and compares against a number you can see and edit; it is not an oracle
for somebody else's current ingest rules, and the moment it presents itself as
one it is lying.

## Build and check

```sh
python3 -m unittest discover -s tests    # 212 checks, about fourteen seconds
./build.sh                              # .app, .pyz and .desktop, verified
python3 preflight.py batch fixtures/title -t acx   # the set-level faults
python3 scripts/make_fixtures.py         # regenerate the test media
python3 app.py                           # the window
python3 preflight.py check f.wav -t acx  # the command line
```

Fixtures are generated from ffmpeg's own sources rather than committed: each
one's definition in `scripts/make_fixtures.py` reads as a description of the
fault it carries, and `fixtures/` is in `.gitignore`.

Parser tests run against ffmpeg output recorded verbatim, so a change in
ffmpeg's output shape breaks a test rather than filling a report with dashes.
End-to-end tests use real files and skip with an install line when ffmpeg is
absent — they do not pass silently on a machine that cannot run them.

## Adding a check

A new check is a `metric` entry in `checks.METRICS` and a rule in a profile.
Three questions decide the rest:

1. **Where does the number come from?** A header field goes in
   `checks.DECLARED_METRICS` so the report does not apologise for having no
   timestamp for it. A measurement goes in the pass that already reads that
   stream.
2. **Can a failure be pointed at?** Only if something holds *the same quantity*
   over time. Add it to `checks.LOCATABLE` and give `checks.locate` a branch;
   otherwise it is a whole-file finding and says so.
3. **Is it a fault or a convention?** A fault belongs in `UNIVERSAL`. A
   convention belongs in a profile that carries its source, and its `note`
   should say plainly whose convention it is.

## Not yet built

Nothing in the original three-week plan. Candidates, in rough order of how
often they would earn their place:

- **Verifying the thresholds** — every published profile against its actual
  source document. The numbers were set from knowledge and stamped
  `checked: 2026-09`; nobody has read the specifications against them.
- **Interlacing** — verified from the picture rather than trusted from the
  header.
