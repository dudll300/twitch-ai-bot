"""Export the approved Studio liquid-glass artwork to PNG and Windows ICO.

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

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(source: QImage, size: int) -> bytes:
    image = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
    parser.add_argument("--source", type=Path, default=Path(__file__).with_name("app-source.png"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    application = QGuiApplication.instance() or QGuiApplication([])
    source = QImage(str(args.source))
    if source.isNull() or source.width() != source.height():
        raise RuntimeError(f"Icon source must be a valid square image: {args.source}")
    if not source.hasAlphaChannel():
        raise RuntimeError(f"Icon source must preserve transparency: {args.source}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "app.png").write_bytes(render_png(source, 512))
    write_ico([(size, render_png(source, size)) for size in ICON_SIZES],
              args.output_dir / "app.ico")
    print(f"Generated 512px PNG and ICO frames {ICON_SIZES} in {args.output_dir}")
    # Keep the application alive until all Qt-backed render objects are gone.
    _ = application


if __name__ == "__main__":
    main()
