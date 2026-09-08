# Writing a target

A delivery target is a JSON file. Drop one into `profiles/` and it appears in
the window's target list and on the command line; point `--target` at a path
and it is used without being installed anywhere.

```json
{
  "id": "house",
  "label": "House standard",
  "summary": "What we ship to clients unless they ask for something else.",
  "source": "Our own delivery note, 2026 revision",
  "checked": "2026-09",
  "confidence": "informal",
  "options": { "silence_threshold_db": -50.0 },
  "rules": [
    {
      "id": "integrated",
      "metric": "integrated_lufs",
      "label": "Integrated loudness",
      "unit": "LUFS",
      "min": -17.0, "max": -13.0,
      "warn_min": -15.5, "warn_max": -14.5,
      "severity": "fail",
      "fix": "loudnorm",
      "note": "Clients play these on laptops. Quieter than -17 disappears."
    },
    {
      "id": "true_peak",
      "metric": "true_peak_dbfs",
      "label": "True peak",
      "unit": "dBTP",
      "max": -1.0,
      "severity": "fail",
      "fix": "loudnorm"
    }
  ]
}
```

Run `python3 preflight.py check file.wav --target profiles/house.json`.

## The header

| field | required | what it is |
| --- | --- | --- |
| `id` | yes | short name used on the command line |
| `label` | yes | what the window calls it |
| `summary` | no | one sentence under the target picker |
| `source` | no | where the numbers came from — printed on every report |
| `checked` | no | when you last read that source |
| `confidence` | no | `published` or `informal`; `informal` prints a warning |
| `options` | no | measurement parameters, below |

`source` and `confidence` are not decoration. A report that quotes a threshold
without saying where it came from invites somebody to treat this tool as the
authority on another company's ingest rules, which it is not and cannot be.

## Rules

Every rule needs `id`, `metric` and `label`, plus at least one requirement.

| requirement | means |
| --- | --- |
| `min`, `max` | an inclusive band; outside it is `severity` |
| `warn_min`, `warn_max` | a looser inner band; inside the band but outside this is a warning |
| `one_of` | the value must be in this list |
| `equals` | exact match |
| `forbid` | a flag that must be false |
| `require` | a flag that must be true |

`severity` is `fail`, `warn` or `info` and defaults to `fail`. `note` is
printed under the finding when it fails or warns — use it for the sentence you
would say out loud, not for a restatement of the number.

`target` names the value a correction should aim at inside the band, when the
middle is not it. ACX asks for one to five seconds of room tone; the middle is
three, which is a sensible ending and an absurd opening, so the opening rule
sets `"target": 1.5`.

`basis` says where the threshold came from — **per rule, not per profile**,
because within one target some numbers are published and some are not:

| basis | means |
| --- | --- |
| `published` | quoted from the target's own specification |
| `observed` | measured behaviour the target does not publish |
| `house` | this tool's own threshold, a starting point |

YouTube publishes its encoding settings and no loudness figure at all; the
−14 LUFS everybody quotes is measured behaviour. A report that presented both
as equally authoritative would be misleading about the more important one, so
findings on `observed` and `house` rules say so in front of their note. A rule
with no `basis` inherits the profile's `confidence`.

`fix` names an operation the corrector knows how to plan. A rule with no `fix`
is reported and left alone, which is the right answer for anything a machine
should not decide on your behalf.

| fix | what it plans |
| --- | --- |
| `loudnorm` | two-pass EBU R128 normalisation to a LUFS target and peak ceiling |
| `gain` | a single gain change to land in an RMS band |
| `limit_peak` | a peak limiter set just under the ceiling |
| `remove_dc` | a gentle 15 Hz high-pass |
| `room_tone_head`, `room_tone_tail` | trim or extend the quiet at one end |
| `encode` | codec, bitrate, bitrate mode, sample rate, channel count |
| `faststart` | move an MP4's index in front of its media — a remux, not an encode |

There is deliberately no fix for a picture or caption fault. Trimming black off
an ending is an edit, retiming a cue is an edit, and this tool does not edit.

## Metrics

**Measured from the audio**

`integrated_lufs`, `loudness_range_lu`, `short_term_excursions`,
`true_peak_dbfs`, `peak_dbfs`, `rms_dbfs`, `noise_floor_dbfs`,
`clipping_seconds`, `dc_offset_max`, `channel_rms_spread_db`,
`silent_channel_count`, `phase_min`, `lead_silence_s`, `tail_silence_s`,
`longest_mid_silence_s`, `ends_abruptly`

**Declared by the file**

`audio_codec`, `sample_rate`, `channels`, `audio_bitrate_kbps`,
`bitrate_mode`, `container`, `bit_depth`, `cover_art`, `duration_s`,
`duration_min`, `av_duration_gap_s`, `fast_start`

**Measured from the picture**

`black_seconds`, `longest_black_s`, `leading_black_s`, `trailing_black_s`,
`frozen_seconds`, `longest_frozen_s`, `flash_regions`, `interlaced`,
`field_order_disagrees`, `telecine_ratio`

**Declared by the picture**

`video_codec`, `video_width`, `video_height`, `resolution`, `aspect_ratio`,
`frame_rate`, `frame_rate_mode`, `pix_fmt`, `video_bitrate_kbps`,
`interlace_declared`, `interlace_detected`

`interlaced`, `field_order_disagrees` and `telecine_ratio` are measured rather
than declared, so naming any of them costs the picture pass. That is the point:
the header's `field_order` is a claim, and telecined material routinely
declares itself progressive. `interlaced` is true when either the header or the
picture says so; `field_order_disagrees` reports the case where they cannot
both be right.

**Captions**

`caption_cue_count`, `caption_format`, `caption_overlaps`,
`caption_shortest_cue_s`, `caption_longest_cue_s`, `caption_max_cps`,
`caption_max_line_length`, `caption_max_lines`, `caption_shortest_gap_s`,
`caption_past_end_s`, `caption_empty_cues`, `caption_bad_timing`,
`caption_missing_fonts`, `caption_uncaptioned_speech_s`,
`caption_over_silence`, `caption_drift_s`

The last three compare the captions against the *programme* rather than
against themselves, using the silence the audio pass already measured — so
they need audio, and a caption file checked on its own reports them as
skipped rather than guessing.

A metric the file cannot answer — a picture rule on an audio file, a caption
rule where there are no captions — is *skipped*, not failed. Absence is not a
fault, and the report groups every skipped check onto one line so that four
real findings are not buried under eight dashes.

Naming a picture metric in a rule is what makes the tool decode the picture at
all. A profile with no picture rules never pays for the pass.

## Where the timestamps come from

A finding carries timestamps only when the second-by-second timeline holds the
same quantity the rule is about.

Short-term loudness can locate an integrated-loudness failure, because both are
loudness. It cannot locate an RMS failure: RMS and LUFS are different
measurements, and pointing at a moment measured in one while quoting a
threshold in the other would be an invention. Rules that cannot be located say
so — *"measured across the whole file — there is no single moment to point
at"* — rather than offering a number that looks precise and means nothing.

Picture and caption failures are located by the things that caused them: black
and frozen runs carry their own start and end, a flashing region carries how
many transitions it holds, and a caption fault carries the cue number and the
number that broke the rule — *"cue 41, 26.3 characters a second"*. Which cues
are at fault depends on the rule's own threshold, so the test lives in
`captions.OFFENDERS` beside the cues rather than in the rule engine.

Sample-peak and clipping failures are located exactly, by a second pass that
measures the peak of every one-second window. That pass runs only when the
cheap whole-file measurement says a peak problem can exist at all: a file whose
loudest sample is below the clipping threshold has no clipped samples, and that
is arithmetic rather than an estimate.

## What gets drawn

The report draws loudness over time, and shades the band the target asks for —
but only when the target states that band in the same unit the chart is drawn
in. A rule on `integrated_lufs` or `short_term_excursions` gives a shaded band;
a target that states its requirement as an RMS level gets no band and a line
saying why, because shading a LUFS chart with an RMS figure would be the same
invention this tool refuses everywhere else.

Every interval a finding carries is marked under the chart, along with
silences, black, frozen and flashing runs, and clipped windows. Nothing extra
is needed in a profile to get this: it follows from the rules that failed.

## Cross-file rules

A profile may carry `set_rules` alongside `rules`. They have the same shape and
are checked by `preflight.py batch` against the delivery rather than against
any file in it — which is the only way to express a requirement like ACX's,
that every file in a title share a channel count.

```json
"set_rules": [
  {
    "id": "channels",
    "metric": "set_channels_distinct",
    "label": "Channel count across the title",
    "max": 1.0,
    "severity": "fail",
    "note": "Mono or stereo is your choice; ACX asks that every file in a title make the same one."
  },
  {
    "id": "loudness",
    "metric": "set_loudness_spread_db",
    "label": "Loudness spread across the title",
    "unit": "dB",
    "max": 3.0,
    "severity": "warn"
  }
]
```

**Set metrics**

`set_file_count`, `set_failing_files`, `set_channels_distinct`,
`set_sample_rate_distinct`, `set_codec_distinct`, `set_container_distinct`,
`set_bitrate_mode_distinct`, `set_bit_depth_distinct`,
`set_loudness_spread_db`, `set_peak_spread_db`, `set_total_duration_min`,
`set_longest_file_min`, `set_missing_files`

A `*_distinct` rule counts how many different values the delivery holds, and
its finding names the files in the minority — the odd ones out. When two groups
are the same size there is no odd one out, only a disagreement, so every file
is named rather than a side being picked arbitrarily.

A spread rule names the files furthest from the middle of the set rather than
the loudest and the quietest, because when four chapters agree and a fifth is
four decibels up the quietest of the four is the reference, not the fault.

`set_loudness_spread_db` is measured in whichever loudness the profile's own
rules state — LUFS for a target written in LUFS, RMS for one written in RMS.
A target that states neither gets no comparison and a line saying so.

Every profile also inherits universal set rules — consistent channel count and
sample rate, and a six-decibel loudness spread — which a target replaces for
any metric it states itself, so nothing is checked twice at two thresholds.

## Measurement options

`options` changes how the file is measured, not what is required of it.

| option | default | what it does |
| --- | --- | --- |
| `silence_threshold_db` | -45.0 | below this counts as silence |
| `silence_min_s` | 0.30 | shorter gaps are speech, not silence |
| `clip_threshold_dbfs` | -0.10 | a sample at or above this is clipping |
| `black_min_s` | 0.5 | shorter than this is a cut, not a hole |
| `black_picture_threshold` | 0.98 | how much of the frame must be dark |
| `black_pixel_threshold` | 0.10 | how dark a pixel counts as black |
| `freeze_min_s` | 2.0 | a still shorter than this is a held shot |
| `freeze_noise_db` | -60.0 | how different two frames must be to have moved |
| `flash_luma_delta` | 20.0 | luminance change counted as a transition |
| `flash_per_second` | 3 | transitions in a second that flag a region |
| `interlace_share` | 0.5 | how many decided frames must look interlaced |
| `field_dominance` | 0.8 | how one-sided the field order must be to count |
| `interlace_evidence` | 0.25 | decided frames needed before claiming anything |
| `telecine_ratio` | 0.05 | repeated fields before it reads as pulldown |
| `uncaptioned_min_s` | 6.0 | uncaptioned sound before it is worth reporting |
| `drift_window_s` | 5.0 | how far a cue may sit from speech and still match |
| `drift_min_s` | 0.4 | offset that counts as drift |
| `orphan_margin_s` | 0.35 | how far inside silence a cue must sit to be orphaned |

The silence threshold matters more than it looks. "Room tone" is a claim about
a level, and a target that asks for a quiet ending is asking for the room, not
for digital zero — ACX's profile lowers the threshold to -50 dB for exactly
that reason.

## Always-on rules

Every profile also gets a set of universal rules — clipping, dead channels,
channel balance, DC offset, stereo phase, audio/video duration agreement,
overlapping captions, impossible caption timings, captions past the end, and
missing subtitle fonts. These are faults on any target rather than requirements
of one, so most of them warn; a silent channel, an overlapping cue and a cue
that ends before it starts fail, because none of those is ever intentional.

Give a rule the same `id` as a universal one and yours replaces it.
