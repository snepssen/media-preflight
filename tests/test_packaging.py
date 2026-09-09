"""The icon, and the promise that a build carries the whole application."""

import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))

import icon  # noqa: E402
import make_icns  # noqa: E402


class PngTests(unittest.TestCase):
    def setUp(self):
        self.data = icon.render(64)

    def test_it_is_a_png_a_decoder_would_accept(self):
        self.assertEqual(self.data[:8], b"\x89PNG\r\n\x1a\n")
        length, kind = struct.unpack(">I4s", self.data[8:16])
        self.assertEqual(kind, b"IHDR")
        width, height, depth, colour = struct.unpack(">IIBB",
                                                     self.data[16:16 + 10])
        self.assertEqual((width, height), (64, 64))
        self.assertEqual((depth, colour), (8, 6), "8-bit RGBA")

    def test_every_chunk_carries_a_correct_checksum(self):
        offset = 8
        seen = []
        while offset < len(self.data):
            length, kind = struct.unpack(">I4s", self.data[offset:offset + 8])
            payload = self.data[offset + 8:offset + 8 + length]
            stored, = struct.unpack(
                ">I", self.data[offset + 8 + length:offset + 12 + length])
            self.assertEqual(stored, zlib.crc32(kind + payload) & 0xFFFFFFFF,
                             f"{kind!r} checksum")
            seen.append(kind)
            offset += 12 + length
        self.assertEqual(seen, [b"IHDR", b"IDAT", b"IEND"])

    def test_the_same_size_renders_the_same_bytes(self):
        self.assertEqual(icon.render(32), icon.render(32),
                         "a build that differs run to run is a build nobody "
                         "can verify")

    def test_it_renders_at_the_sizes_asked_for(self):
        # Not every size in SIZES: rendering a 1024-pixel icon is seconds of
        # pure Python, and a test suite that costs half a minute is one people
        # stop running.
        for size in (16, 64, 128):
            data = icon.render(size)
            width, height = struct.unpack(">II", data[16:24])
            self.assertEqual((width, height), (size, size))

    def test_the_sizes_cover_what_an_icns_needs(self):
        for size in (16, 32, 128, 256, 512, 1024):
            self.assertIn(size, icon.SIZES,
                          "iconutil wants each size and its @2x")

    def test_the_large_sizes_are_oversampled_less(self):
        self.assertGreater(icon.supersample_for(32),
                           icon.supersample_for(1024))


class IcnsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.iconset = Path(self.temporary.name)
        by_size = {}
        for _, name in make_icns.CHUNKS:
            match = re.search(r"_(\d+)x\d+(@2x)?", name)
            points = int(match.group(1))
            size = points * (2 if match.group(2) else 1)
            if size not in by_size:
                by_size[size] = icon.render(size)
            (self.iconset / name).write_bytes(by_size[size])

    def tearDown(self):
        self.temporary.cleanup()

    def test_fallback_contains_every_named_png_representation(self):
        data = make_icns.pack(self.iconset)
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(struct.unpack(">I", data[4:8])[0], len(data))

        offset, seen = 8, []
        while offset < len(data):
            kind, length = struct.unpack(">4sI", data[offset:offset + 8])
            payload = data[offset + 8:offset + length]
            self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
            seen.append(kind.decode("ascii"))
            offset += length
        self.assertEqual(seen, [kind for kind, _ in make_icns.CHUNKS])

    def test_fallback_refuses_a_non_png_representation(self):
        _, name = make_icns.CHUNKS[0]
        (self.iconset / name).write_text("not an image", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "is not a PNG"):
            make_icns.pack(self.iconset)


class DrawingTests(unittest.TestCase):
    def _pixels(self, size=64):
        """Decode our own PNG back to pixels, which also proves it decodes."""
        data = icon.render(size)
        raw = b""
        offset = 8
        while offset < len(data):
            length, kind = struct.unpack(">I4s", data[offset:offset + 8])
            if kind == b"IDAT":
                raw = zlib.decompress(data[offset + 8:offset + 8 + length])
            offset += 12 + length
        rows = []
        stride = size * 4 + 1
        for y in range(size):
            row = raw[y * stride:(y + 1) * stride]
            self.assertEqual(row[0], 0, "filter type none")
            rows.append([tuple(row[1 + x * 4:5 + x * 4]) for x in range(size)])
        return rows

    def test_the_mark_is_drawn_in_the_tools_own_green(self):
        rows = self._pixels()
        greens = [p for row in rows for p in row
                  if p[1] > 180 and p[0] < 190 and p[2] < 160]
        self.assertGreater(len(greens), 60, "the tick should be visible")

    def test_the_corners_are_transparent_because_it_is_rounded(self):
        rows = self._pixels()
        self.assertEqual(rows[0][0][3], 0)
        self.assertEqual(rows[-1][-1][3], 0)

    def test_the_middle_is_the_dark_ground(self):
        rows = self._pixels()
        middle = rows[32][32]
        self.assertGreater(middle[3], 200, "opaque")

    def test_edges_are_softened_rather_than_stepped(self):
        """Supersampling should leave partial alpha somewhere on the curve."""
        rows = self._pixels()
        partial = [p for row in rows for p in row if 20 < p[3] < 235]
        self.assertGreater(len(partial), 20)


class BuildTests(unittest.TestCase):
    """A bundle missing a module fails at runtime, on somebody else's machine."""

    def _module_list(self):
        text = (TOOL / "build.sh").read_text(encoding="utf-8")
        block = re.search(r"MODULES=\((.*?)\)", text, re.S)
        self.assertIsNotNone(block, "build.sh should declare MODULES")
        return set(re.findall(r"[\w.]+\.(?:py|html)", block.group(1)))

    def test_the_build_carries_every_module_the_application_has(self):
        listed = self._module_list()
        present = {path.name for path in TOOL.glob("*.py")}
        # Everything at the top level is application code, so everything at the
        # top level has to be in the bundle.
        self.assertEqual(present - listed, set(),
                         "a module exists that no build would copy")

    def test_the_build_carries_the_window_itself(self):
        self.assertIn("index.html", self._module_list())

    def test_nothing_listed_has_gone_missing(self):
        for name in self._module_list():
            self.assertTrue((TOOL / name).is_file(),
                            f"build.sh copies {name}, which is not here")

    def test_the_zipapp_entry_point_routes_commands_and_the_window(self):
        source = (TOOL / "__main__.py").read_text(encoding="utf-8")
        for command in ("check", "batch", "fix", "targets"):
            self.assertIn(f'"{command}"', source)

    def test_the_version_is_a_version(self):
        version = (TOOL / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")


class ProjectPageTests(unittest.TestCase):
    def setUp(self):
        self.page = (TOOL / "docs" / "index.html").read_text(encoding="utf-8")

    def test_the_current_window_workflow_is_on_the_project_page(self):
        for promise in ("Guided", "Professional", "Measured progress",
                        "Portable targets"):
            self.assertIn(promise, self.page)
        self.assertIn('href="#workflow"', self.page)
        self.assertIn('id="workflow"', self.page)

    def test_the_public_check_count_matches_the_suite(self):
        loader = unittest.TestLoader()
        suite = loader.discover(str(TOOL / "tests"))
        self.assertIn(f"<b>{suite.countTestCases()}</b> checks passing",
                      self.page)


@unittest.skipUnless(sys.platform == "darwin", "the .app is macOS only")
class BundleTests(unittest.TestCase):
    def test_the_launcher_is_valid_shell_and_checks_both_prerequisites(self):
        launcher = TOOL / "packaging/launcher.sh"
        subprocess.run(["sh", "-n", str(launcher)], check=True)
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("check_ffmpeg.py", text)
        self.assertIn("3, 10", text, "it should refuse an older Python")
        self.assertIn("brew install ffmpeg", text,
                      "a missing prerequisite needs the command that fixes it")


if __name__ == "__main__":
    unittest.main()
