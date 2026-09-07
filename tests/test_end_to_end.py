"""The whole path, on real files, when ffmpeg is present.

These are the tests that would notice ffmpeg changing the shape of its output,
so they are not mocked and they are not skipped quietly: when ffmpeg is absent
the skip message says so.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import checks  # noqa: E402
import corrections  # noqa: E402
import platform_support  # noqa: E402
import preflight  # noqa: E402
import profiles  # noqa: E402
import report  # noqa: E402

FFMPEG = platform_support.find_ffmpeg()
FFPROBE = platform_support.find_ffprobe(FFMPEG)
REASON = ("ffmpeg with the ebur128 filter is not installed: "
          + platform_support.install_hint())

# A gated two-tone with a little noise: speech-shaped enough for the loudness
# and silence measurements to behave as they would on a voice.
SPEECH = ("(0.6*sin(2*PI*180*t)+0.25*sin(2*PI*430*t)+0.12*(1-2*random(1)))"
          "*(0.55+0.45*sin(2*PI*3.1*t))*gt(sin(2*PI*1.7*t)\\,-0.35)")


def generate(path, expression, duration=8, rate=44100, channels=1):
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
         f"aevalsrc={expression}:d={duration}:s={rate}",
         "-ac", str(channels), path], check=True)
    return path


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class MeasurementTests(unittest.TestCase):
    def test_a_known_tone_measures_where_it_was_put(self):
        with tempfile.TemporaryDirectory() as folder:
            path = generate(os.path.join(folder, "tone.wav"),
                            "0.5*sin(2*PI*440*t)", duration=4)
            facts, m, _, _ = preflight.run(path, "web")
            self.assertEqual(facts["audio"]["sample_rate"], 44100)
            # 0.5 of full scale is -6.02 dBFS, exactly.
            self.assertAlmostEqual(m["peak_dbfs"], -6.02, places=1)
            self.assertAlmostEqual(m["true_peak_dbfs"], -6.0, places=1)
            self.assertAlmostEqual(m["rms_dbfs"], -9.03, places=1)

    def test_silence_is_found_where_it_was_left(self):
        with tempfile.TemporaryDirectory() as folder:
            path = generate(
                os.path.join(folder, "gap.wav"),
                f"0.3*({SPEECH})*(between(t\\,1\\,3)+between(t\\,6\\,8))",
                duration=9)
            _, m, _, _ = preflight.run(path, "web")
            self.assertGreater(m["lead_silence_s"], 0.9)
            self.assertGreater(m["tail_silence_s"], 0.9)
            self.assertTrue(any(s["duration"] > 2 for s in m["mid_silences"]),
                            m["silences"])

    def test_clipping_is_found_and_located(self):
        with tempfile.TemporaryDirectory() as folder:
            path = generate(
                os.path.join(folder, "clip.wav"),
                "1.6*sin(2*PI*440*t)*between(t\\,2\\,3)+0.3*sin(2*PI*180*t)",
                duration=6, rate=48000)
            _, m, result, _ = preflight.run(path, "web")
            self.assertGreater(m["clipping_seconds"], 0.0)
            finding = next(f for f in result["findings"]
                           if f["id"] == "clipping")
            self.assertEqual(finding["status"], "warn")
            self.assertEqual(finding["timestamps"], [2.0])

    def test_a_dead_channel_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "half.wav")
            subprocess.run(
                [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                 f"aevalsrc=0.3*({SPEECH})|0:d=5:s=48000", path], check=True)
            _, m, result, _ = preflight.run(path, "web")
            self.assertEqual(m["silent_channels"], [2])
            finding = next(f for f in result["findings"]
                           if f["id"] == "silent_channel")
            self.assertEqual(finding["status"], "fail")


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class CorrectionTests(unittest.TestCase):
    def _quiet_audiobook(self, folder):
        return generate(
            os.path.join(folder, "chapter.wav"),
            f"0.00018*(1-2*random(2)) + 0.154*({SPEECH})"
            f"*between(t\\,2.4\\,17)",
            duration=25, rate=44100)

    def test_a_correctable_file_comes_back_passing(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._quiet_audiobook(folder)
            facts, m, result, profile = preflight.run(source, "acx")
            self.assertEqual(result["verdict"], "fail")

            plan = corrections.plan(facts, m, result, profile)
            self.assertTrue(plan["steps"])
            written, command, steps = corrections.apply(
                source, plan["steps"], facts, ffmpeg=FFMPEG)

            self.assertTrue(os.path.isfile(source), "the source still exists")
            self.assertNotEqual(os.path.abspath(written),
                                os.path.abspath(source))

            _, after_m, after, _ = preflight.run(written, "acx")
            self.assertEqual(after["verdict"], "pass", report.text(
                report.envelope(facts, after_m, after, profile)))
            self.assertGreater(after_m["rms_dbfs"], -23.0)
            self.assertLess(after_m["peak_dbfs"], -3.0)

    def test_the_source_is_byte_for_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._quiet_audiobook(folder)
            before = Path(source).read_bytes()
            facts, m, result, profile = preflight.run(source, "acx")
            corrections.apply(source, corrections.plan(
                facts, m, result, profile)["steps"], facts, ffmpeg=FFMPEG)
            self.assertEqual(Path(source).read_bytes(), before)

    def test_loudnorm_does_not_change_the_sample_rate_behind_your_back(self):
        with tempfile.TemporaryDirectory() as folder:
            source = generate(os.path.join(folder, "quiet.wav"),
                              f"0.02*({SPEECH})", duration=10, rate=48000)
            facts, m, result, profile = preflight.run(source, "ebu_r128")
            plan = corrections.plan(facts, m, result, profile)
            written, _, _ = corrections.apply(source, plan["steps"], facts,
                                              ffmpeg=FFMPEG)
            after_facts, _, _, _ = preflight.run(written, "ebu_r128")
            self.assertEqual(after_facts["audio"]["sample_rate"], 48000)

    def test_room_tone_is_copied_from_the_file_when_there_is_some(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._quiet_audiobook(folder)
            facts, m, result, profile = preflight.run(source, "acx")
            plan = corrections.plan(facts, m, result, profile)
            # This file has plenty of room tone, so the ends are trimmed rather
            # than padded; a file with none gets the silence fallback and says
            # so in its caveat.
            ids = [step["id"] for step in plan["steps"]]
            self.assertIn("trim_head", ids)
            self.assertIn("trim_tail", ids)


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class ExitCodeTests(unittest.TestCase):
    def test_the_command_line_says_pass_or_fail_in_its_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            path = generate(os.path.join(folder, "tone.wav"),
                            "0.5*sin(2*PI*440*t)", duration=3)
            self.assertEqual(
                preflight.main(["check", path, "--target", "web", "--quiet"]), 0)
            self.assertEqual(
                preflight.main(["check", path, "--target", "acx", "--quiet"]), 1)

    def test_a_missing_file_is_an_error_not_a_failure(self):
        self.assertEqual(
            preflight.main(["check", "/no/such/file.wav", "--quiet"]), 2)


if __name__ == "__main__":
    unittest.main()
