"""The parsers, against ffmpeg output recorded verbatim.

Nothing here runs ffmpeg. Every string in this file was copied from a real run,
so a change in ffmpeg's output shape breaks these tests rather than quietly
producing a report full of dashes.
"""

import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import analysis  # noqa: E402


EBUR128_SUMMARY = """
[Parsed_ebur128_0 @ 0x7586c409c0] Summary:

  Integrated loudness:
    I:         -23.4 LUFS
    Threshold: -33.6 LUFS

  Loudness range:
    LRA:         6.2 LU
    Threshold: -43.7 LUFS
    LRA low:   -27.1 LUFS
    LRA high:  -20.9 LUFS

  True peak:
    Peak:       -1.8 dBFS
"""

ASTATS = """
[Parsed_astats_3 @ 0x7f] Channel: 1
[Parsed_astats_3 @ 0x7f] DC offset: -0.000015
[Parsed_astats_3 @ 0x7f] Peak level dB: -6.020600
[Parsed_astats_3 @ 0x7f] RMS level dB: -20.492174
[Parsed_astats_3 @ 0x7f] RMS peak dB: -9.015201
[Parsed_astats_3 @ 0x7f] RMS through dB: -inf
[Parsed_astats_3 @ 0x7f] Flat factor: 0.000000
[Parsed_astats_3 @ 0x7f] Abs Peak count: 600
[Parsed_astats_3 @ 0x7f] Noise floor dB: -62.400000
[Parsed_astats_3 @ 0x7f] Bit depth: 15/16/16/16
[Parsed_astats_3 @ 0x7f] Channel: 2
[Parsed_astats_3 @ 0x7f] DC offset: 0.000000
[Parsed_astats_3 @ 0x7f] Peak level dB: -90.000000
[Parsed_astats_3 @ 0x7f] RMS level dB: -inf
[Parsed_astats_3 @ 0x7f] Noise floor dB: -inf
[Parsed_astats_3 @ 0x7f] Overall
[Parsed_astats_3 @ 0x7f] DC offset: -0.000015
[Parsed_astats_3 @ 0x7f] Peak level dB: -6.020600
[Parsed_astats_3 @ 0x7f] RMS level dB: -20.492174
[Parsed_astats_3 @ 0x7f] Noise floor dB: -62.400000
"""

METADATA = """frame:0    pts:0       pts_time:0
lavfi.r128.M=-120.691
lavfi.r128.S=-120.691
lavfi.r128.true_peak=0.500
frame:1    pts:4410    pts_time:0.1
lavfi.r128.M=-14.2
lavfi.r128.S=-15.9
lavfi.r128.true_peak=0.700
frame:2    pts:8820    pts_time:1.4
lavfi.r128.M=-11.0
lavfi.r128.S=-12.5
lavfi.r128.true_peak=0.700
"""

SILENCES = """
[Parsed_silencedetect_2 @ 0x7f] silence_start: 0
[Parsed_silencedetect_2 @ 0x7f] silence_end: 1.5 | silence_duration: 1.5
[Parsed_silencedetect_2 @ 0x7f] silence_start: 40.25
[Parsed_silencedetect_2 @ 0x7f] silence_end: 41 | silence_duration: 0.75
[Parsed_silencedetect_2 @ 0x7f] silence_start: 58.5
"""


class SummaryTests(unittest.TestCase):
    def test_reads_every_loudness_figure(self):
        out = analysis.parse_ebur128_summary(EBUR128_SUMMARY)
        self.assertAlmostEqual(out["integrated_lufs"], -23.4)
        self.assertAlmostEqual(out["loudness_range_lu"], 6.2)
        self.assertAlmostEqual(out["lra_low_lufs"], -27.1)
        self.assertAlmostEqual(out["true_peak_dbfs"], -1.8)

    def test_missing_summary_gives_nothing_rather_than_raising(self):
        out = analysis.parse_ebur128_summary("no summary here")
        self.assertIsNone(out["integrated_lufs"])


class AstatsTests(unittest.TestCase):
    def setUp(self):
        self.stats = analysis.parse_astats(ASTATS)

    def test_separates_channels_from_the_overall_block(self):
        self.assertEqual(len(self.stats["channels"]), 2)
        self.assertAlmostEqual(self.stats["rms_dbfs"], -20.492174)

    def test_accepts_both_spellings_of_the_trough_label(self):
        # ffmpeg writes "RMS through dB" in some builds and "trough" in others.
        self.assertEqual(self.stats["channels"][0]["rms_trough_dbfs"],
                         float("-inf"))

    def test_a_silent_channel_reads_as_negative_infinity(self):
        self.assertEqual(self.stats["channels"][1]["rms_dbfs"], float("-inf"))


class TimelineTests(unittest.TestCase):
    def test_buckets_to_the_second_and_keeps_the_loudest(self):
        timeline = analysis.parse_metadata_stream(METADATA.splitlines())
        rows = timeline.rows()
        self.assertEqual([r["t"] for r in rows], [0, 1])
        self.assertAlmostEqual(rows[0]["momentary"], -14.2)
        self.assertAlmostEqual(rows[1]["short_term"], -12.5)

    def test_discards_the_empty_window_ebur128_reports_as_minus_120(self):
        timeline = analysis.parse_metadata_stream(METADATA.splitlines())
        # Second 0 holds both -120.691 and -14.2; only the real one survives.
        self.assertAlmostEqual(timeline.rows()[0]["momentary"], -14.2)

    def test_records_when_the_running_true_peak_moved(self):
        timeline = analysis.parse_metadata_stream(METADATA.splitlines())
        self.assertAlmostEqual(timeline.rows()[0]["true_peak_dbfs"], -3.098,
                               places=2)

    def test_runs_outside_ignores_a_single_second(self):
        timeline = analysis.Timeline()
        for second, value in enumerate([-20, -5, -4, -20, -3, -20, -2, -1]):
            timeline.add(float(second), {"r128.S": value})
        runs = timeline.runs_outside("short_term", maximum=-10)
        self.assertEqual([(r["start"], r["end"]) for r in runs],
                         [(1, 3), (6, 8)])
        self.assertEqual(runs[1]["worst"], -1)


class SilenceTests(unittest.TestCase):
    def test_positions_and_closes_an_unterminated_tail(self):
        silences = analysis.parse_silences(SILENCES, duration_s=60.0)
        self.assertEqual([s["position"] for s in silences],
                         ["head", "middle", "tail"])
        self.assertAlmostEqual(silences[-1]["duration"], 1.5)

    def test_a_file_that_is_never_quiet_has_no_silences(self):
        self.assertEqual(analysis.parse_silences("", 10.0), [])


class DeriveTests(unittest.TestCase):
    def _measurements(self, **overrides):
        m = {"silences": analysis.parse_silences(SILENCES, 60.0),
             "timeline": [{"t": 59, "momentary": -12.0, "short_term": -12.0,
                           "true_peak_dbfs": None, "phase": None}],
             "channels": [{"channel": 1, "rms_dbfs": -20.0},
                          {"channel": 2, "rms_dbfs": -90.0}]}
        m.update(overrides)
        return m

    def test_lead_and_tail_come_from_position_not_order(self):
        m = self._measurements()
        analysis._derive(m, 60.0)
        self.assertAlmostEqual(m["lead_silence_s"], 1.5)
        self.assertAlmostEqual(m["tail_silence_s"], 1.5)
        self.assertEqual(len(m["mid_silences"]), 1)

    def test_a_silent_channel_suppresses_the_balance_figure(self):
        m = self._measurements()
        analysis._derive(m, 60.0)
        self.assertEqual(m["silent_channels"], [2])
        self.assertIsNone(m["channel_rms_spread_db"])

    def test_abrupt_ending_needs_both_no_tail_and_programme_level(self):
        m = self._measurements(silences=[])
        analysis._derive(m, 60.0)
        self.assertTrue(m["ends_abruptly"])
        quiet = self._measurements(
            silences=[],
            timeline=[{"t": 59, "momentary": -55.0, "short_term": -55.0,
                       "true_peak_dbfs": None, "phase": None}])
        analysis._derive(quiet, 60.0)
        self.assertFalse(quiet["ends_abruptly"])


class PeakWindowTests(unittest.TestCase):
    WINDOWS = """frame:0    pts:0       pts_time:0
lavfi.astats.Overall.Peak_level=-12.000000
frame:1    pts:1       pts_time:1
lavfi.astats.Overall.Peak_level=-0.000100
lavfi.astats.Overall.Abs_Peak_count=4000.000000
frame:2    pts:2       pts_time:2
lavfi.astats.Overall.Peak_level=-0.000100
lavfi.astats.Overall.Abs_Peak_count=1000.000000
frame:3    pts:3       pts_time:3
lavfi.astats.Overall.Peak_level=-30.000000
"""

    def test_keeps_only_offending_windows_and_merges_neighbours(self):
        windows = analysis.parse_peak_windows(self.WINDOWS, -0.1)
        self.assertEqual(len(windows), 1)
        self.assertEqual((windows[0]["start"], windows[0]["end"]), (1.0, 3.0))
        self.assertEqual(windows[0]["samples"], 5000)

    def test_a_clean_file_produces_nothing(self):
        self.assertEqual(analysis.parse_peak_windows(self.WINDOWS, 10.0), [])


class FilterChainTests(unittest.TestCase):
    def test_phase_is_only_measured_on_stereo(self):
        options = dict(analysis.DEFAULTS)
        self.assertIn("aphasemeter", analysis.build_filter_chain(2, options))
        self.assertNotIn("aphasemeter", analysis.build_filter_chain(1, options))
        self.assertNotIn("aphasemeter", analysis.build_filter_chain(6, options))

    def test_silence_thresholds_come_from_the_options(self):
        chain = analysis.build_filter_chain(
            1, {"silence_threshold_db": -50.0, "silence_min_s": 0.25})
        self.assertIn("silencedetect=noise=-50dB:d=0.25", chain)


if __name__ == "__main__":
    unittest.main()
