"""Generate the application icons.

Draws the Strata mark with Qt (no image-editing dependency) and writes:

* ``packaging/icons/strata.png``  — 256x256, used by Linux and the window icon
* ``packaging/icons/strata.ico``  — a Vista-style ICO wrapping the same PNG,
  used by the Windows executable and installer

The ICO is assembled by hand: the format allows a PNG payload directly, so a
6-byte header plus a 16-byte directory entry is the whole container. That avoids
pulling in an image library purely to write 22 bytes of preamble.

Usage::

    python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "packaging" / "icons"
SIZE = 256

# Held for the lifetime of the process: a QGuiApplication that goes out of scope is
# garbage-collected, and the next Qt call segfaults.
_app: object | None = None


def draw(size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

    global _app
    if QGuiApplication.instance() is None:
        _app = QGuiApplication(sys.argv[:1])

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Rounded plate: the deep black-blue of --surface-void.
    plate = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    plate.setColorAt(0.0, QColor("#0a1020"))
    plate.setColorAt(1.0, QColor("#04060d"))
    painter.setBrush(plate)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)

    # Three strata: public cyan, AI violet, and the locked graphite between them.
    layers = [
        (0.30, QColor("#22e0f5"), size * 0.030),
        (0.50, QColor("#6b7793"), size * 0.024),
        (0.70, QColor("#a06bff"), size * 0.030),
    ]
    for fraction, colour, thickness in layers:
        pen = QPen(colour)
        pen.setWidthF(thickness)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        y = size * fraction
        inset = size * 0.20
        painter.drawLine(QPointF(inset, y), QPointF(size - inset, y))

    # The node that links them: selection, illuminated.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPointF(size * 0.5, size * 0.5), size * 0.055, size * 0.055)
    painter.setBrush(QColor(34, 224, 245, 90))
    painter.drawEllipse(QPointF(size * 0.5, size * 0.5), size * 0.10, size * 0.10)

    painter.end()

    # QBuffer must own its storage: passing a temporary QByteArray leaves the
    # buffer pointing at freed memory.
    storage = QByteArray()
    buffer = QBuffer()
    buffer.setBuffer(storage)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(storage)


def write_ico(png: bytes, path: Path, size: int) -> None:
    # ICONDIR: reserved=0, type=1 (icon), count=1
    header = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY: 256 is encoded as 0; planes=1, bpp=32
    entry = struct.pack(
        "<BBBBHHII",
        0 if size >= 256 else size,
        0 if size >= 256 else size,
        0,
        0,
        1,
        32,
        len(png),
        len(header) + 16,
    )
    path.write_bytes(header + entry + png)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png = draw(SIZE)

    (OUT_DIR / "strata.png").write_bytes(png)
    write_ico(png, OUT_DIR / "strata.ico", SIZE)

    print(f"wrote {OUT_DIR / 'strata.png'} ({len(png):,} bytes)")
    print(f"wrote {OUT_DIR / 'strata.ico'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
