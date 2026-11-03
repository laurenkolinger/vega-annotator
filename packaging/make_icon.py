#!/usr/bin/env python3
"""Draw the VEGA Annotator icon as a PNG, with no image library.

The icon is a click on a coral: a magenta outline around a pale disk on VEGA's near-black
background, with a white survey point at its center. A Windows .ico holds the same PNG.

Usage: python3 make_icon.py OUT.png [SIZE]
       python3 make_icon.py OUT.ico [SIZE]
"""

import math
import struct
import sys
import zlib

BACKGROUND = (10, 10, 15)
MAGENTA = (226, 0, 116)
PINK = (255, 179, 217)
WHITE = (255, 255, 255)
CORNER_FRACTION = 0.22
DISK_RADIUS_FRACTION = 0.30
RING_WIDTH_FRACTION = 0.06
POINT_RADIUS_FRACTION = 0.05
DEFAULT_SIZE = 256


def blend(base, color, alpha):
    """Mix a color over a base color.

    Args:
        base: The (r, g, b) tuple underneath.
        color: The (r, g, b) tuple on top.
        alpha: Coverage of the top color, 0 to 1.

    Returns:
        The mixed (r, g, b) tuple.
    """
    return tuple(int(round(b * (1 - alpha) + c * alpha)) for b, c in zip(base, color))


def coverage(distance, edge):
    """Return how much of a pixel lies inside a shape, with a one-pixel soft edge.

    Args:
        distance: The pixel's signed distance measure.
        edge: The distance of the shape's boundary.

    Returns:
        Coverage from 0 to 1.
    """
    return max(0.0, min(1.0, edge - distance + 0.5))


def rounded_square_alpha(x, y, size):
    """Return the alpha of the icon's rounded square at one pixel.

    Args:
        x: Pixel column.
        y: Pixel row.
        size: The icon's size in pixels.

    Returns:
        Alpha from 0 to 1.
    """
    radius = size * CORNER_FRACTION
    dx = max(radius - x - 0.5, x + 0.5 - (size - radius), 0)
    dy = max(radius - y - 0.5, y + 0.5 - (size - radius), 0)
    return coverage(math.hypot(dx, dy), radius)


def pixel(x, y, size):
    """Return one pixel's (r, g, b, a).

    Args:
        x: Pixel column.
        y: Pixel row.
        size: The icon's size in pixels.

    Returns:
        A four-item tuple of 0 to 255 values.
    """
    center = size / 2
    distance = math.hypot(x + 0.5 - center, y + 0.5 - center)
    disk = size * DISK_RADIUS_FRACTION
    ring = size * RING_WIDTH_FRACTION
    color = BACKGROUND
    color = blend(color, PINK, coverage(distance, disk) * 0.35)
    color = blend(color, MAGENTA, coverage(abs(distance - disk), ring / 2))
    color = blend(color, WHITE, coverage(distance, size * POINT_RADIUS_FRACTION))
    return (*color, int(round(255 * rounded_square_alpha(x, y, size))))


def png_bytes(size):
    """Encode the icon as PNG bytes.

    Args:
        size: The icon's size in pixels.

    Returns:
        The PNG file's bytes.
    """
    def chunk(tag, data):
        """Encode one PNG chunk."""
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(pixel(x, y, size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def ico_bytes(size):
    """Encode the icon as a Windows .ico file holding one PNG image.

    Args:
        size: The image's size in pixels, at most 256.

    Returns:
        The .ico file's bytes.
    """
    png = png_bytes(size)
    stored = 0 if size >= 256 else size
    return (struct.pack("<HHH", 0, 1, 1)
            + struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(png), 22) + png)


def main(argv):
    """Write the icon file named on the command line.

    Args:
        argv: The output path, then an optional size.

    Returns:
        0 on success, 2 on bad arguments.
    """
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    size = int(argv[1]) if len(argv) > 1 else DEFAULT_SIZE
    data = ico_bytes(min(size, DEFAULT_SIZE)) if argv[0].endswith(".ico") else png_bytes(size)
    with open(argv[0], "wb") as handle:
        handle.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
