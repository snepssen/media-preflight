"""Sorting a selection into what each file is, before anything is measured."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import intake  # noqa: E402
import probe  # noqa: E402
import platform_support  # noqa: E402

FFMPEG = platform_support.find_ffmpeg()
FFPROBE = platform_support.find_ffprobe(FFMPEG)
REASON = "ffmpeg is not installed: " + platform_support.install_hint()


def item(name, kind, path=None):
    return {"path": path or ("/d/" + name), "name": name, "kind": kind,
            "summary": "", "duration_s": None, "size_bytes": None,
            "reason": None, "companions": [], "facts": None}


class AStillIsNotAProgramme(unittest.TestCase):
    """The same claim the cover-art rule makes, from the other direction.

    The rule lives in probe because a check refusing a caption profile and
    intake sorting a folder have to agree about what a video is.
    """

    def facts(self, frames=None, duration=None):
        return {"video": {"nb_frames": frames, "codec": "png"},
                "container": {"duration_s": duration}}

    def test_no_timeline_at_all_is_a_still(self):
        self.assertTrue(probe.is_still(self.facts()))

    def test_one_frame_and_no_duration_is_a_still(self):
        self.assertTrue(probe.is_still(self.facts(frames=1)))

    def test_a_duration_makes_it_a_programme(self):
        self.assertFalse(probe.is_still(self.facts(duration=186.8)))

    def test_many_frames_make_it_a_programme(self):
        self.assertFalse(probe.is_still(self.facts(frames=11208)))

    def test_the_invented_frame_rate_is_not_consulted(self):
        # ffprobe reports 25 fps for a PNG, on a file with no second to hold
        # 25 frames. Nothing here may believe it.
        still = {"video": {"avg_frame_rate": 25.0, "nb_frames": None},
                 "container": {"duration_s": None}}
        self.assertTrue(probe.is_still(still))


class SidecarsBelongToTheirMedia(unittest.TestCase):
    def test_a_matching_caption_nests_under_its_media(self):
        kept = intake._attach_companions([
            item("film.mp4", "video"), item("film.srt", "captions")])
        self.assertEqual([i["name"] for i in kept], ["film.mp4"])
        self.assertEqual(kept[0]["companions"][0]["name"], "film.srt")

    def test_a_caption_with_no_media_stands_on_its_own(self):
        kept = intake._attach_companions([
            item("film.mp4", "video"), item("elsewhere.srt", "captions")])
        self.assertEqual(sorted(i["name"] for i in kept),
                         ["elsewhere.srt", "film.mp4"])

    def test_matching_is_by_folder_as_well_as_stem(self):
        kept = intake._attach_companions([
            item("film.mp4", "video", "/a/film.mp4"),
            item("film.srt", "captions", "/b/film.srt")])
        self.assertEqual(len(kept), 2, "a different folder is a different file")

    def test_audio_takes_a_sidecar_too(self):
        kept = intake._attach_companions([
            item("ep.mp3", "audio"), item("ep.vtt", "captions")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["companions"][0]["name"], "ep.vtt")


class DeliveriesAreSuggestedNotAssumed(unittest.TestCase):
    def group(self, names, kind="audio", folder="/d"):
        return intake.suggest_groups(
            [item(n, kind, os.path.join(folder, n)) for n in names])

    def test_numbered_files_with_their_own_titles_are_one_delivery(self):
        # The case batch.numbering cannot answer: the suffix differs on every
        # file because every track has a name.
        groups = self.group(["A01 Black Aura.wav", "A02 White Aura.wav",
                             "A03 Green Aura.wav"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["sequence"], "A01–A03")
        self.assertEqual(groups[0]["count"], 3)

    def test_two_files_are_a_coincidence(self):
        self.assertEqual(self.group(["A01 x.wav", "A02 y.wav"]), [])

    def test_a_gap_in_the_numbering_is_reported(self):
        groups = self.group(["A01 x.wav", "A02 y.wav", "A04 z.wav"])
        self.assertEqual(groups[0]["missing"], ["A03"])

    def test_different_prefixes_are_different_deliveries(self):
        groups = self.group(["A01 a.wav", "A02 b.wav", "A03 c.wav",
                             "B01 d.wav", "B02 e.wav", "B03 f.wav"])
        self.assertEqual(sorted(g["sequence"] for g in groups),
                         ["A01–A03", "B01–B03"])

    def test_audio_and_video_are_not_mixed_into_one_delivery(self):
        rows = [item(n, "audio", "/d/" + n)
                for n in ("A01 a.wav", "A02 b.wav", "A03 c.wav")]
        rows += [item(n, "video", "/d/" + n)
                 for n in ("A01 a.mp4", "A02 b.mp4", "A03 c.mp4")]
        self.assertEqual(len(intake.suggest_groups(rows)), 2)

    def test_unnumbered_names_suggest_nothing(self):
        self.assertEqual(self.group(["intro.wav", "outro.wav", "bed.wav"]), [])

    def test_unsupported_files_are_never_part_of_a_delivery(self):
        rows = [item(n, "unsupported", "/d/" + n)
                for n in ("A01 a.md", "A02 b.md", "A03 c.md")]
        self.assertEqual(intake.suggest_groups(rows), [])


class WhatCannotBeReadStaysVisible(unittest.TestCase):
    def test_ffmpeg_keeps_its_words_without_repeating_the_path(self):
        said = intake._plain_error(
            RuntimeError("/long/path/thing.md: Invalid data found"),
            "/long/path/thing.md")
        self.assertEqual(said, "Invalid data found.")

    def test_an_empty_complaint_still_says_something(self):
        self.assertTrue(intake._plain_error(RuntimeError(""), "/a/b"))


class TheEnvelopeSentToThePage(unittest.TestCase):
    def test_probe_output_is_dropped(self):
        rows = [item("a.wav", "audio")]
        rows[0]["facts"] = {"enormous": [0] * 1000}
        lean = intake.without_facts({"items": rows, "groups": [],
                                     "counts": {}})
        self.assertNotIn("facts", lean["items"][0])
        self.assertIn("facts", rows[0], "the original is left alone")


@unittest.skipUnless(FFMPEG and FFPROBE, REASON)
class OnRealFiles(unittest.TestCase):
    def test_it_sorts_a_folder_the_way_somebody_would(self):
        with tempfile.TemporaryDirectory() as folder:
            here = lambda n: os.path.join(folder, n)   # noqa: E731
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            "sine=f=440:d=2", here("A01 One.wav")], check=True)
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            "sine=f=440:d=2", here("A02 Two.wav")], check=True)
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            "sine=f=440:d=2", here("A03 Three.wav")],
                           check=True)
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=64x64:r=25:d=2", "-pix_fmt",
                            "yuv420p", here("clip.mp4")], check=True)
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=64x64:d=1", "-frames:v", "1",
                            here("cover.png")], check=True)
            with open(here("clip.srt"), "w") as handle:
                handle.write("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
            with open(here("notes.md"), "w") as handle:
                handle.write("# not media\n")

            env = intake.classify([folder])

        by_kind = {}
        for row in env["items"]:
            by_kind.setdefault(row["kind"], []).append(row["name"])

        self.assertEqual(sorted(by_kind["audio"]),
                         ["A01 One.wav", "A02 Two.wav", "A03 Three.wav"])
        self.assertEqual(by_kind["video"], ["clip.mp4"])
        self.assertNotIn("captions", by_kind, "the sidecar nested instead")
        self.assertIn("cover.png", by_kind["unsupported"])
        self.assertIn("notes.md", by_kind["unsupported"])

        clip = next(r for r in env["items"] if r["name"] == "clip.mp4")
        self.assertEqual([c["name"] for c in clip["companions"]], ["clip.srt"])

        self.assertEqual([g["sequence"] for g in env["groups"]], ["A01–A03"])
