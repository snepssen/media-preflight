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
`duration_min`, `av_duration_gap_s`

A metric the file cannot answer — a video rule on an audio file — is *skipped*,
not failed. Absence is not a fault.

## Where the timestamps come from

A finding carries timestamps only when the second-by-second timeline holds the
same quantity the rule is about.

Short-term loudness can locate an integrated-loudness failure, because both are
loudness. It cannot locate an RMS failure: RMS and LUFS are different
measurements, and pointing at a moment measured in one while quoting a
threshold in the other would be an invention. Rules that cannot be located say
so — *"measured across the whole file — there is no single moment to point
at"* — rather than offering a number that looks precise and means nothing.

Sample-peak and clipping failures are located exactly, by a second pass that
measures the peak of every one-second window. That pass runs only when the
cheap whole-file measurement says a peak problem can exist at all: a file whose
loudest sample is below the clipping threshold has no clipped samples, and that
is arithmetic rather than an estimate.

## Measurement options

`options` changes how the file is measured, not what is required of it.

| option | default | what it does |
| --- | --- | --- |
| `silence_threshold_db` | -45.0 | below this counts as silence |
| `silence_min_s` | 0.30 | shorter gaps are speech, not silence |
| `clip_threshold_dbfs` | -0.10 | a sample at or above this is clipping |

The silence threshold matters more than it looks. "Room tone" is a claim about
a level, and a target that asks for a quiet ending is asking for the room, not
for digital zero — ACX's profile lowers the threshold to -50 dB for exactly
that reason.

## Always-on rules

Every profile also gets a set of universal rules — clipping, dead channels,
channel balance, DC offset, stereo phase, audio/video duration agreement. These
are faults on any target, so they are never a `fail` on their own account;
they warn, except a completely silent channel, which fails.

Give a rule the same `id` as a universal one and yours replaces it.
