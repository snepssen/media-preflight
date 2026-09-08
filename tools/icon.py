#!/usr/bin/env python3
"""The application icon, drawn in the standard library.

A PNG writer and a rasteriser in a hundred lines, because the alternative was
adding an imaging library to a tool whose whole claim is that it needs nothing
installed. An icon is a rounded square and two strokes; that does not justify a
dependency, and a build that pulls one in is a build somebody cannot run.

    python3 tools/icon.py out/          writes icon_16.png … icon_1024.png

Edges are anti-aliased by drawing at four times the size and averaging back
down, which is a page of code rather than a page of coverage mathematics and
looks identical at every size an icon is ever seen at.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

# The palette is the tool's own: the ground it prints reports on, and the green
# a passing check is drawn in.
GROUND = (16, 19, 16, 255)
SIGNAL = (154, 230, 110, 255)
DIM = (58, 70, 58, 255)

# Four times oversampling at the sizes where a jagged curve is visible, two
# above that. A 1024-pixel icon has enough pixels of its own that the extra
# factor costs fifteen seconds and buys nothing anybody can see.
SUPERSAMPLE = 4
SUPERSAMPLE_LARGE = 2
LARGE_FROM = 256
SIZES = (16, 32, 64, 128, 256, 512, 1024)


def supersample_for(size):
    return SUPERSAMPLE if size < LARGE_FROM else SUPERSAMPLE_LARGE


class Canvas:
    """A grid of RGBA pixels with the three shapes this icon needs."""

    def __init__(self, size, background=(0, 0, 0, 0)):
        self.size = size
        self.pixels = [list(background) for _ in range(size * size)]

    def _blend(self, x, y, colour):
        if not (0 <= x < self.size and 0 <= y < self.size):
            return
        self.pixels[y * self.size + x] = list(colour)

    def rounded_rect(self, left, top, right, bottom, radius, colour):
        for y in range(int(top), int(bottom)):
            for x in range(int(left), int(right)):
                if self._inside_round(x, y, left, top, right, bottom, radius):
                    self._blend(x, y, colour)

    @staticmethod
    def _inside_round(x, y, left, top, right, bottom, radius):
        near_x = min(max(x, left + radius), right - radius - 1)
        near_y = min(max(y, top + radius), bottom - radius - 1)
        if near_x == x or near_y == y:
            return True
        return (x - near_x) ** 2 + (y - near_y) ** 2 <= radius ** 2

    def stroke(self, x0, y0, x1, y1, width, colour):
        """A round-capped line, drawn as the set of points near the segment."""
        half = width / 2.0
        dx, dy = x1 - x0, y1 - y0
        length_squared = dx * dx + dy * dy or 1.0
        left = int(min(x0, x1) - half - 1)
        right = int(max(x0, x1) + half + 2)
        top = int(min(y0, y1) - half - 1)
        bottom = int(max(y0, y1) + half + 2)
        for y in range(top, bottom):
            for x in range(left, right):
                t = ((x - x0) * dx + (y - y0) * dy) / length_squared
                t = min(1.0, max(0.0, t))
                near_x, near_y = x0 + t * dx, y0 + t * dy
                if (x - near_x) ** 2 + (y - near_y) ** 2 <= half * half:
                    self._blend(x, y, colour)

    def downsample(self, factor):
        """Average each factor×factor block — the whole anti-aliasing story."""
        size = self.size // factor
        out = Canvas(size)
        area = factor * factor
        for y in range(size):
            for x in range(size):
                totals = [0, 0, 0, 0]
                for sub_y in range(factor):
                    row = (y * factor + sub_y) * self.size
                    for sub_x in range(factor):
                        pixel = self.pixels[row + x * factor + sub_x]
                        for channel in range(4):
                            totals[channel] += pixel[channel]
                out.pixels[y * size + x] = [total // area for total in totals]
        return out

    def png(self):
        rows = bytearray()
        for y in range(self.size):
            rows.append(0)                      # filter: none
            for x in range(self.size):
                rows.extend(self.pixels[y * self.size + x])
        return _png(bytes(rows), self.size, self.size)


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _png(raw, width, height):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def render(size):
    """The mark: a dark rounded square, a tick, and the line it sits on."""
    scale = supersample_for(size)
    canvas = Canvas(size * scale)
    edge = size * scale
    inset = edge * 0.06
    canvas.rounded_rect(inset, inset, edge - inset, edge - inset,
                        edge * 0.20, GROUND)

    # The baseline a measurement is read against, then the tick.
    canvas.stroke(edge * 0.22, edge * 0.72, edge * 0.78, edge * 0.72,
                  edge * 0.035, DIM)
    canvas.stroke(edge * 0.26, edge * 0.50, edge * 0.44, edge * 0.66,
                  edge * 0.10, SIGNAL)
    canvas.stroke(edge * 0.44, edge * 0.66, edge * 0.75, edge * 0.30,
                  edge * 0.10, SIGNAL)
    return canvas.downsample(scale).png()


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(folder, exist_ok=True)
    for size in SIZES:
        path = os.path.join(folder, f"icon_{size}.png")
        with open(path, "wb") as handle:
            handle.write(render(size))
        print(path)


if __name__ == "__main__":
    main()
