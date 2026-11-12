"""Delivered Doomfist world-queued impulse and finite braking integration."""

import os
import unittest
from pathlib import Path

from test_doomfist_combat import DoomfistFixture
from ow174.game import mover, npc
from ow174.game.commands import Command


class SlamImpulseTests(DoomfistFixture, unittest.TestCase):
    def test_next_world_tick_adds_existing_vertical_velocity_once(self):
        target = self.fight.player_target(self.b)
        self.b.mover.velocity = (1, 2, 3)
        tick = self.match.tick
        self.fight.slams._queue_impulse(target, (0, 6, -3))
        self.fight.slams.advance_impulses(tick)
        self.assertEqual(self.b.mover.velocity, (1, 2, 3))
        self.fight.slams.advance_impulses(tick + 1)
        self.assertEqual(self.b.mover.velocity, (1, 8, 0))
        self.assertFalse(self.fight.slams.impulses)
        self.fight.slams.advance_impulses(tick + 1)
        self.assertEqual(self.b.mover.velocity, (1, 8, 0))
        self.assertEqual(len([key for key in self.b.mover.mods if key[0] == -2]), 1)

    def test_retired_and_past_records_cannot_launch_replacement(self):
        target = self.fight.player_target(self.b)
        tick = self.match.tick
        self.fight.slams._queue_impulse(target, (0, 6, 0))
        self.fight.slams.retire_target(target.entity)
        self.fight.slams.advance_impulses(tick + 1)
        self.assertEqual(self.b.mover.velocity, (0, 0, 0))
        self.fight.slams._queue_impulse(target, (0, 6, 0))
        self.fight.slams.advance_impulses(tick + 2)
        self.assertFalse(self.fight.slams.impulses)
        self.assertEqual(self.b.mover.velocity, (0, 0, 0))

    def test_braking_uses_world_clock_independent_of_command_epoch(self):
        body = self.b.mover
        body.add_timed_mod(mover.DECEL_GROUND, mover.MULTIPLY, .01, 200, 220)
        body.track_mods({}, 10000)
        self.assertEqual(len([key for key in body.mods if key[0] == -2]), 1)
        body.step_pose(Command(10001), .016, 200)
        expected = body.data.decel_ground_base * .01
        self.assertAlmostEqual(body.values.decel_ground_base, expected, places=5)
        body.step_pose(Command(10002), .016, 219)
        self.assertAlmostEqual(body.values.decel_ground_base, expected, places=5)
        body.step_pose(Command(10003), .016, 220)
        self.assertEqual(body.values.decel_ground_base, body.data.decel_ground_base)
        self.assertFalse([key for key in body.mods if key[0] == -2])

    def test_five_npcs_reuse_existing_prepared_mover_without_rescaling(self):
        definitions = npc.load_catalog(Path(os.environ['ORPHEUS_NPC_DATA_DIR']) / 'pve_npcs_174.json')
        for definition in definitions.values():
            with self.subTest(npc=definition.name):
                x, y, z = self.a.mover.position
                bot = npc.spawn(self.match, definition, (x + 5, y, z + 5), team=1)
                bot.route = []
                bot.firing = False
                existing = bot.ensure_manual_mover(self.match.tick, self.fight._collision(), mover.MOVER)
                self.assertIsNotNone(existing)
                existing.velocity = (1, 2, 3)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                self.fight.slams._queue_impulse(target, (0, 6, -3))
                self.fight.slams.advance_impulses(self.match.tick + 1)
                self.assertIs(bot.impulse_mover, existing)
                # Actual Heavy1254 already owns modifier26 SET0 at spawn.
                expected = (1, 2, 3) if definition.name == 'Heavy Assault' else (1, 8, 0)
                self.assertEqual(existing.velocity, expected)
                self.assertIs(existing.data, bot.movement_data or mover.mover_data(bot.body))
                npc.remove_body(self.match, bot)

class SlamImpulseStorageTests(unittest.TestCase):
    def test_real_server_stores_pending_and_applied_world_impulse(self):
        import test_server_snapshot as stored
        from ow174.game.server_snapshot import snapshot_digest
        from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest as vm_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        target_player = players[1]
        fight = target_player.match.combat
        target = fight.player_target(target_player)
        def digest():
            return snapshot_digest(case.capture(session))
        before = digest()
        fight.slams._queue_impulse(target, (0, 6, -3))
        pending = digest()
        self.assertNotEqual(before, pending)
        record = fight.slams.impulses[0]
        fight.slams.impulses[0] = (record[0] + 1, record[1], record[2])
        self.assertNotEqual(digest(), pending)
        fight.slams.impulses[0] = record
        self.assertEqual(digest(), pending)
        fight.slams.advance_impulses(record[0])
        self.assertFalse(fight.slams.impulses)
        applied = digest()
        self.assertNotEqual(applied, pending)
        vm_digest(export_body_vm(target_player.body_script))
        key = next(key for key in target_player.mover.mods if key[0] == -2)
        mod = target_player.mover.mods[key]
        mod.end += 1
        self.assertNotEqual(digest(), applied)
        mod.end -= 1
        self.assertEqual(digest(), applied)
