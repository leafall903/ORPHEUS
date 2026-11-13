"""tools/extract_map_tags.py reads target tags and positions from a map entity chunk."""

import struct
import unittest

from tools.extract_map_tags import TARGET_TAG_INSTANCE, map_tags

TAG = 0x0D600000000007CA


def stu(kind: int, payload: bytes) -> bytes:
    return b"DUTS" + bytes(0x1C) + struct.pack("<I", kind) + bytes(4) + payload


def placeable(xyz, blobs) -> bytes:
    body = bytearray(112 + 8 * len(blobs))
    struct.pack_into("<3f", body, 24, *xyz)
    struct.pack_into("<I", body, 64, len(blobs))
    tail = b""
    for k, blob in enumerate(blobs):
        entry = 112 + 8 * k
        struct.pack_into("<II", body, entry, k, len(body) - entry + len(tail))
        tail += blob
    record = bytes(body) + tail
    header = bytearray(24)
    struct.pack_into("<I", header, 20, 24 + len(record))
    return bytes(header) + record


def chunk(*records) -> bytes:
    return struct.pack("<4I", len(records), 0, 16, 0) + b"".join(records)


class MapTagTests(unittest.TestCase):
    def test_tags_come_only_from_target_tag_instance_data(self):
        data = chunk(
            placeable((1.0, 2.0, 3.0), [stu(TARGET_TAG_INSTANCE, struct.pack("<Q", TAG))]),
            placeable((4.0, 5.0, 6.0), [stu(0x12345678, struct.pack("<Q", TAG + 1))]),
        )
        self.assertEqual(map_tags(data), {TAG: [(1.0, 2.0, 3.0)]})

    def test_a_blob_that_is_not_stu_v1_is_refused(self):
        with self.assertRaises(ValueError):
            map_tags(chunk(placeable((0.0, 0.0, 0.0), [b"XXXX" + bytes(40)])))


if __name__ == "__main__":
    unittest.main()
