"""Command-batch and receipt jitter invariants on the actual Flashbang flight."""
import dataclasses
import json
import unittest
from pathlib import Path

from ow174.game import combat, projectiles
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest as server_digest
from tests.runtime import test_mccree_combat as scenes


class FlashbangClockTests(unittest.TestCase):
    def test_captured_shot_clock_is_stored_and_survives_body_replacement(self):
        run, caster, _ = scenes.McCreeMeleeTests().arena(distance=20)
        run.step({caster: (0, 0, 16)})
        rocket, = run.match.combat.rockets
        shot = rocket.shot
        old_clock = shot.clock_origin
        self.assertIsNotNone(old_clock)
        owner_boundary = object()
        # This storage oracle covers the shot authority record, with network
        # deliveries absent; encrypted peer lifecycle is checked separately.
        stored = dataclasses.replace(shot, player=owner_boundary, observers=[])

        def digest():
            graph = _Graph({'owner': owner_boundary}, {}, {})
            root = graph.value(stored)
            return server_digest({'schema': SCHEMA, 'scope': SCOPE, 'restorable': False,
                                  'roots': {'shot': root}, 'nodes': graph.finish(), 'assets': {}})

        original = digest()
        stored.clock_origin = (old_clock[0] + 1, *old_clock[1:])
        self.assertNotEqual(digest(), original)
        stored.clock_origin = old_clock
        self.assertEqual(digest(), original)
        self.assertTrue(run.match.switch_hero(caster, caster.hero, start_frame=9000))
        self.assertNotEqual(caster.body_script.mccree_birth, old_clock[:2])
        self.assertEqual(shot.clock_origin, old_clock)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        self.assertEqual(shot.clock_origin, old_clock)
        self.assertEqual(projectiles.owner_to_world((1020, 9000, 32), 9004), 1028)
        self.assertEqual(projectiles.world_to_owner((1020, 9000, 32), 1028), 9004)

    def flight(self, *, batch, jitter, command_origin):
        run, caster, target = scenes.McCreeMeleeTests().arena(distance=20)
        world_origin = run.frame
        self.assertTrue(run.match.switch_hero(caster, caster.hero, preserve_pose=True,
                                             start_frame=command_origin))
        caster.commands.reset(last_frame=command_origin)
        birth = caster.body_script.mccree_birth
        self.assertEqual(birth, (world_origin, command_origin))
        delivered = 0
        rocket = None
        sample = None
        launch_tick = None
        for offset in range(1, 65):
            run.frame = world_origin + offset
            run.match.update(run.frame * combat.TICK, run.frame)
            limit = min(40, offset - jitter)
            if limit > delivered and ((offset - jitter) % batch == 0 or limit == 40):
                caster.take_commands([Command(command_origin + at, buttons=16 if at == 4 else 0)
                                      for at in range(delivered + 1, limit + 1)])
                delivered = limit
                if rocket is None and run.match.combat.rockets:
                    rocket = next(r for r in run.match.combat.rockets if r.shooter is caster)
                    launch_tick = rocket.last_tick
                    self.assertEqual(launch_tick, world_origin + 4)
            target.take_commands([Command(run.frame)])
            self.assertEqual(caster.body_script.mccree_birth, birth)
            if offset == 16 and rocket is not None and rocket in run.match.combat.rockets:
                sample = rocket.position
            if batch == 32 and offset == 33 + jitter:
                self.assertIsNotNone(rocket)
                self.assertNotIn(rocket, run.match.combat.rockets,
                                 'a flight already older than its range limit must catch up')
        self.assertIsNotNone(rocket)
        self.assertEqual(rocket.ended, 'expired')
        self.assertAlmostEqual(rocket.flown, 7 / 30)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.projectiles.shots,
                         'owner confirmation/release must use world deadlines too')
        return sample, rocket.position, launch_tick - world_origin

    def test_packet_batch_and_arrival_jitter_do_not_restart_a_flight(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/mccree-flashbang-clock-v1.json'
        contract = json.loads(path.read_text())
        rows = []
        for case in contract['input']['cases']:
            batch, jitter, origin = (case[name] for name in ('batch', 'jitter', 'command_origin'))
            with self.subTest(batch=batch, jitter=jitter, command_origin=origin):
                row = self.flight(batch=batch, jitter=jitter, command_origin=origin)
                self.assertEqual(row[2], contract['expected']['launch_world_offset'])
                rows.append(row)
        self.assertEqual(len(rows), len(contract['input']['cases']))
        for row in rows[1:]:
            self.assertEqual(row[1:], rows[0][1:])
        self.assertEqual(rows[0][0], rows[1][0])
        self.assertEqual(rows[0][0], rows[2][0])

    def test_birth_pair_is_causal_body_storage_and_replacement_gets_a_new_pair(self):
        run, caster, _ = scenes.McCreeMeleeTests().arena(distance=20)
        script = caster.body_script
        before = snapshot_digest(export_body_vm(script))
        born_tick, born_frame = script.mccree_birth
        script.mccree_birth = born_tick + 1, born_frame
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)
        script.mccree_birth = born_tick, born_frame
        self.assertEqual(snapshot_digest(export_body_vm(script)), before)
        self.assertTrue(run.match.switch_hero(caster, caster.hero, start_frame=9000))
        self.assertIsNot(caster.body_script, script)
        self.assertEqual(caster.body_script.mccree_birth, (run.match.tick, 9000))
