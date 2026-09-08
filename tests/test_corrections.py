"""Planning corrections, and the promises the planner makes."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import checks  # noqa: E402
import corrections  # noqa: E402
import profiles  # noqa: E402
from test_checks import facts, measurements  # noqa: E402


def planned(target="acx", **overrides):
    profile = profiles.get(target)
    f = facts(**overrides.pop("facts", {}))
    m = measurements(**overrides)
    result = checks.evaluate(f, m, profile)
    return f, m, result, profile, corrections.plan(f, m, result, profile)


class OrderTests(unittest.TestCase):
    def test_steps_run_in_the_order_that_makes_them_mean_anything(self):
        _, _, _, _, plan = planned(rms_dbfs=-40.0, lead_silence_s=4.0,
                                   tail_silence_s=0.1, peak_dbfs=-0.2,
                                   silences=[
                                       {"start": 0.0, "end": 4.0,
                                        "duration": 4.0, "position": "head"}])
        ids = [step["id"] for step in plan["steps"]]
        self.assertEqual(ids, sorted(ids, key=corrections.STEP_ORDER.index))
        self.assertEqual(ids[-1], "encode", "the encode is always last")

    def test_the_tail_trim_accounts_for_what_the_head_trim_removed(self):
        head = {"start": 0.0, "end": 7.0, "duration": 7.0, "position": "head"}
        tail = {"start": 52.0, "end": 60.0, "duration": 8.0, "position": "tail"}
        _, _, _, _, plan = planned(
            lead_silence_s=7.0, tail_silence_s=8.0, silences=[head, tail],
            duration_s=60.0)
        steps = {s["id"]: s for s in plan["steps"]}
        # ACX's band is one to five seconds; the head aims at 1.5, so
        # 7.0 - 1.5 = 5.5 comes off the front, and the ending is then measured
        # against 54.5 seconds rather than 60.
        self.assertIn("atrim=start=5.5", steps["trim_head"]["filters"])
        self.assertIn("atrim=end=49.5", steps["trim_tail"]["filters"])


class GuardTests(unittest.TestCase):
    """A correction must not create a fault the file did not have."""

    def test_a_gain_that_would_clip_brings_a_limiter_with_it(self):
        _, _, result, _, plan = planned(rms_dbfs=-40.0, peak_dbfs=-6.0)
        peak = next(f for f in result["findings"] if f["id"] == "peak")
        self.assertEqual(peak["status"], "pass",
                         "the source's peak is fine; only the gain breaks it")
        self.assertIn("limit_peak", [s["id"] for s in plan["steps"]])

    def test_a_gain_that_would_raise_dc_over_the_line_removes_it_first(self):
        _, _, _, _, plan = planned(
            rms_dbfs=-40.0,
            channels=[{"channel": 1, "dc_offset": 0.004, "rms_dbfs": -40.0}])
        ids = [s["id"] for s in plan["steps"]]
        self.assertIn("remove_dc", ids)
        self.assertLess(ids.index("remove_dc"), ids.index("gain"))

    def test_a_modest_gain_adds_nothing(self):
        _, _, _, _, plan = planned(rms_dbfs=-24.0, peak_dbfs=-20.0)
        self.assertNotIn("limit_peak", [s["id"] for s in plan["steps"]])
        self.assertNotIn("remove_dc", [s["id"] for s in plan["steps"]])

    def test_a_lossy_encode_buys_a_decibel_of_headroom(self):
        _, _, _, _, plan = planned(rms_dbfs=-40.0, peak_dbfs=-6.0)
        limiter = next(s for s in plan["steps"] if s["id"] == "limit_peak")
        # ACX asks for -3 dBFS; the limiter aims further under because
        # libmp3lame will hand peaks back louder than it was given.
        self.assertAlmostEqual(limiter["margin"], 1.1)
        self.assertIn("alimiter=limit=0.6237", limiter["filters"][0])


class UnfixableTests(unittest.TestCase):
    def test_faults_with_no_safe_fix_are_reported_not_dropped(self):
        _, _, _, _, plan = planned(noise_floor_dbfs=-40.0)
        self.assertEqual([u["id"] for u in plan["unfixable"]], ["noise_floor"])
        self.assertEqual(plan["steps"], [],
                         "nothing else was wrong, so nothing is proposed")

    def test_a_rule_may_name_where_a_correction_should_aim(self):
        """The middle of ACX's one-to-five-second band is three seconds, which
        is a sensible ending and an absurd opening."""
        head = next(r for r in profiles.get("acx")["rules"]
                    if r["id"] == "head_room_tone")
        self.assertEqual(corrections._target_band(head), 1.5)
        tail = next(r for r in profiles.get("acx")["rules"]
                    if r["id"] == "tail_room_tone")
        self.assertEqual(corrections._target_band(tail), 3.0)


class EncodeTests(unittest.TestCase):
    def test_a_codec_the_target_already_allows_is_kept(self):
        _, _, _, _, plan = planned("spotify_podcast",
                                   facts={"audio": dict(facts()["audio"],
                                                        codec="aac",
                                                        bit_rate=64000.0)})
        encode = next(s for s in plan["steps"] if s["id"] == "encode")
        self.assertIn("aac", encode["args"])
        self.assertNotIn("libmp3lame", encode["args"])

    def test_a_file_carrying_picture_keeps_its_container(self):
        f = facts(video_streams=[{"codec": "h264"}],
                  video={"codec": "h264", "duration_s": 60.0},
                  path="/tmp/episode.mp4",
                  container={"format_name": "mov,mp4,m4a", "duration_s": 60.0,
                             "bit_rate": 2000000.0, "tags": {}})
        m = measurements(integrated_lufs=-30.0)
        profile = profiles.get("spotify_podcast")
        result = checks.evaluate(f, m, profile)
        plan = corrections.plan(f, m, result, profile)
        out = corrections.output_path("/tmp/episode.mp4", plan["steps"])
        self.assertTrue(out.endswith(".mp4"), out)

    def test_approximate_encoders_are_asked_for_headroom(self):
        self.assertEqual(corrections._bitrate_for("libmp3lame", 192), 192)
        self.assertGreater(corrections._bitrate_for("aac", 128), 128)


class SafetyTests(unittest.TestCase):
    def test_writing_over_the_source_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "a.wav")
            Path(source).write_bytes(b"not really a wav")
            step = {"id": "encode", "kind": "encode", "args": [], "extension": "wav"}
            with self.assertRaises(corrections.CorrectionError) as caught:
                corrections.apply(source, [step], facts(), destination=source)
            self.assertIn("overwrite the source", str(caught.exception))

    def test_an_existing_output_is_refused_unless_asked_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "a.wav")
            existing = os.path.join(folder, "b.wav")
            Path(source).write_bytes(b"x")
            Path(existing).write_bytes(b"y")
            step = {"id": "encode", "kind": "encode", "args": [], "extension": "wav"}
            with self.assertRaises(corrections.CorrectionError) as caught:
                corrections.apply(source, [step], facts(), destination=existing)
            self.assertIn("already exists", str(caught.exception))

    def test_the_output_name_says_what_it_is(self):
        step = {"id": "encode", "kind": "encode", "args": [], "extension": "mp3"}
        out = corrections.output_path("/tmp/chapter one.wav", [step])
        self.assertEqual(os.path.basename(out), "chapter one.preflight.mp3")


class CommandTests(unittest.TestCase):
    def test_video_rides_along_uncompressed(self):
        f = facts(video_streams=[{"codec": "h264"}])
        steps = [{"id": "gain", "kind": "filter", "filters": ["volume=+3dB"]},
                 {"id": "encode", "kind": "encode", "args": ["-c:a", "aac"],
                  "extension": "mp4"}]
        command = corrections.build_command("/tmp/a.mp4", "/tmp/b.mp4", steps, f)
        self.assertIn("-c:v", command)
        self.assertIn("copy", command)
        self.assertIn("-map_metadata", command)

    def test_cover_art_stays_cover_art(self):
        f = facts(cover_art=True)
        steps = [{"id": "encode", "kind": "encode", "args": ["-c:a", "libmp3lame"],
                  "extension": "mp3"}]
        command = corrections.build_command("/tmp/a.mp3", "/tmp/b.mp3", steps, f)
        self.assertIn("-disposition:v:0", command)
        self.assertIn("attached_pic", command)

    def test_room_tone_is_split_once_and_reused(self):
        graph = corrections._concat_graph(
            {"start": 0.1, "length": 0.8, "copies": 3, "needed": 2.0,
             "where": "tail"}, ["volume=+2dB"])
        self.assertEqual(graph.count("asplit=3"), 1)
        self.assertIn("concat=n=3:v=0:a=1", graph)
        self.assertTrue(graph.endswith("concat=n=2:v=0:a=1[out]"))

    def test_the_pad_goes_on_the_side_it_was_asked_for(self):
        head = corrections._concat_graph(
            {"start": 0.1, "length": 1.0, "copies": 1, "needed": 0.5,
             "where": "head"}, [])
        self.assertIn("[pad][body]concat", head)


class RecipeTests(unittest.TestCase):
    def test_the_recipe_is_readable_and_repeatable(self):
        steps = [{"id": "gain", "description": "Apply +3 dB",
                  "filters": ["volume=+3dB"], "addresses": ["rms"]}]
        recipe = corrections.recipe("/tmp/a.wav", "/tmp/b.wav", steps,
                                    ["ffmpeg", "-i", "/tmp/a.wav"])
        text = json.dumps(recipe)
        self.assertIn("volume=+3dB", text)
        self.assertEqual(recipe["operations"][0]["addresses"], ["rms"])
        self.assertEqual(recipe["command"][0], "ffmpeg")


class LoudnormTests(unittest.TestCase):
    def test_the_measurement_pass_is_read_out_of_a_noisy_stderr(self):
        stderr = ("[Parsed_loudnorm_0 @ 0x7f] \n{\n"
                  '  "input_i" : "-27.76",\n  "input_tp" : "-13.66",\n'
                  '  "input_lra" : "0.40",\n  "input_thresh" : "-37.76",\n'
                  '  "target_offset" : "0.47"\n}\n')
        measured = corrections._last_json_object(stderr)
        self.assertEqual(measured["input_i"], "-27.76")

    def test_loudnorm_resamples_back_to_where_it_started(self):
        _, _, _, _, plan = planned("ebu_r128", integrated_lufs=-35.0)
        step = next(s for s in plan["steps"] if s["id"] == "loudnorm")
        self.assertEqual(step["post_filters"], ["aresample=44100"])


if __name__ == "__main__":
    unittest.main()
