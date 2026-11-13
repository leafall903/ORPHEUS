"""Public synthetic release bytes, independent of client assets or a runtime pack."""
import json
import os
import unittest
from pathlib import Path

from ow174.game import projectiles
from ow174.game.bits import BitReader


class ProjectileOwnerClockContractTests(unittest.TestCase):
    def test_epoch_mapping_and_native_release_bytes(self):
        root = Path(os.environ.get('ORPHEUS_BEHAVIOR_FIXTURES') or
                    Path(__file__).resolve().parents[2] / 'fixtures/behavior')
        fixture = json.loads((root / 'projectile-owner-clock-v1.json').read_text())
        for row, expected in zip(fixture['input']['cases'], fixture['expected'], strict=True):
            with self.subTest(clock_origin=row['clock_origin']):
                origin = tuple(row['clock_origin']) if row['clock_origin'] is not None else None
                self.assertEqual(projectiles.owner_to_world(origin, row['owner_frame']), expected['world_tick'])
                native_frame = projectiles.world_to_owner(origin, row['release_world_tick']) + projectiles.RELEASE_AHEAD
                self.assertEqual(native_frame, expected['native_release_frame'])
                encoded = projectiles.release(native_frame).getvalue()
                self.assertEqual(encoded.hex(), expected['release_hex'])
                bits = BitReader(encoded)
                self.assertEqual((bits.bit(), bits.bit()), (0, 1))
                baseline = bits.var_b()
                self.assertEqual(baseline, projectiles.NO_BASELINE)
                self.assertEqual((baseline + bits.var_b()) & 0xFFFFFFFF, expected['native_release_frame'])
