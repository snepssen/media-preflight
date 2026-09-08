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


class ProvenanceTests(unittest.TestCase):
    """Where a number came from is part of what the number means."""

    def test_a_basis_is_one_of_the_three_that_mean_something(self):
        for profile in profiles.all_profiles():
            for rule in profile["rules"] + profile.get("set_rules", []):
                basis = rule.get("basis")
                if basis is not None:
                    self.assertIn(basis, ("published", "observed", "house"),
                                  f"{profile['id']}/{rule['id']}")

    def test_every_universal_rule_says_it_is_this_tools_own(self):
        """They get attached to published profiles, where inheriting the
        profile's confidence would claim a provenance they do not have."""
        for rule in profiles.UNIVERSAL + profiles.UNIVERSAL_SET:
            self.assertEqual(rule.get("basis"), "house", rule["id"])

    def test_a_published_profile_marks_the_rules_that_are_not(self):
        youtube = profiles.get("youtube")
        loudness = next(r for r in youtube["rules"] if r["id"] == "integrated")
        self.assertEqual(loudness["basis"], "observed",
                         "YouTube publishes no loudness figure")
        codec = next(r for r in youtube["rules"] if r["id"] == "codec")
        self.assertEqual(codec["basis"], "published")

    def test_an_unpublished_threshold_says_so_before_its_note(self):
        profile = profiles.get("youtube")
        f, m = facts(), measurements(integrated_lufs=-40.0)
        result = checks.evaluate(f, m, profile)
        text = report.text(report.envelope(f, m, result, profile))
        self.assertIn("measured behaviour, not a published figure", text)

    def test_the_validator_rejects_a_basis_it_does_not_understand(self):
        with self.assertRaises(ValueError) as caught:
            profiles.validate({"id": "x", "label": "X", "rules": [
                {"id": "a", "metric": "rms_dbfs", "label": "A", "max": 1.0,
                 "basis": "vibes"}]})
        self.assertIn("basis", str(caught.exception))


class AuditedThresholdTests(unittest.TestCase):
    """The numbers, as read from the source documents in September 2026.

    These are not testing arithmetic; they are pinning values somebody checked
    against a specification, so that changing one is a deliberate act with a
    failing test attached rather than a quiet edit.
    """

    def _rule(self, target, rule_id):
        return next(r for r in profiles.get(target)["rules"]
                    if r["id"] == rule_id)

    def test_acx_room_tone_is_one_to_five_seconds_at_both_ends(self):
        # "We recommend between 1 and 5 seconds of room tone at the beginning
        # and end of each file." — help.acx.com. The 0.5-to-1-second opening
        # repeated widely elsewhere appears on no ACX page.
        for rule_id in ("head_room_tone", "tail_room_tone"):
            rule = self._rule("acx", rule_id)
            self.assertEqual((rule["min"], rule["max"]), (1.0, 5.0))
            self.assertEqual(rule["severity"], "warn",
                             "the page says 'recommend'")

    def test_acx_levels_are_as_published(self):
        self.assertEqual((self._rule("acx", "rms")["min"],
                          self._rule("acx", "rms")["max"]), (-23.0, -18.0))
        self.assertEqual(self._rule("acx", "peak")["max"], -3.0)
        self.assertEqual(self._rule("acx", "noise_floor")["max"], -60.0)
        self.assertEqual(self._rule("acx", "duration")["max"], 120.0)

    def test_ebu_r128_keeps_both_of_its_tolerances(self):
        rule = self._rule("ebu_r128", "integrated")
        self.assertEqual((rule["warn_min"], rule["warn_max"]), (-23.5, -22.5),
                         "the ±0.5 LU normal tolerance")
        self.assertEqual((rule["min"], rule["max"]), (-24.0, -22.0),
                         "the ±1.0 LU permitted for live programmes")
        self.assertEqual(self._rule("ebu_r128", "true_peak")["max"], -1.0)

    def test_spotify_warns_between_the_two_peak_figures(self):
        rule = self._rule("spotify_podcast", "true_peak")
        self.assertEqual(rule["max"], -1.0)
        self.assertEqual(rule["warn_max"], -2.0,
                         "-2 dBTP is asked for above -14 LUFS")

    def test_spotify_does_not_claim_a_podcast_specification(self):
        profile = profiles.get("spotify_podcast")
        self.assertEqual(profile["confidence"], "informal")
        self.assertIn("music", profile["source"])

    def test_youtube_requires_deinterlacing_rather_than_suggesting_it(self):
        rule = self._rule("youtube", "interlaced")
        self.assertEqual(rule["severity"], "fail")
        self.assertEqual(rule["basis"], "published")

    def test_youtube_accepts_the_codecs_its_guide_names(self):
        self.assertEqual(sorted(self._rule("youtube", "codec")["one_of"]),
                         ["aac", "opus"])

    def test_every_published_target_records_when_it_was_read(self):
        for profile in profiles.all_profiles():
            if profile.get("confidence") == "published":
                self.assertRegex(profile.get("checked", ""), r"^\d{4}-\d{2}$",
                                 profile["id"])


class FastStartTests(unittest.TestCase):
    """The index in front of the media, which costs a few seeks to check."""

    def _mp4(self, folder, faststart):
        import subprocess
        import platform_support
        ffmpeg = platform_support.find_ffmpeg()
        if not ffmpeg:
            self.skipTest("ffmpeg is not installed")
        path = os.path.join(folder, f"{'fast' if faststart else 'slow'}.mp4")
        command = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                   "testsrc2=size=160x120:rate=25:d=1", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p"]
        if faststart:
            command += ["-movflags", "+faststart"]
        subprocess.run(command + [path], check=True)
        return path

    def test_the_atom_order_is_read_without_decoding(self):
        with tempfile.TemporaryDirectory() as folder:
            order = probe.atom_order(self._mp4(folder, False))
            self.assertIn("moov", order)
            self.assertIn("mdat", order)

    def test_faststart_puts_the_index_first(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(probe.fast_start(self._mp4(folder, True), "mp4"))
            self.assertFalse(probe.fast_start(self._mp4(folder, False), "mp4"))

    def test_a_file_with_no_such_structure_is_not_asked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "notes.wav")
            Path(path).write_bytes(b"RIFF....WAVEfmt ")
            self.assertIsNone(probe.fast_start(path, "wav"))

    def test_a_truncated_file_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "cut.mp4")
            Path(path).write_bytes(b"\x00\x00\x00\x18ftypisom")
            self.assertIsNone(probe.fast_start(path, "mp4"))

    def test_youtube_asks_for_it_by_name(self):
        rule = next(r for r in profiles.get("youtube")["rules"]
                    if r["id"] == "fast_start")
        self.assertEqual(rule["basis"], "published")
        self.assertTrue(rule["require"])


class BeforeAndAfterTests(unittest.TestCase):
    def test_the_chart_can_carry_an_earlier_report(self):
        before = envelope("ebu_r128", integrated_lufs=-40.0, timeline=[
            {"t": t, "short_term": -40.0, "momentary": -40.0,
             "true_peak_dbfs": None, "phase": None} for t in range(30)])
        after = envelope("ebu_r128", integrated_lufs=-23.0, timeline=[
            {"t": t, "short_term": -23.0, "momentary": -23.0,
             "true_peak_dbfs": None, "phase": None} for t in range(30)])
        drawing = report.chart_svg(after, baseline=before)
        self.assertIn('class="before"', drawing)
        self.assertNotIn('class="before"', report.chart_svg(after))
