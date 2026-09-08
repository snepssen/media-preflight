"""Reducing a timeline, and the three ways it gets drawn."""

import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import chart  # noqa: E402
import profiles  # noqa: E402


def timeline(values, start=0):
    return [{"t": start + index, "short_term": value, "momentary": value,
             "true_peak_dbfs": None, "phase": None}
            for index, value in enumerate(values)]


class ReduceTests(unittest.TestCase):
    def test_a_short_timeline_is_returned_whole(self):
        points = chart.reduce(timeline([-20, -21, -22]), points=900)
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0]["high"], -20)

    def test_a_long_timeline_is_bucketed_to_the_limit(self):
        points = chart.reduce(timeline([-20] * 10000), points=100)
        self.assertLessEqual(len(points), 100)

    def test_a_spike_survives_reduction(self):
        """Averaging would hide this; keeping the extremes is the whole point."""
        values = [-30] * 500
        values[250] = -5
        points = chart.reduce(timeline(values), points=50)
        self.assertAlmostEqual(max(p["high"] for p in points), -5)
        self.assertAlmostEqual(min(p["low"] for p in points), -30)

    def test_a_hole_survives_reduction_too(self):
        values = [-14] * 500
        values[100] = -60
        points = chart.reduce(timeline(values), points=50)
        self.assertAlmostEqual(min(p["low"] for p in points), -60)

    def test_rows_with_nothing_measured_are_dropped(self):
        rows = timeline([-20, -21])
        rows.append({"t": 2, "short_term": None, "momentary": None,
                     "true_peak_dbfs": None, "phase": None})
        self.assertEqual(len(chart.reduce(rows)), 2)

    def test_an_empty_timeline_draws_nothing(self):
        self.assertEqual(chart.reduce([]), [])
        self.assertEqual(chart.reduce(None), [])


class BandTests(unittest.TestCase):
    def test_a_loudness_target_gives_a_band(self):
        band = chart.band_for(profiles.get("ebu_r128"))
        self.assertAlmostEqual(band["min"], -24.0)
        self.assertAlmostEqual(band["max"], -22.0)

    def test_an_rms_target_refuses_to_draw_one(self):
        """ACX states its requirement in RMS; the chart is in LUFS."""
        band = chart.band_for(profiles.get("acx"))
        self.assertIsNone(band.get("min"))
        self.assertIn("not the same measurement", band["absent"])

    def test_a_target_with_no_loudness_rule_says_so(self):
        band = chart.band_for({"id": "x", "label": "X", "rules": [
            {"id": "codec", "metric": "audio_codec", "label": "Codec",
             "one_of": ["mp3"]}]})
        self.assertIn("no loudness band", band["absent"])


class EventTests(unittest.TestCase):
    def test_measurements_and_findings_both_contribute(self):
        events = chart.events(
            {"silences": [{"start": 0.0, "end": 1.0, "detail": "1 s"}],
             "black": [{"start": 5.0, "end": 6.0}]},
            [{"status": "fail", "label": "True peak",
              "intervals": [{"start": 9.0, "end": 10.0, "detail": "-0.2 dBTP"}]},
             {"status": "pass", "label": "Codec", "intervals": []}])
        groups = sorted(e["group"] for e in events)
        self.assertEqual(groups, ["fail", "picture", "quiet"])

    def test_a_passing_finding_marks_nothing(self):
        events = chart.events({}, [{"status": "pass", "label": "X",
                                    "intervals": [{"start": 1, "end": 2}]}])
        self.assertEqual(events, [])


class ChapterTests(unittest.TestCase):
    CHAPTERS = [
        {"start_s": 0.0, "end_s": 10.0, "title": "One"},
        {"start_s": 10.0, "end_s": 20.0, "title": ""},
    ]

    def test_each_chapter_gets_the_loudest_moment_inside_it(self):
        rows = timeline([-30] * 10 + [-12] * 10)
        out = chart.chapters(self.CHAPTERS, rows)
        self.assertAlmostEqual(out[0]["loudest_short_term"], -30)
        self.assertAlmostEqual(out[1]["loudest_short_term"], -12)

    def test_an_untitled_chapter_is_numbered_rather_than_blank(self):
        out = chart.chapters(self.CHAPTERS, timeline([-20] * 20))
        self.assertEqual(out[1]["title"], "Chapter 2")

    def test_a_chapter_with_no_measurements_reports_nothing_not_zero(self):
        out = chart.chapters(self.CHAPTERS, timeline([-20] * 5))
        self.assertIsNone(out[1]["loudest_short_term"])

    def test_no_chapters_is_no_table(self):
        self.assertEqual(chart.chapters(None, timeline([-20])), [])

    def test_the_previous_chapter_does_not_leak_over_the_boundary(self):
        """Short-term loudness looks three seconds back, so the first seconds
        of a quiet chapter still carry the loud one before it."""
        rows = timeline([-8] * 10 + [-40] * 10)
        out = chart.chapters(self.CHAPTERS, rows)
        self.assertAlmostEqual(out[1]["loudest_short_term"], -40,
                               msg="the quiet chapter must read as quiet")

    def test_a_chapter_shorter_than_the_window_keeps_what_it_has(self):
        short = [{"start_s": 0.0, "end_s": 2.0, "title": "Blink"}]
        out = chart.chapters(short, timeline([-20, -20]))
        self.assertAlmostEqual(out[0]["loudest_short_term"], -20,
                               msg="better a smeared number than none at all")


class StripTests(unittest.TestCase):
    def test_a_short_file_still_fills_the_strip(self):
        points = chart.reduce(timeline([-20, -10, -20, -10]))
        lines = chart.strip(points, width=40)
        bar = lines[0].strip()
        self.assertEqual(len(bar), 40, "a stub with blank columns reads as "
                                       "missing data")

    def test_failing_regions_are_marked_under_the_bar(self):
        points = chart.reduce(timeline([-20] * 60))
        lines = chart.strip(points, None,
                            [{"start": 0, "end": 30, "group": "fail",
                              "label": "x"}], width=40)
        self.assertIn("✕", lines[1])

    def test_nothing_measured_draws_nothing(self):
        self.assertEqual(chart.strip([]), [])


class SvgTests(unittest.TestCase):
    def setUp(self):
        self.points = chart.reduce(timeline([-30, -20, -14, -18, -25]))

    def test_it_is_well_formed_and_self_contained(self):
        import xml.etree.ElementTree as tree
        drawing = chart.svg(self.points, duration=5)
        root = tree.fromstring(drawing)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("viewBox", root.attrib)

    def test_exports_are_light_and_the_window_follows_the_theme(self):
        light = chart.svg(self.points, duration=5, theme="light")
        auto = chart.svg(self.points, duration=5, theme="auto")
        self.assertNotIn("prefers-color-scheme", light)
        self.assertIn("prefers-color-scheme", auto)

    def test_events_are_labelled_so_a_finding_can_find_them(self):
        drawing = chart.svg(self.points, duration=5, event_list=[
            {"start": 1, "end": 2, "group": "fail", "label": "True peak",
             "detail": "-0.2 dBTP"}])
        self.assertIn('data-label="True peak"', drawing)
        self.assertIn("<title>True peak", drawing)

    def test_the_reason_for_a_missing_band_is_drawn_on_the_chart(self):
        drawing = chart.svg(self.points, chart.band_for(profiles.get("acx")),
                            duration=5)
        self.assertIn("No target band drawn", drawing)

    def test_a_flat_programme_is_not_drawn_as_a_line_on_the_edge(self):
        flat = chart.reduce(timeline([-23.0] * 20))
        drawing = chart.svg(flat, duration=20)
        self.assertIn("<polyline", drawing)

    def test_nothing_measured_draws_nothing(self):
        self.assertEqual(chart.svg([]), "")

    def test_markup_in_a_title_cannot_escape_into_the_drawing(self):
        drawing = chart.svg(self.points, duration=5,
                            title='</svg><script>alert(1)</script>')
        self.assertNotIn("<script>", drawing)


if __name__ == "__main__":
    unittest.main()
