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
