#!/usr/bin/env python3
"""Pack generated PNG icon sizes into an ``.icns`` container.

``iconutil`` remains the build's first choice. This standard-library fallback
exists for macOS versions where iconutil rejects a conventional iconset. ICNS
stores modern representations as complete PNG payloads in named chunks, so no
image conversion happens here.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


# Duplicate pixel sizes are intentional: ic11 is 16 points at 2x, while icp5
# is 32 points at 1x. Finder may choose by display scale, not pixel count.
CHUNKS = (
    ("icp4", "icon_16x16.png"),
    ("ic11", "icon_16x16@2x.png"),
    ("icp5", "icon_32x32.png"),
    ("ic12", "icon_32x32@2x.png"),
    ("ic07", "icon_128x128.png"),
    ("ic13", "icon_128x128@2x.png"),
    ("ic08", "icon_256x256.png"),
    ("ic14", "icon_256x256@2x.png"),
    ("ic09", "icon_512x512.png"),
    ("ic10", "icon_512x512@2x.png"),
)


def pack(iconset):
    """Return one ICNS container, refusing missing or non-PNG inputs."""
    chunks = []
    for kind, name in CHUNKS:
        path = Path(iconset) / name
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{path} is not a PNG file")
        chunks.append(kind.encode("ascii") + struct.pack(">I", len(data) + 8)
                      + data)
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iconset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    args.output.write_bytes(pack(args.iconset))


if __name__ == "__main__":
    main()
