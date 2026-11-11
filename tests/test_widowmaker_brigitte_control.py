"""Staged actual-input checks for Brigitte control against Widowmaker."""

import unittest

from test_game_combat import aim
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import brigitte_shield, server_snapshot
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.expr import Entity


class WidowmakerBrigitteControlTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[brigitte_shield.HERO]))
        self.a.mover.position[:] = (0, 0, 3)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat

    def test_actual_bash_damages_stuns_and_then_allows_widow_fire(self):
        for _ in range(35):
            self.run.step({self.b: (0, 0, SCOPE)})
        self.run.step({self.b: (0, 0, SCOPE | FIRE)})
        for _ in range(20):
            self.run.step({self.b: (0, 0, SCOPE)})
            if self.fight.is_stunned(self.a):
                break
        self.assertEqual(self.health(self.a), 170)
        self.assertTrue(self.fight.is_stunned(self.a))
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.press(FIRE, 5)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.press(0, 60)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)

    def test_actual_bash_interrupts_active_grapple_and_keeps_its_cooldown(self):
        self.run.match.collision_world = World([
            (-30, 0, -30, -30, 0, 30, 30, 0, 30),
            (-30, 0, -30, 30, 0, 30, 30, 0, -30),
            (-5, 0, 19, 5, 0, 19, 5, 4, 19),
            (-5, 0, 19, 5, 4, 19, -5, 4, 19),
        ])
        for _ in range(35):
            self.run.step({self.b: (0, 0, SCOPE)})
        hook = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x005A)
        self.run.step({self.a: (0, 0, 8), self.b: (0, 0, SCOPE)})
        for _ in range(50):
            self.run.step({self.b: (0, 0, SCOPE)})
            if hook.state(1).active:
                break
        self.assertTrue(hook.state(1).active)
        self.b.mover.position[:] = (0, 0, self.a.mover.position[2] - 2)
        self.b.mover.body = None
        self.run.step({self.b: (0, 0, SCOPE | FIRE)})
        for _ in range(5):
            self.run.step({self.b: (0, 0, SCOPE)})
            if self.fight.is_stunned(self.a):
                break
        self.assertEqual(self.health(self.a), 170)
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertFalse(hook.state(1).active)
        self.assertGreater(hook.vars[61].value(), 10)

    def bash(self):
        self.run.step({self.b: (0, 0, SCOPE | FIRE)})
        for _ in range(35):
            self.run.step({self.b: (0, 0, SCOPE)})

    def test_bash_cooldown_rejects_repeat_then_admits_new_press(self):
        for _ in range(35):
            self.run.step({self.b: (0, 0, SCOPE)})
        self.bash()
        self.assertEqual(self.health(self.a), 170)
        self.b.mover.position[:] = (0, 0, 0)
        self.b.mover.body = None
        self.bash()
        self.assertEqual(self.health(self.a), 170)
        for _ in range(450):
            self.run.step({self.b: (0, 0, SCOPE)})
        self.b.mover.position[:] = (0, 0, 0)
        self.b.mover.body = None
        self.bash()
        self.assertEqual(self.health(self.a), 165)

    def test_bash_rejects_allies_and_outside_reach(self):
        for mode in ('ally', 'range'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'ally':
                    self.a.team = self.b.team
                else:
                    self.a.mover.position[2] = 12
                for _ in range(35):
                    self.run.step({self.b: (0, 0, SCOPE)})
                self.bash()
                self.assertEqual(self.health(self.a), 175)
                self.assertFalse(self.fight.is_stunned(self.a))

    def test_actual_whip_damages_once_and_displaces_widow(self):
        self.a.mover.position[:] = (0, 0, 8)
        self.run.step({self.b: (0, 0, 8)})
        for _ in range(60):
            self.run.step({})
        self.assertEqual(self.health(self.a), 105)
        self.assertGreater(self.a.mover.position[2], 8.2)
        self.assertFalse(self.fight.brigitte_control.whips)

    def test_active_whip_world_clock_is_stored_and_caster_replacement_retires_it(self):
        self.a.mover.position[:] = (0, 0, 18)
        self.run.step({self.b: (0, 0, 8)})
        manager = self.fight.brigitte_control
        for _ in range(40):
            if manager.whips:
                break
            self.run.step({})
        flight, = manager.whips

        def capture():
            storage = server_snapshot._Graph({
                'combat': self.fight, 'caster': self.b, 'graph': flight.instance,
            }, server_snapshot.WorldRegistry())
            root = storage.value(manager)
            return root, storage.finish()

        first = capture()
        flight.last += 1
        self.assertNotEqual(capture(), first)
        flight.last -= 1
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E000000000006E]))
        self.assertFalse(manager.whips)
        self.assertFalse(manager.seen)

    def test_static_wall_stops_whip_before_widow(self):
        self.a.mover.position[:] = (0, 0, 8)
        self.run.match.collision_world = World([
            (-30, 0, -30, -30, 0, 30, 30, 0, 30),
            (-30, 0, -30, 30, 0, 30, 30, 0, -30),
            (-5, 0, 5, 5, 0, 5, 5, 4, 5),
            (-5, 0, 5, 5, 4, 5, -5, 4, 5),
        ])
        self.run.step({self.b: (0, 0, 8)})
        for _ in range(60):
            self.run.step({})
        self.assertEqual(self.health(self.a), 175)
        self.assertAlmostEqual(self.a.mover.position[2], 8)
        self.assertTrue(self.fight.brigitte_control.seen)


class WidowmakerBashBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_actual_projected_barrier_blocks_bash_and_stun(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[brigitte_shield.HERO]))
        self.a.mover.position[:] = (0, 0, 3)
        self.b.mover.position[:] = (0, 0, 0)
        self.c.mover.position[:] = (-2, 0, 3)
        for player in self.run.players:
            player.mover.body = None
        fight = self.run.match.combat
        for _ in range(35):
            self.run.step({self.b: (0, 0, SCOPE)})
        protecting = aim(fight._eye(self.c), fight._eye(self.a))
        self.run.step({self.b: (0, 0, SCOPE), self.c: (*protecting, 0x10)})
        self.assertEqual(self.barrier().vars[536].value(), Entity(self.a.body))
        self.run.step({self.b: (0, 0, SCOPE | FIRE)})
        for _ in range(20):
            self.run.step({self.b: (0, 0, SCOPE)})
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 195)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(fight.is_stunned(self.a))

    def test_actual_projected_barrier_blocks_whip_displacement(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[brigitte_shield.HERO]))
        self.b.mover.position[:] = (0, 0, 8)
        self.b.mover.body = None
        fight = self.run.match.combat
        self.activate()
        self.step(enemy=8)
        for _ in range(50):
            self.step()
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 130)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(tuple(self.a.mover.position), (0, 0, 0))
