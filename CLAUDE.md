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

**A correction costs no more than it has to.** Every fix but one re-encodes,
and says so in a caveat. Fast start is a `mux` step: where the plan holds
nothing else, `build_command` emits `-map 0 -c copy` and only the container is
rearranged, so the output holds exactly the media the input did — a test
proves it by comparing a hash of the decoded video. The trap here is
`_passthrough_encode`, which used to be added whenever there were *any* steps;
it is now added only when something actually filters the audio, because
re-encoding to fix a container throws away quality for nothing.

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
blackdetect -> freezedetect -> idet -> signalstats -> metadata(print)
```

It is a **separate** decode, deliberately. The two read different streams, and
combining them would put two metadata printers on one pipe with nothing keeping
their blocks apart. `parse_luma_stream` keeps only `YAVG`: signalstats prints
fifteen fields a frame, and a ninety-minute film is 130,000 frames.

**The chain is built to order.** It runs only when the target has a rule that
needs it, and then carries only the filters those rules read —
`video.FILTER_METRICS` maps one to the other, `preflight.picture_filters`
applies it. This is not a micro-optimisation. Timed on a 1080p60 file, the
whole chain runs at roughly 0.9x the file's own duration: a ninety-minute
feature is about eighty minutes of work, of which `idet` is half and
`signalstats` a third, while `blackdetect` and `freezedetect` together are a
tenth. A target that asks only about black frames should not pay for the
field detector, and before this it did.

Anything switched off measures **None, not zero**. `_derive` guards every
total for that reason: zero seconds of black is a finding and no answer is
not, and a check nobody ran must not be able to come back green.

**`--picture full` exists because "the target does not ask" and "the file is
fine" are different sentences.** It measures everything whatever the target
wants, and `report.picture_lines` prints what no rule read — otherwise paying
for the full pass would buy nothing anybody can see.

**On making it faster.** Three things were measured on an 8-core machine, and
two of them do nothing: `-filter_threads 8` came back at 1.05x the baseline,
and `-hwaccel videotoolbox` at 0.98x — the cost is in the filters, which run
on one core, not in the decode. Halving the width does help, roughly 1.8x, and
quartering it 3.5x. `video.width_divisor` allows it and refuses it with the
field checks: on a near-static picture with one small moving element, full
width reports progressive and half width reports that it cannot tell. An
inconclusive answer is not a cheaper answer. Vertical scaling is never offered
at all, because blending adjacent lines is exactly what `idet` compares.

**Do not make the pass bail out early.** `idet` reports every frame as
undetermined on static content — a lyric video, a slideshow, a locked-off
talking head — so on that material the most expensive filter in the chain
costs 27 s a minute and answers nothing. Sampling the opening and giving up
when no verdict has appeared would roughly halve it. It has been considered
and declined twice over.

It would be wrong about the file: "no evidence yet" and "no evidence ever"
are different claims, and a programme that opens on a static title card
before cutting to interlaced footage reads as the first and is the second.

It would also be wrong about itself, which matters just as much here. This
tool states what a pass will cost before it starts, and somebody schedules an
afternoon around that sentence. A pass that finishes early depending on
content the estimate has not read yet turns a number you can plan around into
a number that is sometimes smaller. Accurate beats fast; a promise about
runtime is part of the product.

Captions cost no decode at all unless they are embedded, in which case one
`ffmpeg -f ass -` extraction reads them.

`captions.align` costs none either: it compares the cues against the
`silences` the audio pass already measured. Three findings come out of that —
sound nobody captioned, a constant offset, and cues over silence — and they
are the ones that actually save somebody an afternoon.

- **Gaps within a run, not runs as a whole.** `_gaps_in` subtracts the cues
  from each stretch of sound. Judging a whole passage as covered or not was the
  first attempt and it hid a twelve-second passage with three seconds of
  caption on the front.
- **Drift is a median with a confidence figure.** Cues legitimately sit
  mid-sentence; one of those must not become the answer, and a file where a
  third of cues matched nothing has not been measured.
- **Music is legitimately uncaptioned.** The six-second minimum and the `house`
  basis are both admissions that this finds passages to look at, not faults.

## Things measured the hard way, for a reason

- **The timeline is bucketed to one second.** ebur128 reports every 100 ms; the
  report quotes seconds. A ten-hour audiobook costs 36,000 rows instead of
  360,000, and nothing is lost that survives to the page.
- **Timestamps only when the quantity matches.** `checks.LOCATABLE` is the
  whole rule: short-term loudness can locate an integrated failure, and cannot
  locate an RMS failure. Adding an entry there is claiming two measurements are
  the same measurement. Be sure.
- **Fast start is read, not decoded.** An MP4's top level is a list of boxes,
  each headed by its own length, so `probe.atom_order` walks the whole thing in
  a handful of seeks without touching a byte of media. `moov` behind `mdat`
  means nothing plays until the file has finished downloading. Handles the
  64-bit length escape (`size == 1`) and the run-to-end case (`size == 0`);
  returns None for anything that is not ISO-BMFF, because the question does
  not apply to a WAV.
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
- **Interlacing is measured, not read.** `field_order` in the header is a
  claim, and telecined material routinely declares itself progressive.
  `video.classify_fields` decides from `idet`, which is in the chain the
  picture pass already runs — **but idet alone is not enough**: on progressive
  material with hard vertical edges and fast motion it reports most frames as
  interlaced (a test pattern here reads TFF 42 / BFF 25 of 75). The
  discriminator is that real interlacing is overwhelmingly *one* field order
  and the false positives are mixed, so both a share test and a dominance test
  must pass. Anything that passes one and fails the other is `inconclusive`,
  which is a truthful answer and not a verdict. A static shot is `unknown`,
  because idet calls every frame undetermined and absence of evidence is not
  evidence.
- **`interlaced` believes either source.** Header or picture saying interlaced
  is enough, because either alone is a reason to deinterlace;
  `field_order_disagrees` reports the contradiction separately, and is usually
  the more useful finding.
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
- **A gap in the numbering is a delivery fault.** `batch.numbering` only
  reports one where there is actually a sequence: three or more files sharing
  a prefix, a suffix **and a digit width**. The width matters — grouping
  `a1, a2, a10` together would invent six missing files. A folder of unrelated
  names reports nothing, which is the right answer.

### Correcting one

`batch.correct` is plan → write → measure → rebuild, and **both front ends call
it**, so the window and the command line cannot drift apart on the promise. A
test asserts that both files mention it.

- **A file breaking no rule of its own still gets corrected.** That is the
  point: every chapter inside ACX's band, four decibels apart, is a delivery
  fault and no file's fault. `batch._set_derived_findings` synthesises findings
  in the ordinary shape so the existing planner handles them — guards,
  ordering, caveats and all — and `corrections.plan` takes an `overrides` dict
  for the decisions only a set can make (a per-file rule allows mono *or*
  stereo; only the delivery knows this title is mono).
- **The majority decides format; the target decides level.** Bringing four
  quiet chapters up to meet a loud fifth would satisfy the set rule by making
  every file wrong.
- **Rebuilding is not optional here.** A stereo chapter downmixed to mono
  returns at a level nothing could have predicted — how much a downmix costs
  depends on how alike the channels were. Measured, not assumed, and rebuilt
  from the source.
- **`corrections.refine` fixes level before peak.** A file corrected to the
  wrong level peaks too high *because* it is too loud; chasing the limiter
  never fixes that, and lowering the level lowers the peak. Getting this
  backwards cost two wasted rebuild rounds and a file that never converged.

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
- **A corrected copy is drawn over its original.** `chart.svg(baseline=…)`
  takes an earlier reduced timeline and draws it dashed behind. Both series
  are plotted against whichever file is **longer**, and the y-range takes in
  both — scaling them to a common width, or clipping the earlier one, would
  hide exactly the change somebody is looking for.
- **The missing-band note has a short form.** `band_for` returns `absent` (the
  full sentence, for the report) and `short` (for the chart, which has about
  a hundred characters of room before the text runs off the edge or lands on
  the time axis).
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
- **The app cache is keyed on file identity**, including size, nanosecond
  modification and change times, device and inode. It must still refuse
  directories: `os.stat` succeeds on a folder, but a delivery cache is valid
  only while every measured input is still the same regular file. Picture
  depth is part of both single-file and delivery keys so a selective pass can
  never stand in for a full one.

## Guided and Professional

One page, two amounts of exposed decision. The mode is a presentation
preference: it lives in `localStorage`, it is never sent with a job, and
switching it re-renders the queue without re-measuring anything. **Anything
that made the two modes disagree about a verdict would be a bug in
index.html, not a feature.** Guided asks for a target once per kind;
Professional asks once per file and adds the picture-depth control. Same
question, same answers, different number of times.

`intake.classify` runs first in both. `/api/intake` returns the sorted list
with the probe output stripped and keeps the full envelope server-side under a
token, because `/api/estimate` needs the width, height and frame rate that
were in it and probing a folder twice to avoid holding it once is the wrong
trade.

The queue runs as one `/api/check` per file from the page, in order, and not
as a batch endpoint. Three reasons: every result lands in the same cache a
single check would fill, so opening a row afterwards is a lookup; one file
that cannot be measured does not take the rest with it; and the bar can be
weighted by `/api/estimate` rather than by file count. That last one is not a
nicety — eight WAVs and eight lyric videos from one album are 1.9 seconds each
and 180 seconds each, so a file-counting bar spends half its travel on one
percent of the work.

Estimates are given as a range. They are arithmetic over constants measured on
one laptop, and "about 16 to 33 minutes" is honest in a way "1406 seconds" is
not.

## What a profile is for

`profiles.applies_to` reads it off the rules. Profiles used to carry a `kind`
field; it was consumed nowhere and it was wrong — `youtube`, `web` and
`social_vertical` all declared `audio` while carrying eleven, nine and five
picture rules, and `house` declared nothing. It has been removed rather than
corrected, because a label kept beside the rules drifts from them and the
rules cannot drift from themselves.

`checks.NEEDS` says which stream each metric is a question about — `audio`,
`picture`, `captions` or `any`. A profile stating a picture rule is a picture
target and is offered for video only. One stating no picture rule but some
audio rule is offered for audio *and* video, because a video's soundtrack
still has to meet R 128 and the profile simply has nothing to say about the
picture. The converse does not hold: offering YouTube for a WAV is a dropdown
proposing to check the frame rate of a sound file.

Two traps, both of which caught this on the way in:

- **`av_duration_gap_s` is `any`, not `picture`.** Every profile carries it,
  so counting it as a picture question makes every profile a video target.
- **The universal rules do not decide what a profile is for.** Clipping,
  phase, silent channels and overlapping captions are attached to *every*
  profile including the caption one, which presents as an audio target until
  they are set aside. `with_universal` marks what it injects with
  `universal: True` and `applies_to` skips those.

`profiles.for_kind` lists what to offer; `profiles.accepts` refuses on the
server what the page would have hidden, because a dropdown is a convenience
and not a guarantee about what will arrive.

## The numbers in profiles.py

Every built-in target carries `source`, `checked` and `confidence`, and every
rule may carry `basis` — `published`, `observed` or `house`. **Provenance is
per rule, not per profile**, because within one target some numbers are
published and some are not: YouTube documents its encoding settings and states
no loudness figure anywhere, so its encoding rules are `published` and its
−14 LUFS is `observed`. Findings on `observed` and `house` rules say so in
front of their note.

Every universal rule is `house`. They get attached to `published` profiles, and
without an explicit basis they would inherit a provenance they do not have.

**When you change a threshold, change `checked` too**, and read the source
before you change it. All four published targets were audited against their own
documents in September 2026 and three were wrong:

- **ACX room tone.** The page says "We recommend between 1 and 5 seconds of
  room tone at the beginning and end of each file". The 0.5–1 s opening that
  most third-party guidance repeats is on no ACX page. Corrected to 1–5 at both
  ends, `warn` rather than `fail` because the page says *recommend*, with the
  opening aiming at 1.5 s via `target`.
- **Spotify.** −14 LUFS is published for music playback normalisation. Spotify
  publishes nothing for podcast delivery, and the profile claimed it did.
  `confidence` is now `informal`, and −2 dBTP for masters louder than −14 LUFS
  became the warning band inside the −1 dBTP limit.
- **YouTube.** Its guide says interlaced content *must* be deinterlaced before
  uploading, so that rule fails rather than warns; the audio codec list gained
  Opus; the bitrate note now records that 128 is the mono figure and 384 the
  stereo one, which a single rule cannot express.

EBU R 128 was correct as written, including the ±0.5 LU normal tolerance and
the ±1.0 LU permitted for live programmes.

This tool measures exactly and compares against a number you can see and edit;
it is not an oracle for somebody else's current ingest rules, and the moment it
presents itself as one it is lying.

## Build and check

```sh
python3 -m unittest discover -s tests    # 346 checks, about thirty seconds
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

Nothing in the original three-week plan, and nothing named since. The list of
things this tool could deterministically catch and does not is, for the
moment, empty. When something goes on it, the three questions under "Adding a
check" are how to decide what shape it takes.
