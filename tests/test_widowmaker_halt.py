"""Actual Orisa orb and pull against Widowmaker's collision mover."""

import os
import unittest

from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import body_vm_snapshot, combat, orisa_halt
from ow174.game.collision import World
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_ORISA_HALT'), 'requires authored Halt metadata')
class WidowmakerHaltTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[orisa_halt.HERO]))
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.position[:] = (0, 0, 7)
        self.b.mover.body = self.a.mover.body = None
        self.fight = self.run.match.combat
        self.manager = self.fight.halt

    def detonate(self):
        for n in range(26):
            self.run.step({self.b: (0, 0, 2 if n in (0, 25) else 0)})
        orb, = self.manager.orbs.values()
        self.assertEqual(orb.requested, self.fight.tick)
        return orb

    def test_actual_second_press_holds_orb_and_pulls_widow_without_damage(self):
        initial = tuple(self.a.mover.position)
        orb = self.detonate()
        centre = orb.position
        for _ in range(24):
            self.run.step({})
        self.assertEqual(orb.position, centre)
        self.assertEqual(tuple(self.a.mover.position), initial)
        self.run.step({})
        pull = self.manager.pulls[self.a.body]
        destination, speed = pull.instance.state(2).move_request()
        self.assertEqual(speed, 20)
        self.assertGreater(destination.z, initial[2])
        self.assertLess(destination.z, centre[2])
        for _ in range(19):
            self.run.step({})
        self.assertAlmostEqual(self.a.mover.position[2], destination.z, delta=0.002)
        self.assertFalse(self.manager.orbs)
        cast = next(i for i in self.b.body_script.component.instances.values()
                    if i.graph.index == orisa_halt.CAST)
        self.assertGreater(cast.state(2).remaining(), 5)
        self.run.step({self.b: (0, 0, 2)})
        for _ in range(15):
            self.run.step({})
        self.assertEqual(cast.state(4).counter, 1)
        self.assertFalse(self.manager.orbs)
        self.assertEqual(self.health(self.a), 175)

    def test_actual_static_wall_or_team_rejects_the_pull(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                self.setUp()
                if friendly:
                    self.a.team = self.b.team
                else:
                    self.a.mover.position[0] = 2.5
                    self.run.match.collision_world = World([
                        (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                        (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                        (1.5, 0, 2, 1.5, 0, 15, 1.5, 3, 15),
                        (1.5, 0, 2, 1.5, 3, 15, 1.5, 3, 2),
                    ])
                initial = tuple(self.a.mover.position)
                self.detonate()
                for _ in range(60):
                    self.run.step({})
                self.assertEqual(tuple(self.a.mover.position), initial)
                self.assertEqual(self.manager.pulls, {})
                self.assertEqual(self.health(self.a), 175)

    def test_world_expiry_retires_unposed_pull_and_preserves_body_clock(self):
        self.detonate()
        self.run.step({})
        pull = self.manager.pulls[self.a.body]
        script = self.a.body_script
        active = body_vm_snapshot.snapshot_digest(body_vm_snapshot.export_body_vm(script))
        frame = script.frame
        self.run.frame = pull.until
        self.run.match.update(pull.until * combat.TICK, pull.until)
        self.assertEqual(script.frame, frame)
        self.assertNotIn(self.a.body, self.manager.pulls)
        self.assertTrue(pull.instance.stopped)
        retired = body_vm_snapshot.snapshot_digest(body_vm_snapshot.export_body_vm(script))
        self.assertNotEqual(active, retired)
