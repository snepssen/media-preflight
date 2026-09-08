"""The picture parsers, against ffmpeg output recorded verbatim."""

import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import video  # noqa: E402


BLACK = """
[Parsed_blackdetect_0 @ 0x7f] black_start:3 black_end:4.96 black_duration:1.96
[Parsed_blackdetect_0 @ 0x7f] black_start:0 black_end:0.8 black_duration:0.8
"""

BLACK_TO_EOF = """
[Parsed_blackdetect_0 @ 0x7f] black_start:58.5
"""

FREEZE = """
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_start: 3
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_duration: 2.5
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_end: 5.5
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_start: 20
"""

SIGNALSTATS = """frame:0    pts:0       pts_time:0
lavfi.signalstats.YMIN=22
lavfi.signalstats.YAVG=120.825
lavfi.signalstats.YMAX=232
frame:1    pts:1       pts_time:0.04
lavfi.signalstats.YAVG=121.0
frame:2    pts:2       pts_time:0.08
lavfi.signalstats.YAVG=16.0
"""


class BlackTests(unittest.TestCase):
    def test_intervals_are_read_and_placed(self):
        black = video.parse_black(BLACK, duration_s=5.0)
        self.assertEqual(len(black), 2)
        self.assertEqual(black[0]["position"], "tail")
        self.assertEqual(black[1]["position"], "head")

    def test_black_running_to_the_end_is_closed_at_the_end(self):
        black = video.parse_black(BLACK_TO_EOF, duration_s=60.0)
        self.assertAlmostEqual(black[0]["duration"], 1.5)
        self.assertEqual(black[0]["position"], "tail")

    def test_a_file_that_is_never_black_has_no_intervals(self):
        self.assertEqual(video.parse_black("", 10.0), [])


class FreezeTests(unittest.TestCase):
    def test_a_start_without_an_end_runs_to_the_end_of_the_file(self):
        frozen = video.parse_freeze(FREEZE, duration_s=30.0)
        self.assertEqual(len(frozen), 2)
        self.assertAlmostEqual(frozen[0]["duration"], 2.5)
        self.assertAlmostEqual(frozen[1]["duration"], 10.0)
        self.assertEqual(frozen[1]["position"], "tail")


class LumaTests(unittest.TestCase):
    def test_only_average_luma_is_kept(self):
        luma = video.parse_luma_stream(SIGNALSTATS.splitlines())
        self.assertEqual([round(t, 2) for t, _ in luma], [0.0, 0.04, 0.08])
        self.assertEqual([v for _, v in luma], [120.825, 121.0, 16.0])


class FlashTests(unittest.TestCase):
    OPTIONS = dict(video.DEFAULTS)

    def _luma(self, values, step=1 / 30):
        return [(index * step, value) for index, value in enumerate(values)]

    def test_three_transitions_in_a_second_is_a_region(self):
        luma = self._luma([16, 235, 16, 235, 16, 235] * 3)
        regions = video.find_flashing(luma, self.OPTIONS, duration_s=1.0)
        self.assertEqual(len(regions), 1)
        self.assertGreaterEqual(regions[0]["worst"], 3)

    def test_two_transitions_a_second_is_not(self):
        # Two changes a second, held for eight seconds: under the threshold
        # however long it goes on for.
        luma = []
        for index in range(16):
            luma.append((index * 0.5, 16 if index % 2 else 235))
        self.assertEqual(video.find_flashing(luma, self.OPTIONS, 8.0), [])

    def test_a_gradual_fade_is_not_a_flash(self):
        luma = self._luma([16 + index for index in range(120)])
        self.assertEqual(video.find_flashing(luma, self.OPTIONS, 4.0), [])

    def test_regions_are_clamped_to_the_file(self):
        luma = self._luma([16, 235] * 30)
        regions = video.find_flashing(luma, self.OPTIONS, duration_s=2.0)
        self.assertLessEqual(regions[0]["end"], 2.0)


class FrameRateTests(unittest.TestCase):
    def test_a_constant_rate_survives_a_ragged_last_frame(self):
        durations = [0.04] * 200 + [0.017]
        self.assertEqual(video.classify_frame_durations(durations), "cfr")

    def test_ntsc_microsecond_drift_is_still_constant(self):
        durations = [0.033367, 0.033366] * 100
        self.assertEqual(video.classify_frame_durations(durations), "cfr")

    def test_two_rates_in_one_file_is_variable(self):
        durations = [0.04] * 50 + [0.02] * 50
        self.assertEqual(video.classify_frame_durations(durations), "vfr")

    def test_too_few_frames_to_tell_says_so(self):
        self.assertEqual(video.classify_frame_durations([0.04] * 5), "unknown")


class DeriveTests(unittest.TestCase):
    def test_totals_separate_the_ends_from_the_middle(self):
        measurements = {
            "black": video.parse_black(BLACK, 5.0),
            "frozen": video.parse_freeze(FREEZE, 30.0),
            "flashes": [{"start": 1.0, "end": 2.0, "worst": 4}],
        }
        video._derive(measurements, 5.0)
        self.assertAlmostEqual(measurements["black_seconds"], 2.76)
        self.assertAlmostEqual(measurements["leading_black_s"], 0.8)
        self.assertAlmostEqual(measurements["trailing_black_s"], 1.96)
        self.assertAlmostEqual(measurements["longest_frozen_s"], 10.0)
        self.assertEqual(measurements["flash_regions"], 1)


class ChainTests(unittest.TestCase):
    def test_thresholds_come_from_the_options(self):
        chain = video.build_filter_chain(dict(video.DEFAULTS,
                                              black_min_s=1.5,
                                              freeze_min_s=3.0))
        self.assertIn("blackdetect=d=1.5", chain)
        self.assertIn("freezedetect=n=-60dB:d=3", chain)
        self.assertTrue(chain.endswith("signalstats,metadata=print:file=-"))


if __name__ == "__main__":
    unittest.main()


# idet's actual output on files whose truth is known, recorded verbatim. The
# progressive case is the one that matters: a synthetic pattern with hard
# vertical edges and fast motion, which idet reports as overwhelmingly
# interlaced and which is not interlaced at all.
IDET_PROGRESSIVE_FALSE_POSITIVE = """
[Parsed_idet_0 @ 0x7f] Repeated Fields: Neither:     0 Top:     0 Bottom:     0
[Parsed_idet_0 @ 0x7f] Multi frame detection: TFF:     0 BFF:     0 Progressive:     0 Undetermined:     0
[Parsed_idet_0 @ 0x7f] Repeated Fields: Neither:    75 Top:     0 Bottom:     0
[Parsed_idet_0 @ 0x7f] Multi frame detection: TFF:    42 BFF:    25 Progressive:     8 Undetermined:     0
"""

IDET_INTERLACED = """
[Parsed_idet_0 @ 0x7f] Repeated Fields: Neither:    75 Top:     0 Bottom:     0
[Parsed_idet_0 @ 0x7f] Multi frame detection: TFF:    75 BFF:     0 Progressive:     0 Undetermined:     0
"""

IDET_TELECINED = """
[Parsed_idet_0 @ 0x7f] Repeated Fields: Neither:    72 Top:    24 Bottom:    24
[Parsed_idet_0 @ 0x7f] Multi frame detection: TFF:   120 BFF:     0 Progressive:     0 Undetermined:     0
"""

IDET_PROGRESSIVE = """
[Parsed_idet_0 @ 0x7f] Repeated Fields: Neither:    75 Top:     0 Bottom:     0
[Parsed_idet_0 @ 0x7f] Multi frame detection: TFF:     0 BFF:     0 Progressive:    75 Undetermined:     0
"""

IDET_STATIC = """
[Parsed_idet_0 @ 0x7f] Repeated Fields: Neither:    75 Top:     0 Bottom:     0
[Parsed_idet_0 @ 0x7f] Multi frame detection: TFF:     0 BFF:     0 Progressive:     0 Undetermined:    75
"""


class InterlaceTests(unittest.TestCase):
    OPTIONS = dict(video.DEFAULTS)

    def _classify(self, text):
        return video.classify_fields(video.parse_idet(text), self.OPTIONS)

    def test_the_last_summary_is_the_one_that_counts(self):
        """idet prints an all-zero block first, as an artefact of flushing."""
        counts = video.parse_idet(IDET_PROGRESSIVE_FALSE_POSITIVE)
        self.assertEqual(counts["tff"], 42)
        self.assertEqual(counts["repeated_neither"], 75)

    def test_mixed_field_orders_are_inconclusive_rather_than_interlaced(self):
        """The failure mode that makes a naive threshold useless: 67 of 75
        frames read as interlaced on material that is not."""
        out = self._classify(IDET_PROGRESSIVE_FALSE_POSITIVE)
        self.assertGreater(out["interlace_share"], 0.8, "it looks interlaced")
        self.assertLess(out["field_dominance"], 0.8, "but it cannot pick a field")
        self.assertEqual(out["interlace_detected"], "inconclusive")

    def test_one_dominant_field_order_is_interlaced(self):
        out = self._classify(IDET_INTERLACED)
        self.assertEqual(out["interlace_detected"], "tff")
        self.assertEqual(out["field_dominance"], 1.0)

    def test_smooth_progressive_material_reads_as_progressive(self):
        self.assertEqual(self._classify(IDET_PROGRESSIVE)["interlace_detected"],
                         "progressive")

    def test_a_static_shot_gives_no_evidence_rather_than_a_verdict(self):
        out = self._classify(IDET_STATIC)
        self.assertEqual(out["interlace_detected"], "unknown")
        self.assertIsNone(out["interlace_share"])

    def test_repeated_fields_are_reported_as_telecine(self):
        out = self._classify(IDET_TELECINED)
        self.assertAlmostEqual(out["telecine_ratio"], 0.4)
        self.assertEqual(out["interlace_detected"], "tff")

    def test_nothing_measured_claims_nothing(self):
        out = self._classify("")
        self.assertEqual(out["interlace_detected"], "unknown")
        self.assertIsNone(out["telecine_ratio"])

    def test_the_detector_is_in_the_chain_that_already_runs(self):
        chain = video.build_filter_chain(dict(video.DEFAULTS))
        self.assertIn("idet", chain, "no second decode for this")
