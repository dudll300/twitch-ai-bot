import struct
import unittest
from pathlib import Path


class IconTests(unittest.TestCase):
    def test_windows_icon_contains_small_and_large_sizes(self):
        data = (Path(__file__).resolve().parents[1] / "assets" / "app.ico").read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, image_type), (0, 1))
        sizes = set()
        for i in range(count):
            width, height, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + i * 16)
            self.assertEqual(width, height)
            self.assertLessEqual(offset + length, len(data))
            self.assertGreater(length, 0)
            sizes.add(width or 256)
        self.assertEqual(sizes, {16, 24, 32, 48, 64, 128, 256})
