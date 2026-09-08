"""The window's caches must never outlive the files they describe."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import app  # noqa: E402


class FileCacheTests(unittest.TestCase):
    def setUp(self):
        app._cache.clear()
        app._delivery.clear()

    def test_a_same_size_replacement_is_not_the_same_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "chapter.wav")
            path.write_bytes(b"old")
            app._remember(str(path), "acx", {"answer": "old"})

            replacement = Path(folder, "replacement.wav")
            replacement.write_bytes(b"new")
            os.replace(replacement, path)

            self.assertIsNone(app._recall(str(path), "acx"))


class DeliveryCacheTests(unittest.TestCase):
    def setUp(self):
        app._delivery.clear()

    def test_a_changed_chapter_invalidates_the_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder, "01.wav"), Path(folder, "02.wav")]
            for path in paths:
                path.write_bytes(b"first")
            names = [str(path) for path in paths]
            result = {"measured": True}
            app._remember_delivery(names, "acx", result)
            self.assertIs(app._recall_delivery(names, "acx"), result)

            paths[1].write_bytes(b"second version")

            self.assertIsNone(app._recall_delivery(names, "acx"))

    def test_a_replaced_chapter_invalidates_the_delivery_even_at_same_size(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "01.wav")
            path.write_bytes(b"old")
            app._remember_delivery([str(path)], "acx", {"measured": True})

            replacement = Path(folder, "replacement.wav")
            replacement.write_bytes(b"new")
            os.replace(replacement, path)

            self.assertIsNone(app._recall_delivery([str(path)], "acx"))

    def test_a_missing_chapter_is_never_recalled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "01.wav")
            path.write_bytes(b"audio")
            app._remember_delivery([str(path)], "acx", {"measured": True})
            path.unlink()

            self.assertIsNone(app._recall_delivery([str(path)], "acx"))

    def test_picture_depth_is_part_of_a_delivery_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "01.mp4")
            path.write_bytes(b"video")
            app._remember_delivery([str(path)], "youtube",
                                   {"measured": True}, "full")

            self.assertIsNone(app._recall_delivery(
                [str(path)], "youtube", "selective"))
            self.assertIsNotNone(app._recall_delivery(
                [str(path)], "youtube", "full"))


class EstimatingBeforeAnythingRuns(unittest.TestCase):
    """Weighted by predicted cost, because file count is not the work."""

    def setUp(self):
        app._intakes.clear()

    def stash(self, *items):
        return app._remember_intake({"items": list(items), "groups": [],
                                     "counts": {}})

    def audio(self, path, duration):
        return {"path": path, "kind": "audio",
                "facts": {"container": {"duration_s": duration},
                          "audio": {"channels": 2}}}

    def video(self, path, duration, width=1920, height=1080, fps=60):
        return {"path": path, "kind": "video",
                "facts": {"container": {"duration_s": duration},
                          "audio": {"channels": 2},
                          "video": {"width": width, "height": height,
                                    "avg_frame_rate": fps}}}

    def test_a_picture_pass_dwarfs_the_sound_beside_it(self):
        token = self.stash(self.audio("/a.wav", 187),
                           self.video("/b.mp4", 187))
        out = app.estimate_run(token, [
            {"path": "/a.wav", "target": "acx"},
            {"path": "/b.mp4", "target": "youtube"}])
        rows = {r["path"]: r["seconds"] for r in out["per_file"]}
        self.assertLess(rows["/a.wav"], 5)
        self.assertGreater(rows["/b.mp4"], 100)

    def test_a_skipped_file_costs_nothing_and_is_not_counted(self):
        token = self.stash(self.video("/b.mp4", 187))
        out = app.estimate_run(token, [{"path": "/b.mp4", "target": "youtube",
                                        "action": "skip"}])
        self.assertEqual(out["files"], 0)
        self.assertEqual(out["seconds"], 0.0)

    def test_a_target_asking_nothing_of_the_picture_costs_almost_nothing(self):
        token = self.stash(self.video("/b.mp4", 187))
        loud = app.estimate_run(token, [{"path": "/b.mp4",
                                         "target": "ebu_r128"}])
        full = app.estimate_run(token, [{"path": "/b.mp4",
                                         "target": "youtube"}])
        self.assertLess(loud["seconds"], full["seconds"] / 10)

    def test_full_depth_costs_more_than_selective(self):
        token = self.stash(self.video("/b.mp4", 187))
        lean = app.estimate_run(token, [{"path": "/b.mp4",
                                         "target": "social_vertical"}])
        everything = app.estimate_run(token, [{"path": "/b.mp4",
                                               "target": "social_vertical",
                                               "depth": "full"}])
        self.assertGreater(everything["seconds"], lean["seconds"])

    def test_the_range_brackets_the_estimate(self):
        token = self.stash(self.video("/b.mp4", 187))
        out = app.estimate_run(token, [{"path": "/b.mp4",
                                        "target": "youtube"}])
        self.assertLess(out["low"], out["seconds"])
        self.assertGreater(out["high"], out["seconds"])

    def test_a_forgotten_selection_says_so_rather_than_guessing(self):
        with self.assertRaises(ValueError) as caught:
            app.estimate_run("nothing-like-a-token", [])
        self.assertIn("again", str(caught.exception))

    def test_only_a_few_selections_are_kept(self):
        for _ in range(app._INTAKES_KEPT + 3):
            self.stash(self.audio("/a.wav", 1))
        self.assertLessEqual(len(app._intakes), app._INTAKES_KEPT)


class ProfilesAreRefusedForTheWrongFile(unittest.TestCase):
    """A dropdown is a convenience, not a guarantee about what arrives."""

    def test_a_picture_target_is_refused_for_a_caption_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "lines.srt")
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
            with self.assertRaises(ValueError) as caught:
                app._refuse_mismatch(str(path), "youtube")
            self.assertIn("caption", str(caught.exception))

    def test_the_caption_target_is_allowed_for_a_caption_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "lines.srt")
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
            app._refuse_mismatch(str(path), "subtitles")

    def test_an_unreadable_file_is_left_to_the_check_to_explain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "broken.wav")
            path.write_bytes(b"not audio")
            app._refuse_mismatch(str(path), "acx")
