"""Render the Studio vector mark to PNG and a seven-size Windows icon.

Run with the application's PySide6 environment:
    python assets/generate_icon.py

The ICO embeds PNG frames, supported by the project's Windows 10+ target.
No image package or network access is required.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode the {size}px icon")
    buffer.close()
    return bytes(data)


def write_ico(frames: list[tuple[int, bytes]], target: Path) -> None:
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    payload = bytearray()
    for size, data in frames:
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256,
                                     0, 0, 1, 32, len(data), offset))
        payload.extend(data)
        offset += len(data)
    target.write_bytes(directory + payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    application = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(Path(__file__).with_name("app.svg")))
    if not renderer.isValid():
        raise RuntimeError("assets/app.svg is not a valid SVG")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "app.png").write_bytes(render_png(renderer, 512))
    write_ico([(size, render_png(renderer, size)) for size in ICON_SIZES],
              args.output_dir / "app.ico")
    print(f"Generated 512px PNG and ICO frames {ICON_SIZES} in {args.output_dir}")
    # Keep the application alive until all Qt-backed render objects are gone.
    _ = application


if __name__ == "__main__":
    main()
