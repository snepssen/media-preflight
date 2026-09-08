"""The rule engine, against dictionaries rather than files."""

import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import checks  # noqa: E402
import profiles  # noqa: E402


def facts(**overrides):
    base = {
        "path": "/tmp/x.mp3", "name": "x.mp3", "size_bytes": 1000,
        "container": {"format_name": "mp3", "duration_s": 60.0,
                      "bit_rate": 192000.0, "tags": {}},
        "audio": {"codec": "mp3", "sample_rate": 44100, "channels": 1,
                  "bit_rate": 192000.0, "bits_per_sample": 0,
                  "duration_s": 60.0},
        "audio_streams": [], "video": None, "video_streams": [],
        "cover_art": False, "streams": [], "chapters": [],
        "subtitle_streams": [],
    }
    base.update(overrides)
    return base


def measurements(**overrides):
    base = {
        "integrated_lufs": -20.0, "loudness_range_lu": 5.0,
        "true_peak_dbfs": -4.0, "peak_dbfs": -4.2, "rms_dbfs": -20.5,
        "noise_floor_dbfs": -70.0, "channels": [{"channel": 1,
                                                 "dc_offset": 0.0001,
                                                 "rms_dbfs": -20.5}],
        "channel_rms_spread_db": 0.0, "silent_channels": [],
        "phase_min": None, "lead_silence_s": 0.7, "tail_silence_s": 2.0,
        "mid_silences": [], "silences": [], "ends_abruptly": False,
        "clipping_seconds": 0.0, "duration_s": 60.0, "timeline": [],
        "settings": dict(),
    }
    base.update(overrides)
    return base


class JudgeTests(unittest.TestCase):
    def test_a_band_fails_on_both_sides(self):
        rule = {"min": -23.0, "max": -18.0, "severity": "fail"}
        self.assertEqual(checks.judge(rule, -24.4)[0], "fail")
        self.assertEqual(checks.judge(rule, -17.0)[0], "fail")
        self.assertEqual(checks.judge(rule, -20.0)[0], "pass")

    def test_the_inner_band_warns_without_failing(self):
        rule = {"max": -1.0, "warn_max": -2.0, "severity": "fail"}
        self.assertEqual(checks.judge(rule, -1.5)[0], "warn")
        self.assertEqual(checks.judge(rule, -0.5)[0], "fail")
        self.assertEqual(checks.judge(rule, -3.0)[0], "pass")

    def test_enumerations_compare_as_text_when_they_have_to(self):
        rule = {"one_of": [44100, 48000], "severity": "fail"}
        self.assertEqual(checks.judge(rule, 44100)[0], "pass")
        self.assertEqual(checks.judge(rule, "44100")[0], "pass")
        self.assertEqual(checks.judge(rule, 96000)[0], "fail")

    def test_a_flag_rule_reads_the_flag_not_a_number(self):
        self.assertEqual(checks.judge({"forbid": True, "severity": "warn"},
                                      True)[0], "warn")
        self.assertEqual(checks.judge({"forbid": True, "severity": "warn"},
                                      False)[0], "pass")

    def test_severity_is_the_rule_s_to_choose(self):
        rule = {"max": -1.0, "severity": "warn"}
        self.assertEqual(checks.judge(rule, 0.0)[0], "warn")


class CheckTests(unittest.TestCase):
    def test_a_metric_the_file_cannot_answer_is_skipped_not_failed(self):
        rule = {"id": "fps", "metric": "av_duration_gap_s", "label": "A/V",
                "max": 0.5}
        finding = checks.check(rule, facts(), measurements())
        self.assertEqual(finding["status"], "skip")
        self.assertIn("Not present", finding["detail"])

    def test_an_unknown_metric_says_so_rather_than_passing(self):
        rule = {"id": "x", "metric": "nonsense", "label": "X", "max": 1}
        finding = checks.check(rule, facts(), measurements())
        self.assertEqual(finding["status"], "skip")
        self.assertIn("does not know", finding["detail"])

    def test_declared_metrics_are_marked_as_having_no_moment(self):
        rule = {"id": "sample_rate", "metric": "sample_rate", "label": "Rate",
                "one_of": [48000], "severity": "fail"}
        finding = checks.check(rule, facts(), measurements())
        self.assertEqual(finding["scope"], "declared")
        self.assertEqual(finding["timestamps"], [])

    def test_loudness_failures_are_located_from_the_timeline(self):
        timeline = [{"t": t, "short_term": s, "momentary": s,
                     "true_peak_dbfs": None, "phase": None}
                    for t, s in enumerate([-20, -20, -5, -4, -5, -20])]
        rule = {"id": "integrated", "metric": "integrated_lufs",
                "label": "Loudness", "min": -24.0, "max": -12.0,
                "severity": "fail"}
        finding = checks.check(
            rule, facts(),
            measurements(integrated_lufs=-8.0, timeline=timeline))
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(finding["timestamps"], [2.0])
        self.assertEqual(finding["intervals"][0]["end"], 5.0)

    def test_rms_failures_carry_no_invented_timestamps(self):
        rule = {"id": "rms", "metric": "rms_dbfs", "label": "RMS",
                "min": -23.0, "max": -18.0, "severity": "fail"}
        finding = checks.check(rule, facts(), measurements(rms_dbfs=-40.0))
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(finding["timestamps"], [])
        self.assertEqual(finding["scope"], "file")

    def test_sample_peak_failures_ask_the_locator_once(self):
        calls = []

        def locator(threshold):
            calls.append(threshold)
            return [{"start": 12.0, "end": 13.0, "worst": -0.5,
                     "detail": "peak -0.50 dBFS"}]

        m = measurements(peak_dbfs=-0.5)
        rules = [{"id": "peak", "metric": "peak_dbfs", "label": "Peak",
                  "max": -3.0, "severity": "fail"},
                 {"id": "clipping", "metric": "clipping_seconds",
                  "label": "Clipping", "max": 0.0, "severity": "warn"}]
        first = checks.check(rules[0], facts(), m, locator)
        m["clipping_seconds"] = 1.0
        second = checks.check(rules[1], facts(), m, locator)
        self.assertEqual(first["timestamps"], [12.0])
        self.assertEqual(second["timestamps"], [12.0])
        self.assertEqual(calls, [-3.0], "the second rule reuses the windows")


class EvaluateTests(unittest.TestCase):
    def test_one_failure_makes_the_whole_verdict_a_failure(self):
        profile = profiles.get("acx")
        result = checks.evaluate(facts(), measurements(rms_dbfs=-40.0), profile)
        self.assertEqual(result["verdict"], "fail")
        self.assertGreaterEqual(result["counts"]["fail"], 1)

    def test_a_clean_file_against_a_real_profile_passes(self):
        profile = profiles.get("acx")
        result = checks.evaluate(facts(), measurements(), profile)
        self.assertEqual(result["verdict"], "pass", [
            (f["label"], f["actual"], f["required"]) for f in result["findings"]
            if f["status"] not in ("pass", "skip")])


class WordingTests(unittest.TestCase):
    def test_requirements_read_as_english(self):
        self.assertEqual(checks.describe({"unit": "dBTP", "max": -3.0}),
                         "≤ -3 dBTP")
        self.assertEqual(checks.describe({"unit": "s", "min": 1.0, "max": 5.0}),
                         "1 to 5 s")
        self.assertEqual(checks.describe({"one_of": ["mp3", "aac"]}),
                         "mp3 or aac")
        self.assertEqual(checks.describe({"forbid": True}), "none")

    def test_timecodes_grow_an_hour_field_only_when_needed(self):
        self.assertEqual(checks.timecode(222), "03:42")
        self.assertEqual(checks.timecode(3910), "1:05:10")

    def test_digital_silence_is_a_word_not_a_number(self):
        self.assertEqual(checks.format_value(float("-inf"), "dBFS"), "silent")


if __name__ == "__main__":
    unittest.main()
