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

import batch  # noqa: E402
import captions  # noqa: E402
import checks  # noqa: E402
import corrections  # noqa: E402
import platform_support  # noqa: E402
import preflight  # noqa: E402
import profiles  # noqa: E402
import report  # noqa: E402
import video  # noqa: E402

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


def picture(path, *sources, filters=None):
    command = [FFMPEG, "-y", "-v", "error"]
    for source in sources:
        command += ["-f", "lavfi", "-i", source]
    if filters:
        command += ["-filter_complex", filters]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(command, check=True)
    return path


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class PictureTests(unittest.TestCase):
    SIZE = "size=320x180"

    def test_black_at_the_end_is_found_and_placed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = picture(os.path.join(folder, "tail.mp4"),
                           f"testsrc2={self.SIZE}:rate=25:d=2",
                           f"color=black:{self.SIZE}:rate=25:d=3",
                           filters="[0:v][1:v]concat=n=2:v=1:a=0")
            m = video.analyse(path, ffmpeg=FFMPEG, duration_s=5.0)
            self.assertGreater(m["trailing_black_s"], 2.5)
            self.assertEqual(m["leading_black_s"], 0)
            self.assertEqual(m["black"][0]["position"], "tail")

    def test_a_still_picture_reads_as_frozen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = picture(os.path.join(folder, "frozen.mp4"),
                           f"testsrc2={self.SIZE}:rate=25:d=2",
                           f"color=c=gray:{self.SIZE}:rate=25:d=5",
                           filters="[0:v][1:v]concat=n=2:v=1:a=0")
            m = video.analyse(path, ffmpeg=FFMPEG, duration_s=7.0)
            self.assertGreater(m["longest_frozen_s"], 4.0)

    def test_a_strobe_is_flagged_and_a_moving_picture_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            strobe = picture(
                os.path.join(folder, "strobe.mp4"),
                f"color=c=white:{self.SIZE}:rate=30:d=3,"
                r"geq=lum='if(lt(mod(floor(T*10)\,2)\,1)\,235\,16)'"
                ":cb=128:cr=128")
            calm = picture(os.path.join(folder, "calm.mp4"),
                           f"testsrc2={self.SIZE}:rate=25:d=3")
            self.assertGreater(
                video.analyse(strobe, ffmpeg=FFMPEG, duration_s=3.0)
                ["flash_regions"], 0)
            self.assertEqual(
                video.analyse(calm, ffmpeg=FFMPEG, duration_s=3.0)
                ["flash_regions"], 0,
                "a moving test pattern is not a flashing hazard")

    def test_two_rates_in_one_file_read_as_variable(self):
        with tempfile.TemporaryDirectory() as folder:
            first = picture(os.path.join(folder, "a.mp4"),
                            f"testsrc2={self.SIZE}:rate=25:d=2")
            second = picture(os.path.join(folder, "b.mp4"),
                             f"testsrc2={self.SIZE}:rate=50:d=2")
            listing = os.path.join(folder, "list.txt")
            Path(listing).write_text(
                "".join(f"file '{os.path.basename(p)}'\n"
                        for p in (first, second)), encoding="utf-8")
            joined = os.path.join(folder, "joined.mkv")
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat",
                            "-safe", "0", "-i", listing, "-c", "copy",
                            "-fps_mode", "passthrough", joined], check=True)
            self.assertEqual(video.frame_rate_mode(joined, 4.0, FFPROBE), "vfr")
            self.assertEqual(video.frame_rate_mode(first, 2.0, FFPROBE), "cfr")

    def test_the_picture_pass_only_runs_when_a_rule_asks_for_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = picture(os.path.join(folder, "calm.mp4"),
                           f"testsrc2={self.SIZE}:rate=25:d=2")
            subprocess.run([FFMPEG, "-y", "-v", "error", "-i", path,
                            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                            "-c:v", "copy", "-c:a", "aac", "-shortest",
                            os.path.join(folder, "with-audio.mp4")], check=True)
            media = os.path.join(folder, "with-audio.mp4")
            _, quiet, _, _ = preflight.run(media, "spotify_podcast")
            _, asked, _, _ = preflight.run(media, "web")
            self.assertNotIn("black_seconds", quiet,
                             "no picture rule, no picture decode")
            self.assertIn("black_seconds", asked)


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class CaptionTests(unittest.TestCase):
    CLEAN = ("1\n00:00:00,500 --> 00:00:03,500\nA readable line here.\n\n"
             "2\n00:00:04,000 --> 00:00:07,000\nAnd a second one.\n")
    BROKEN = ("1\n00:00:00,200 --> 00:00:00,600\n"
              "Far too many characters to be read in four tenths of one second"
              "\n\n2\n00:00:00,500 --> 00:00:02,000\nOverlapping.\n")

    def _media(self, folder, seconds=8):
        path = os.path.join(folder, "episode.wav")
        return generate(path, f"0.3*({SPEECH})", duration=seconds)

    def test_a_sidecar_is_found_beside_the_media_and_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            media = self._media(folder)
            Path(os.path.join(folder, "episode.srt")).write_text(
                self.CLEAN, encoding="utf-8")
            _, m, result, _ = preflight.run(media, "web")
            self.assertEqual(m["caption_cue_count"], 2)
            self.assertEqual(m["caption_origin"], "sidecar")
            overlaps = next(f for f in result["findings"]
                            if f["id"] == "caption_overlaps")
            self.assertEqual(overlaps["status"], "pass")

    def test_broken_captions_fail_and_name_the_cue(self):
        with tempfile.TemporaryDirectory() as folder:
            media = self._media(folder)
            Path(os.path.join(folder, "episode.srt")).write_text(
                self.BROKEN, encoding="utf-8")
            _, _, result, _ = preflight.run(media, "web")
            overlaps = next(f for f in result["findings"]
                            if f["id"] == "caption_overlaps")
            self.assertEqual(overlaps["status"], "fail")
            self.assertIn("cue 2", overlaps["intervals"][0]["detail"])

    def test_an_explicit_caption_path_beats_the_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            media = self._media(folder)
            Path(os.path.join(folder, "episode.srt")).write_text(
                self.CLEAN, encoding="utf-8")
            other = os.path.join(folder, "elsewhere.srt")
            Path(other).write_text(self.BROKEN, encoding="utf-8")
            _, m, _, _ = preflight.run(media, "web", caption_path=other)
            self.assertEqual(m["caption_source"], other)

    def test_no_captions_leaves_the_caption_rules_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            media = self._media(folder)
            _, _, result, _ = preflight.run(media, "web")
            overlaps = next(f for f in result["findings"]
                            if f["id"] == "caption_overlaps")
            self.assertEqual(overlaps["status"], "skip")

    def test_an_embedded_stream_is_read_when_there_is_no_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "subs.srt")
            Path(source).write_text(self.CLEAN, encoding="utf-8")
            media = picture(os.path.join(folder, "silent.mp4"),
                            "testsrc2=size=320x180:rate=25:d=8")
            muxed = os.path.join(folder, "muxed.mkv")
            subprocess.run([FFMPEG, "-y", "-v", "error", "-i", media,
                            "-i", source, "-c", "copy", "-c:s", "srt",
                            muxed], check=True)
            _, m, _, _ = preflight.run(muxed, "web")
            self.assertEqual(m["caption_origin"], "embedded")
            self.assertEqual(m["caption_cue_count"], 2)


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class SubtitleFileTests(unittest.TestCase):
    def test_a_caption_file_can_be_checked_with_no_media_at_all(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "film.srt")
            Path(path).write_text(CaptionTests.BROKEN, encoding="utf-8")
            facts, m, result, _ = preflight.run(path, "subtitles")
            self.assertIsNone(facts["audio"])
            self.assertEqual(result["verdict"], "fail")
            speed = next(f for f in result["findings"]
                         if f["id"] == "reading_speed")
            self.assertEqual(speed["status"], "fail")

    def test_audio_rules_are_skipped_rather_than_passed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "film.srt")
            Path(path).write_text(CaptionTests.CLEAN, encoding="utf-8")
            _, _, result, _ = preflight.run(path, "subtitles")
            silent = next(f for f in result["findings"]
                          if f["id"] == "silent_channel")
            self.assertEqual(silent["status"], "skip",
                             "a file with no channels has not passed a "
                             "channel check")


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class ChapterAndChartTests(unittest.TestCase):
    METADATA = (";FFMETADATA1\n"
                "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=12000\n"
                "title=Quiet one\n"
                "[CHAPTER]\nTIMEBASE=1/1000\nSTART=12000\nEND=24000\n"
                "title=Loud one\n")

    def _chaptered(self, folder):
        metadata = os.path.join(folder, "chapters.txt")
        Path(metadata).write_text(self.METADATA, encoding="utf-8")
        path = os.path.join(folder, "book.m4a")
        expression = (f"0.03*({SPEECH})*lt(t\\,12) + "
                      f"0.4*({SPEECH})*gt(t\\,12)")
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
             f"aevalsrc={expression}:d=24:s=44100", "-i", metadata,
             "-map_metadata", "1", "-ac", "1", "-c:a", "aac", "-b:a", "128k",
             path], check=True)
        return path

    def test_chapter_markers_are_read_and_measured_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._chaptered(folder)
            facts, m, result, profile = preflight.run(path, "spotify_podcast")
            self.assertEqual(len(facts["chapters"]), 2)
            envelope = report.envelope(facts, m, result, profile)
            first, second = envelope["chapters"]
            self.assertEqual(first["title"], "Quiet one")
            self.assertLess(first["loudest_short_term"],
                            second["loudest_short_term"] - 6,
                            "the loud chapter must read as loud")

    def test_the_report_carries_a_reduced_timeline_not_the_whole_one(self):
        with tempfile.TemporaryDirectory() as folder:
            path = generate(os.path.join(folder, "long.wav"),
                            f"0.2*({SPEECH})", duration=30)
            facts, m, result, profile = preflight.run(path, "ebu_r128")
            envelope = report.envelope(facts, m, result, profile)
            self.assertTrue(envelope["timeline"])
            self.assertLessEqual(len(envelope["timeline"]),
                                 len(m["timeline"]))
            self.assertNotIn("timeline", envelope["measurements"])

    def test_the_markdown_export_writes_a_chart_beside_itself(self):
        with tempfile.TemporaryDirectory() as folder:
            path = generate(os.path.join(folder, "a.wav"),
                            f"0.2*({SPEECH})", duration=12)
            out = os.path.join(folder, "report.md")
            preflight.main(["check", path, "--target", "ebu_r128", "--quiet",
                            "--markdown", out])
            drawing = os.path.join(folder, "report.loudness.svg")
            self.assertTrue(os.path.isfile(drawing))
            body = Path(out).read_text(encoding="utf-8")
            self.assertIn("![Loudness over time](report.loudness.svg)", body)
            self.assertIn("<svg", Path(drawing).read_text(encoding="utf-8"))

    def test_a_caption_file_has_no_chart_and_does_not_pretend_to(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "film.srt")
            Path(path).write_text(CaptionTests.CLEAN, encoding="utf-8")
            facts, m, result, profile = preflight.run(path, "subtitles")
            envelope = report.envelope(facts, m, result, profile)
            self.assertEqual(envelope["timeline"], [])
            self.assertEqual(report.chart_svg(envelope), "")


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class DeliveryTests(unittest.TestCase):
    """Real files, and the faults that only exist between them."""

    ROOM = "0.00018*(1-2*random(2))"

    def _chapter(self, folder, name, amplitude, channels=1):
        expression = (f"{self.ROOM} + {amplitude}*({SPEECH})"
                      f"*between(t\\,0.75\\,8)")
        source = expression if channels == 1 else f"{expression}|{expression}"
        path = os.path.join(folder, name)
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
             f"aevalsrc={source}:d=11:s=44100", "-c:a", "libmp3lame",
             "-b:a", "192k", "-abr", "0", "-ar", "44100", path], check=True)
        return path

    def test_a_title_of_valid_files_can_still_be_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("chapter-01.mp3", "chapter-02.mp3"):
                self._chapter(folder, name, 0.392)
            self._chapter(folder, "chapter-09.mp3", 0.392, channels=2)
            result = batch.run([folder], "acx", FFMPEG, FFPROBE)

            self.assertEqual(len(result["files"]), 3)
            self.assertEqual(result["set_result"]["verdict"], "fail")
            channels = next(f for f in result["set_result"]["findings"]
                            if f["id"] == "channels")
            self.assertEqual(channels["files"], ["chapter-09.mp3"])

    def test_the_corrected_copies_of_a_folder_are_not_checked_next_time(self):
        with tempfile.TemporaryDirectory() as folder:
            self._chapter(folder, "chapter-01.mp3", 0.392)
            Path(os.path.join(folder,
                              "chapter-01.preflight.mp3")).write_bytes(b"x")
            found = batch.collect([folder])
            self.assertEqual([os.path.basename(p) for p in found],
                             ["chapter-01.mp3"])

    def test_a_file_that_cannot_be_read_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            self._chapter(folder, "chapter-01.mp3", 0.392)
            Path(os.path.join(folder, "broken.wav")).write_bytes(b"not audio")
            result = batch.run([folder], "acx", FFMPEG, FFPROBE)
            self.assertEqual(len(result["files"]), 1)
            self.assertEqual(len(result["unreadable"]), 1)
            self.assertEqual(result["unreadable"][0]["name"], "broken.wav")
            self.assertEqual(result["verdict"], "fail")

    def test_nothing_readable_at_all_is_an_error_with_the_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(os.path.join(folder, "broken.wav")).write_bytes(b"nope")
            with self.assertRaises(batch.BatchError) as caught:
                batch.run([folder], "acx", FFMPEG, FFPROBE)
            self.assertIn("broken.wav", str(caught.exception))

    def test_the_delivery_report_and_its_chart_are_written(self):
        with tempfile.TemporaryDirectory() as folder:
            self._chapter(folder, "chapter-01.mp3", 0.392)
            self._chapter(folder, "chapter-02.mp3", 0.621)
            out = os.path.join(folder, "delivery.md")
            code = preflight.main(["batch", folder, "--target", "acx",
                                   "--quiet", "--markdown", out])
            self.assertIn(code, (0, 1))
            body = Path(out).read_text(encoding="utf-8")
            self.assertIn("## Files", body)
            self.assertIn("chapter-01.mp3", body)
            drawing = os.path.join(folder, "delivery.loudness.svg")
            self.assertTrue(os.path.isfile(drawing))

    def test_the_command_line_exit_code_reports_the_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            self._chapter(folder, "chapter-01.mp3", 0.392)
            self._chapter(folder, "chapter-09.mp3", 0.392, channels=2)
            self.assertEqual(
                preflight.main(["batch", folder, "--target", "acx",
                                "--quiet"]), 1,
                "a title whose files disagree must fail the exit code")


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
