"""Reading captions, and the arithmetic done on them once they are read."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import captions  # noqa: E402


SRT = """1
00:00:01,000 --> 00:00:03,500
Hello there
second line

2
00:00:04,000 --> 00:00:06,000
<i>Styled</i> text
"""

VTT = """WEBVTT

NOTE this is a comment, not a cue

STYLE
::cue { color: yellow }

intro
00:01.000 --> 00:03.500 line:90% align:center
Hello there

00:00:04.000 --> 00:00:06.000
Second cue with full hours
"""

ASS = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Helvetica,48
Style: Title,Some Absent Face,72

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,{\\fnInline Font}Second in time
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,First\\Nin time
"""


class SrtTests(unittest.TestCase):
    def test_times_lines_and_markup(self):
        track = captions.parse(SRT, "srt")
        self.assertEqual(track["format"], "srt")
        first, second = track["cues"]
        self.assertAlmostEqual(first["start"], 1.0)
        self.assertAlmostEqual(first["end"], 3.5)
        self.assertEqual(first["line_count"], 2)
        self.assertEqual(second["text"], "Styled text",
                         "markup is stripped, the words are kept")

    def test_reading_speed_is_characters_over_seconds(self):
        track = captions.parse(SRT, "srt")
        cue = track["cues"][0]
        self.assertAlmostEqual(cue["cps"], cue["characters"] / 2.5, places=5)


class VttTests(unittest.TestCase):
    def test_headers_notes_and_styles_are_not_cues(self):
        track = captions.parse(VTT, "vtt")
        self.assertEqual(len(track["cues"]), 2)

    def test_both_timestamp_shapes_are_understood(self):
        cues = captions.parse(VTT, "vtt")["cues"]
        self.assertAlmostEqual(cues[0]["start"], 1.0)
        self.assertAlmostEqual(cues[1]["start"], 4.0)

    def test_cue_settings_after_the_end_time_are_not_part_of_it(self):
        self.assertAlmostEqual(captions.parse(VTT, "vtt")["cues"][0]["end"], 3.5)


class AssTests(unittest.TestCase):
    def test_dialogue_out_of_order_is_sorted_into_time(self):
        cues = captions.parse(ASS, "ass")["cues"]
        self.assertEqual([c["index"] for c in cues], [1, 2])
        self.assertAlmostEqual(cues[0]["start"], 1.0)
        self.assertEqual(cues[0]["text"], "First\nin time")

    def test_fonts_come_from_styles_and_from_inline_overrides(self):
        fonts = captions.parse(ASS, "ass")["fonts"]
        self.assertIn("Helvetica", fonts)
        self.assertIn("Some Absent Face", fonts)
        self.assertIn("Inline Font", fonts)

    def test_override_blocks_are_not_counted_as_words(self):
        cue = captions.parse(ASS, "ass")["cues"][1]
        self.assertEqual(cue["text"], "Second in time")


class MeasureTests(unittest.TestCase):
    OVERLAPPING = """1
00:00:01,000 --> 00:00:03,000
First

2
00:00:02,500 --> 00:00:04,000
Second

3
00:00:05,000 --> 00:00:04,900
Backwards
"""

    def setUp(self):
        self.m = captions.measure(captions.parse(self.OVERLAPPING, "srt"), 10.0)

    def test_an_overlap_is_counted_and_described(self):
        self.assertEqual(self.m["caption_overlaps"], 1)
        interval = self.m["caption_overlap_intervals"][0]
        self.assertIn("cue 2 starts", interval["detail"])

    def test_a_cue_that_ends_before_it_starts_is_caught(self):
        self.assertEqual(self.m["caption_bad_timing"], 1)

    def test_a_zero_length_cue_has_no_reading_speed_rather_than_infinity(self):
        backwards = self.m["cues"][2]
        self.assertIsNone(backwards["cps"])

    def test_cues_past_the_end_are_measured_against_the_media(self):
        m = captions.measure(captions.parse(self.OVERLAPPING, "srt"), 3.0)
        self.assertAlmostEqual(m["caption_past_end_s"], 2.0)

    def test_no_media_duration_means_no_claim_about_the_end(self):
        m = captions.measure(captions.parse(self.OVERLAPPING, "srt"), None)
        self.assertEqual(m["caption_past_end_s"], 0.0)


class OffenderTests(unittest.TestCase):
    def test_the_cues_at_fault_depend_on_the_rule_s_own_threshold(self):
        cues = captions.parse(SRT, "srt")["cues"]
        strict = captions.offending_cues(
            "caption_max_line_length", {"max": 5}, cues)
        lenient = captions.offending_cues(
            "caption_max_line_length", {"max": 80}, cues)
        self.assertTrue(strict)
        self.assertEqual(lenient, [])

    def test_each_interval_names_the_cue_and_the_number(self):
        cues = captions.parse(SRT, "srt")["cues"]
        found = captions.offending_cues("caption_max_cps", {"max": 1.0}, cues)
        self.assertIn("cue 1", found[0]["detail"])
        self.assertIn("characters a second", found[0]["detail"])

    def test_a_metric_with_no_cue_level_test_returns_nothing(self):
        self.assertEqual(
            captions.offending_cues("caption_cue_count", {"min": 1}, []), [])


class DiscoveryTests(unittest.TestCase):
    def test_a_sidecar_is_found_by_name(self):
        with tempfile.TemporaryDirectory() as folder:
            media = os.path.join(folder, "episode.mp4")
            Path(media).write_bytes(b"not really an mp4")
            Path(os.path.join(folder, "episode.srt")).write_text(
                SRT, encoding="utf-8")
            track = captions.find(media, facts={"subtitle_streams": []})
            self.assertEqual(track["origin"], "sidecar")
            self.assertEqual(len(track["cues"]), 2)

    def test_no_captions_anywhere_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            media = os.path.join(folder, "episode.mp4")
            Path(media).write_bytes(b"x")
            self.assertIsNone(captions.find(media,
                                            facts={"subtitle_streams": []}))

    def test_a_named_file_that_is_not_there_says_so(self):
        with self.assertRaises(captions.CaptionError):
            captions.find("/tmp/x.mp4", explicit="/no/such/file.srt")

    def test_picture_subtitles_are_reported_as_unmeasurable(self):
        track = captions.extract("/tmp/x.mkv",
                                 {"index": 2, "codec": "hdmv_pgs_subtitle"})
        self.assertIn("picture subtitles", track["unreadable"])
        self.assertEqual(track["cues"], [])

    def test_a_format_is_recognised_by_its_contents_not_its_name(self):
        self.assertEqual(captions.parse(VTT, "srt")["format"], "vtt")
        self.assertEqual(captions.parse(ASS, "")["format"], "ass")


class FontTests(unittest.TestCase):
    def test_nothing_wanted_is_nothing_missing(self):
        self.assertEqual(captions.missing_fonts([]), [])

    def test_an_unanswerable_question_is_none_rather_than_a_guess(self):
        original = captions.installed_families.__defaults__[0].copy()
        try:
            captions.installed_families.__defaults__[0]["families"] = None
            self.assertIsNone(captions.missing_fonts(["Anything"]))
        finally:
            captions.installed_families.__defaults__[0].clear()
            captions.installed_families.__defaults__[0].update(original)

    def test_families_are_matched_without_caring_about_case(self):
        original = captions.installed_families.__defaults__[0].copy()
        try:
            captions.installed_families.__defaults__[0]["families"] = {
                "Helvetica Neue"}
            self.assertEqual(captions.missing_fonts(["helvetica neue"]), [])
            self.assertEqual(captions.missing_fonts(["Gone"]), ["Gone"])
        finally:
            captions.installed_families.__defaults__[0].clear()
            captions.installed_families.__defaults__[0].update(original)


if __name__ == "__main__":
    unittest.main()
