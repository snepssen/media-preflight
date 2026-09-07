"""Targets as data, and the three ways a report is written."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import checks  # noqa: E402
import probe  # noqa: E402
import profiles  # noqa: E402
import report  # noqa: E402
from test_checks import facts, measurements  # noqa: E402


class ProfileTests(unittest.TestCase):
    def test_every_built_in_profile_is_valid_by_its_own_rules(self):
        for profile in profiles.BUILT_IN:
            profiles.validate(profile)

    def test_every_rule_names_a_metric_the_engine_can_answer(self):
        for profile in profiles.all_profiles():
            for rule in profile["rules"]:
                self.assertIn(rule["metric"], checks.METRICS,
                              f"{profile['id']}/{rule['id']}")

    def test_every_named_fix_has_a_builder(self):
        for profile in profiles.all_profiles():
            for rule in profile["rules"]:
                if rule.get("fix"):
                    self.assertIn(rule["fix"], profiles_fix_names(),
                                  f"{profile['id']}/{rule['id']}")

    def test_every_profile_says_where_its_numbers_came_from(self):
        for profile in profiles.all_profiles():
            self.assertTrue(profile.get("source"), profile["id"])
            self.assertIn(profile.get("confidence"), ("published", "informal"))

    def test_universal_rules_are_added_but_never_shadow_the_target(self):
        acx = profiles.get("acx")
        ids = [rule["id"] for rule in acx["rules"]]
        self.assertIn("clipping", ids)
        self.assertEqual(len(ids), len(set(ids)), "no rule appears twice")

    def test_an_unknown_target_names_the_ones_that_exist(self):
        with self.assertRaises(ValueError) as caught:
            profiles.get("netflix")
        self.assertIn("acx", str(caught.exception))


def profiles_fix_names():
    import corrections
    return set(corrections._BUILDERS)


class CustomProfileTests(unittest.TestCase):
    GOOD = {
        "id": "house", "label": "House standard", "summary": "Ours.",
        "source": "internal", "checked": "2026-09", "confidence": "informal",
        "rules": [{"id": "integrated", "metric": "integrated_lufs",
                   "label": "Loudness", "unit": "LUFS", "min": -17.0,
                   "max": -13.0, "severity": "fail", "fix": "loudnorm"}],
    }

    def test_a_custom_profile_loads_and_gains_the_universal_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "house.json")
            Path(path).write_text(json.dumps(self.GOOD), encoding="utf-8")
            profile = profiles.get(path)
            self.assertEqual(profile["id"], "house")
            self.assertIn("clipping", [r["id"] for r in profile["rules"]])

    def test_a_rule_that_states_no_requirement_is_rejected_with_a_sentence(self):
        broken = json.loads(json.dumps(self.GOOD))
        broken["rules"][0] = {"id": "x", "metric": "integrated_lufs",
                              "label": "X"}
        with self.assertRaises(ValueError) as caught:
            profiles.validate(broken)
        self.assertIn("states no requirement", str(caught.exception))

    def test_a_missing_field_names_the_field(self):
        broken = json.loads(json.dumps(self.GOOD))
        del broken["label"]
        with self.assertRaises(ValueError) as caught:
            profiles.validate(broken)
        self.assertIn("label", str(caught.exception))

    def test_a_round_trip_through_json_keeps_the_target_s_own_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "out.json")
            profiles.save(profiles.get("acx"), path)
            back = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual([r["id"] for r in back["rules"]],
                             [r["id"] for r in profiles.ACX["rules"]])


def envelope(target="acx", **overrides):
    profile = profiles.get(target)
    f, m = facts(), measurements(**overrides)
    result = checks.evaluate(f, m, profile)
    return report.envelope(f, m, result, profile)


class ReportTests(unittest.TestCase):
    def test_the_text_report_states_measurement_and_requirement(self):
        text = report.text(envelope(rms_dbfs=-40.0))
        self.assertIn("RMS level: -40 dBFS", text)
        self.assertIn("Required: -23 to -18 dBFS", text)
        self.assertIn("Not ready", text)

    def test_timestamps_are_collected_into_one_line(self):
        timeline = [{"t": t, "short_term": s, "momentary": s,
                     "true_peak_dbfs": None, "phase": None}
                    for t, s in enumerate([-20] * 222 + [-2, -2] + [-20] * 10)]
        text = report.text(envelope("ebu_r128", integrated_lufs=-2.0,
                                    timeline=timeline))
        line = next(l for l in text.splitlines()
                    if l.startswith("Problems occur at"))
        self.assertIn("03:42", line)

    def test_json_survives_digital_silence(self):
        data = report.data(envelope(noise_floor_dbfs=float("-inf")))
        parsed = json.loads(data)          # would raise on -Infinity
        self.assertEqual(parsed["measurements"]["noise_floor_dbfs"], "-inf")

    def test_the_client_report_carries_its_own_provenance(self):
        text = report.markdown(envelope(rms_dbfs=-40.0))
        self.assertIn("ACX audio submission requirements", text)
        self.assertIn("ITU-R BS.1770", text)
        self.assertIn("| ✕ | RMS level", text)

    def test_an_informal_target_says_so_in_writing(self):
        profile = profiles.get("social_vertical")
        f, m = facts(), measurements()
        result = checks.evaluate(f, m, profile)
        text = report.markdown(report.envelope(f, m, result, profile))
        self.assertIn("not** a published specification", text)


class ProbeTests(unittest.TestCase):
    RAW = {
        "format": {"format_name": "mp3", "duration": "60.0",
                   "bit_rate": "192000", "size": "1440000",
                   "tags": {"TITLE": "Chapter One"}},
        "streams": [
            {"index": 0, "codec_type": "audio", "codec_name": "mp3",
             "sample_rate": "44100", "channels": 1, "duration": "60.0",
             "bit_rate": "192000", "disposition": {}},
            {"index": 1, "codec_type": "video", "codec_name": "mjpeg",
             "width": 600, "height": 600, "avg_frame_rate": "0/0",
             "disposition": {"attached_pic": 1}},
        ],
    }

    def test_cover_art_is_not_a_video_track(self):
        f = probe.normalise(self.RAW, "/tmp/x.mp3")
        self.assertTrue(f["cover_art"])
        self.assertIsNone(f["video"])
        self.assertEqual(f["video_streams"], [])

    def test_tag_keys_are_matched_without_caring_about_case(self):
        f = probe.normalise(self.RAW, "/tmp/x.mp3")
        self.assertEqual(f["container"]["tags"]["title"], "Chapter One")

    def test_an_unknown_frame_rate_is_none_rather_than_zero(self):
        f = probe.normalise({"format": {}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264",
             "avg_frame_rate": "0/0", "r_frame_rate": "30000/1001",
             "disposition": {}}]}, "/tmp/x.mp4")
        self.assertIsNone(f["video"]["avg_frame_rate"])
        self.assertAlmostEqual(f["video"]["r_frame_rate"], 29.97, places=2)

    def test_constant_and_variable_bitrate_are_told_apart_by_packet_size(self):
        self.assertEqual(probe.classify_packet_sizes([417] * 30), "cbr")
        self.assertEqual(probe.classify_packet_sizes([417, 418] * 15), "cbr")
        self.assertEqual(
            probe.classify_packet_sizes([200, 480, 310, 522] * 8), "vbr")
        self.assertEqual(probe.classify_packet_sizes([417] * 3), "unknown")


if __name__ == "__main__":
    unittest.main()
