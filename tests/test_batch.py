"""Checking a delivery: discovery, ordering, and the faults only a set has."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import batch  # noqa: E402
import checks  # noqa: E402
import profiles  # noqa: E402
import report  # noqa: E402


def entry(name, channels=1, sample_rate=44100, codec="mp3", rms=-20.0,
          duration=600.0, verdict="pass", peak=-6.0, mode="cbr"):
    """A stand-in for one checked file, shaped like batch.run produces."""
    return {
        "name": name,
        "path": "/tmp/" + name,
        "facts": {
            "audio": {"channels": channels, "sample_rate": sample_rate,
                      "codec": codec, "bits_per_sample": 0},
            "container": {"format_name": "mp3", "duration_s": duration},
        },
        "measurements": {"rms_dbfs": rms, "peak_dbfs": peak,
                         "integrated_lufs": rms + 3.0,
                         "duration_s": duration, "bitrate_mode": mode},
        "result": {"verdict": verdict, "findings": []},
        "envelope": {"verdict": verdict,
                     "counts": {"fail": 0, "warn": 0, "pass": 1, "skip": 0},
                     "file": {"duration_s": duration, "audio": {}},
                     "measurements": {"rms_dbfs": rms},
                     "findings": []},
    }


class OrderTests(unittest.TestCase):
    def test_chapter_two_comes_before_chapter_ten(self):
        names = ["Chapter 10.mp3", "Chapter 2.mp3", "Chapter 1.mp3"]
        ordered = sorted(names, key=batch.natural_key)
        self.assertEqual(ordered,
                         ["Chapter 1.mp3", "Chapter 2.mp3", "Chapter 10.mp3"])

    def test_ordering_ignores_case(self):
        ordered = sorted(["b.wav", "A.wav"], key=batch.natural_key)
        self.assertEqual(ordered, ["A.wav", "b.wav"])


class CollectTests(unittest.TestCase):
    def _folder(self, names):
        folder = tempfile.mkdtemp()
        for name in names:
            Path(os.path.join(folder, name)).write_bytes(b"x")
        return folder

    def test_a_folder_becomes_its_media_files_in_order(self):
        folder = self._folder(["b.wav", "a.wav", "notes.txt", "art.png"])
        found = [os.path.basename(p) for p in batch.collect([folder])]
        self.assertEqual(found, ["a.wav", "b.wav"])

    def test_the_tools_own_output_is_never_swept_back_in(self):
        """A folder checked twice would otherwise check its own corrected
        copies, and a corrected copy of a corrected copy is nobody's delivery."""
        folder = self._folder(["a.wav", "a.preflight.mp3"])
        found = [os.path.basename(p) for p in batch.collect([folder])]
        self.assertEqual(found, ["a.wav"])

    def test_hidden_files_and_folders_are_left_alone(self):
        folder = self._folder([".DS_Store", "a.wav"])
        self.assertEqual(len(batch.collect([folder])), 1)

    def test_sub_folders_need_asking_for(self):
        folder = self._folder(["a.wav"])
        inner = os.path.join(folder, "extras")
        os.makedirs(inner)
        Path(os.path.join(inner, "b.wav")).write_bytes(b"x")
        self.assertEqual(len(batch.collect([folder])), 1)
        self.assertEqual(len(batch.collect([folder], recursive=True)), 2)

    def test_a_file_listed_twice_is_checked_once(self):
        folder = self._folder(["a.wav"])
        path = os.path.join(folder, "a.wav")
        self.assertEqual(len(batch.collect([path, path, folder])), 1)

    def test_an_empty_folder_says_what_it_looked_for(self):
        folder = self._folder(["notes.txt"])
        with self.assertRaises(batch.BatchError) as caught:
            batch.collect([folder])
        self.assertIn("Nothing to check", str(caught.exception))

    def test_a_path_that_is_not_there_names_itself(self):
        with self.assertRaises(batch.BatchError) as caught:
            batch.collect(["/no/such/folder"])
        self.assertIn("/no/such/folder", str(caught.exception))


class SetMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.profile = profiles.get("acx")
        self.files = [entry("chapter-01.mp3"), entry("chapter-02.mp3"),
                      entry("chapter-03.mp3"),
                      entry("chapter-09.mp3", channels=2)]

    def test_the_odd_file_out_is_named(self):
        m = batch.measure_set(self.files, self.profile)
        self.assertEqual(m["channels_distinct"], 2)
        self.assertEqual(m["channels_odd"], ["chapter-09.mp3"])

    def test_an_even_split_names_everyone_rather_than_picking_a_side(self):
        files = [entry("a.mp3"), entry("b.mp3", channels=2)]
        m = batch.measure_set(files, self.profile)
        self.assertEqual(m["channels_odd"], ["a.mp3", "b.mp3"])

    def test_a_set_that_agrees_has_no_odd_file(self):
        m = batch.measure_set(self.files[:3], self.profile)
        self.assertEqual(m["channels_distinct"], 1)
        self.assertEqual(m["channels_odd"], [])

    def test_loudness_is_compared_in_the_unit_the_target_states(self):
        """ACX is written in RMS; comparing its files in LUFS would be the
        same invention the per-file report refuses."""
        m = batch.measure_set(self.files, profiles.get("acx"))
        self.assertEqual(m["loudness"]["metric"], "rms_dbfs")
        lufs = batch.measure_set(self.files, profiles.get("ebu_r128"))
        self.assertEqual(lufs["loudness"].get("metric"), "integrated_lufs")

    def test_a_target_with_no_loudness_rule_compares_nothing(self):
        bare = {"id": "x", "label": "X", "rules": [
            {"id": "codec", "metric": "audio_codec", "label": "Codec",
             "one_of": ["mp3"]}]}
        m = batch.measure_set(self.files, bare)
        self.assertIn("absent", m["loudness"])

    def test_one_file_above_the_rest_is_the_outlier_not_the_two_ends(self):
        files = [entry(f"chapter-0{i}.mp3", rms=-22.5) for i in range(1, 5)]
        files.append(entry("chapter-05.mp3", rms=-18.5))
        m = batch.measure_set(files, self.profile)
        self.assertAlmostEqual(m["loudness"]["spread"], 4.0)
        self.assertEqual(m["loudness"]["outliers"], ["chapter-05.mp3"])

    def test_a_smooth_ramp_has_no_outlier_so_the_ends_are_named(self):
        files = [entry(f"f{i}.mp3", rms=-24 + i) for i in range(5)]
        m = batch.measure_set(files, self.profile)
        self.assertEqual(sorted(m["loudness"]["outliers"]),
                         ["f0.mp3", "f4.mp3"])

    def test_one_file_is_not_a_spread(self):
        m = batch.measure_set([entry("only.mp3")], self.profile)
        self.assertIn("absent", m["loudness"])


class SetRuleTests(unittest.TestCase):
    def test_a_title_of_files_that_each_pass_can_still_be_rejected(self):
        """The whole reason this exists: every file valid, the title not."""
        profile = profiles.get("acx")
        files = [entry("chapter-01.mp3"), entry("chapter-02.mp3"),
                 entry("chapter-09.mp3", channels=2)]
        self.assertTrue(all(f["result"]["verdict"] == "pass" for f in files))
        result = checks.evaluate_set(batch.measure_set(files, profile), profile)
        self.assertEqual(result["verdict"], "fail")
        channels = next(f for f in result["findings"] if f["id"] == "channels")
        self.assertEqual(channels["files"], ["chapter-09.mp3"])

    def test_the_finding_says_which_values_disagree(self):
        profile = profiles.get("acx")
        files = [entry("a.mp3", sample_rate=44100),
                 entry("b.mp3", sample_rate=44100),
                 entry("c.mp3", sample_rate=48000)]
        result = checks.evaluate_set(batch.measure_set(files, profile), profile)
        rate = next(f for f in result["findings"] if f["id"] == "sample_rate")
        self.assertIn("44100", rate["actual"])
        self.assertIn("48000", rate["actual"])

    def test_a_target_that_states_its_own_rule_is_not_checked_twice(self):
        profile = profiles.get("acx")
        metrics = [rule["metric"] for rule in profile["set_rules"]]
        self.assertEqual(len(metrics), len(set(metrics)))

    def test_every_profile_names_set_metrics_the_engine_knows(self):
        for profile in profiles.all_profiles():
            for rule in profile.get("set_rules", []):
                self.assertIn(rule["metric"], checks.SET_METRICS,
                              f"{profile['id']}/{rule['id']}")


class SetReportTests(unittest.TestCase):
    def _envelope(self):
        profile = profiles.get("acx")
        files = [entry("chapter-01.mp3"), entry("chapter-02.mp3"),
                 entry("chapter-09.mp3", channels=2)]
        measurements = batch.measure_set(files, profile)
        return report.set_envelope({
            "profile": profile, "files": files, "unreadable": [],
            "set_measurements": measurements,
            "set_result": checks.evaluate_set(measurements, profile),
            "verdict": "fail"})

    def test_the_text_report_names_the_file_and_the_requirement(self):
        text = report.set_text(self._envelope())
        self.assertIn("Channel count across the title", text)
        self.assertIn("chapter-09.mp3", text)
        self.assertIn("Required: ≤ 1", text)

    def test_the_json_is_valid_and_does_not_repeat_every_file_report(self):
        import json
        envelope = self._envelope()
        parsed = json.loads(report.data(envelope))
        self.assertEqual(len(parsed["files"]), 3)
        self.assertNotIn("findings", parsed["files"][0])

    def test_a_file_reports_the_loudness_the_target_states(self):
        text = report.set_text(self._envelope())
        self.assertIn("dBFS", text)
        self.assertNotIn("LUFS", text)

    def test_the_markdown_lists_the_files_and_the_delivery_separately(self):
        markdown = report.set_markdown(self._envelope())
        self.assertIn("## Across the delivery", markdown)
        self.assertIn("## Files", markdown)
        self.assertIn("`chapter-09.mp3`", markdown)


if __name__ == "__main__":
    unittest.main()


class ConsensusTests(unittest.TestCase):
    """What a delivery decides its files should agree on."""

    def test_the_majority_decides_the_format(self):
        files = [entry("a.mp3"), entry("b.mp3"), entry("c.mp3", channels=2)]
        agreed = batch.consensus(
            _result(files, profiles.get("acx")), profiles.get("acx"))
        self.assertEqual(agreed["channels"], 1)

    def test_an_even_split_changes_nothing_and_says_why(self):
        files = [entry("a.mp3"), entry("b.mp3", channels=2)]
        agreed = batch.consensus(
            _result(files, profiles.get("acx")), profiles.get("acx"))
        self.assertIsNone(agreed["channels"])
        self.assertTrue(any("evenly split" in r for r in agreed["reasons"]))

    def test_the_level_comes_from_the_target_not_from_the_files(self):
        """Bringing four quiet chapters up to meet a loud fifth would satisfy
        the set rule by making every file wrong."""
        files = [entry(f"c{i}.mp3", rms=-30.0) for i in range(4)]
        files.append(entry("loud.mp3", rms=-19.0))
        agreed = batch.consensus(
            _result(files, profiles.get("acx")), profiles.get("acx"))
        self.assertEqual(agreed["loudness_metric"], "rms_dbfs")
        self.assertAlmostEqual(agreed["loudness_target"], -20.5,
                               msg="the middle of ACX's band, not the median")

    def test_a_target_with_no_loudness_rule_names_no_level(self):
        bare = profiles.with_universal(
            {"id": "x", "label": "X", "rules": [
                {"id": "codec", "metric": "audio_codec", "label": "Codec",
                 "one_of": ["mp3"]}]})
        agreed = batch.consensus(_result([entry("a.mp3")], bare), bare)
        self.assertIsNone(agreed["loudness_target"])


def _result(files, profile):
    measurements = batch.measure_set(files, profile)
    return {"profile": profile, "files": files, "unreadable": [],
            "set_measurements": measurements,
            "set_result": checks.evaluate_set(measurements, profile),
            "verdict": "fail"}


class DeliveryPlanTests(unittest.TestCase):
    def _planned(self):
        profile = profiles.get("acx")
        files = [entry("chapter-01.mp3", rms=-22.5),
                 entry("chapter-02.mp3", rms=-22.5),
                 entry("chapter-03.mp3", rms=-18.5),
                 entry("chapter-09.mp3", rms=-22.5, channels=2)]
        return batch.plan(_result(files, profile), profile)

    def test_a_file_breaking_no_rule_of_its_own_is_still_corrected(self):
        """Every one of these passes ACX individually; the set does not."""
        planned = self._planned()
        names = [f["name"] for f in planned["files"]]
        self.assertIn("chapter-01.mp3", names)
        first = next(f for f in planned["files"]
                     if f["name"] == "chapter-01.mp3")
        self.assertTrue(first["because_of_the_set"])

    def test_the_odd_file_is_converted_to_the_majority(self):
        entry_09 = next(f for f in self._planned()["files"]
                        if f["name"] == "chapter-09.mp3")
        encode = next(s for s in entry_09["steps"] if s["id"] == "encode")
        self.assertIn("-ac", encode["args"])
        self.assertEqual(encode["args"][encode["args"].index("-ac") + 1], "1")

    def test_the_loud_file_is_brought_down_and_the_quiet_ones_up(self):
        planned = self._planned()
        gains = {}
        for entry_file in planned["files"]:
            step = next((s for s in entry_file["steps"] if s["id"] == "gain"),
                        None)
            if step:
                gains[entry_file["name"]] = float(
                    step["filters"][0].split("=")[1].rstrip("dB"))
        self.assertLess(gains["chapter-03.mp3"], 0, "the loud one comes down")
        self.assertGreater(gains["chapter-01.mp3"], 0, "the quiet ones go up")

    def test_a_file_already_at_the_target_is_left_alone(self):
        profile = profiles.get("acx")
        files = [entry(f"c{i}.mp3", rms=-20.5) for i in range(3)]
        planned = batch.plan(_result(files, profile), profile)
        self.assertEqual(planned["files"], [])
        self.assertEqual(len(planned["untouched"]), 3)

    def test_every_step_carries_a_sentence_before_anything_runs(self):
        for entry_file in self._planned()["files"]:
            for step in entry_file["steps"]:
                self.assertTrue(step["description"].strip(), step["id"])


class NumberingTests(unittest.TestCase):
    """A missing chapter is visible from the filenames alone."""

    def test_a_gap_in_the_middle_is_found_and_named(self):
        out = batch.numbering(["chapter-01.mp3", "chapter-02.mp3",
                               "chapter-04.mp3", "chapter-05.mp3"])
        self.assertEqual(out["missing"], ["chapter-03.mp3"])
        self.assertEqual((out["first"], out["last"]), (1, 5))

    def test_a_complete_sequence_is_missing_nothing(self):
        out = batch.numbering([f"chapter-{n:02d}.mp3" for n in range(1, 6)])
        self.assertEqual(out["missing"], [])

    def test_a_sequence_that_starts_at_four_is_not_missing_one_to_three(self):
        """Where a delivery starts is its business; gaps inside it are not."""
        out = batch.numbering(["part-04.wav", "part-05.wav", "part-06.wav"])
        self.assertEqual(out["missing"], [])

    def test_names_that_are_not_a_sequence_make_no_claim(self):
        out = batch.numbering(["intro.wav", "outro.wav", "bed.wav"])
        self.assertIsNone(out["sequence"])
        self.assertEqual(out["missing"], [])

    def test_two_files_are_not_enough_to_be_a_sequence(self):
        self.assertEqual(
            batch.numbering(["a-01.wav", "a-03.wav"])["missing"], [])

    def test_padding_width_is_part_of_the_scheme(self):
        """`part2` and `part02` are two naming schemes, and a delivery that
        mixes them has a different problem from a missing file."""
        out = batch.numbering(["a1.wav", "a2.wav", "a10.wav"])
        self.assertEqual(out["missing"], [], "no invented gap from 3 to 9")

    def test_the_largest_consistent_group_is_the_sequence(self):
        out = batch.numbering(["chapter-01.mp3", "chapter-02.mp3",
                               "chapter-04.mp3", "bonus.mp3", "intro.mp3"])
        self.assertEqual(out["missing"], ["chapter-03.mp3"])

    def test_the_finding_says_what_is_missing_rather_than_how_many(self):
        profile = profiles.get("acx")
        files = [entry("chapter-01.mp3"), entry("chapter-02.mp3"),
                 entry("chapter-04.mp3")]
        measurements = batch.measure_set(files, profile)
        result = checks.evaluate_set(measurements, profile)
        finding = next(f for f in result["findings"]
                       if f["id"] == "set_numbering")
        self.assertEqual(finding["status"], "warn")
        self.assertIn("chapter-", finding["actual"])
        self.assertEqual(finding["files"], ["chapter-03.mp3"])


class DepthAcrossADelivery(unittest.TestCase):
    """One delivery is measured one way, decided once rather than per file."""

    def test_run_passes_the_depth_to_every_file(self):
        seen = []

        def spy(path, profile, ffmpeg=None, ffprobe=None, progress=None,
                depth="selective", width_divide=None, **rest):
            seen.append(depth)
            raise RuntimeError("measured")   # reported, not lost

        original = batch.preflight.run
        batch.preflight.run = spy
        try:
            with tempfile.TemporaryDirectory() as folder:
                for name in ("a.wav", "b.wav"):
                    open(os.path.join(folder, name), "wb").write(b"\0" * 16)
                with self.assertRaises(batch.BatchError):
                    batch.run([folder], "web", ffmpeg="ffmpeg",
                              ffprobe="ffprobe", depth="full")
        finally:
            batch.preflight.run = original
        self.assertEqual(seen, ["full", "full"])
