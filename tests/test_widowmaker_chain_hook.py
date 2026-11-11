"""Actual Roadhog Chain Hook input against Widowmaker in build 104319."""

import unittest

from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import server_snapshot
from ow174.game.collision import World
from ow174.game.content import heroes

ROADHOG = 0x02E0000000000040
HOOK = 8


class WidowmakerChainHookTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[ROADHOG]))
        self.a.mover.position[:] = (0, 0, 10)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat
        self.hooks = self.fight.hooks

    def cast(self):
        self.run.step({self.b: (0, 0, HOOK)})

    def wait_for_pull(self):
        for _ in range(50):
            self.run.step({})
            if self.hooks.controlled(self.a.body):
                return
        self.fail('Actual Chain Hook did not acquire Widowmaker')

    def test_actual_hit_damage_pull_blocks_fire_and_keeps_cooldown(self):
        self.cast()
        self.assertEqual(self.health(self.a), 175)
        self.wait_for_pull()
        self.assertEqual(self.health(self.a), 145)
        before = self.health(self.b)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        for _ in range(12):
            self.run.step({self.a: (-32768, 0, FIRE)})
        self.assertLess(self.a.mover.position[2], 10)
        self.assertEqual(self.health(self.b), before)
        self.assertEqual(weapon.vars[53].value(), ammo)
        for _ in range(78):
            self.run.step({})
        self.assertAlmostEqual(self.a.mover.position[2], 3.0, places=2)
        self.assertFalse(self.hooks.controlled(self.a.body))
        self.assertEqual(self.health(self.a), 145)
        self.cast()
        for _ in range(40):
            self.run.step({})
        self.assertEqual(self.health(self.a), 145)
        self.assertFalse(self.hooks.flights)
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)

    def test_actual_miss_wall_and_team_rejection(self):
        for mode in ('miss', 'wall', 'ally'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'miss':
                    self.a.mover.position[0] = 4
                elif mode == 'ally':
                    self.a.team = self.b.team
                else:
                    self.run.match.collision_world = World([
                        (-30, 0, -30, -30, 0, 30, 30, 0, 30),
                        (-30, 0, -30, 30, 0, 30, 30, 0, -30),
                        (-5, 0, 4, 5, 0, 4, 5, 4, 4),
                        (-5, 0, 4, 5, 4, 4, -5, 4, 4),
                    ])
                start = tuple(self.a.mover.position)
                self.cast()
                for _ in range(100):
                    self.run.step({})
                self.assertEqual(self.health(self.a), 175)
                self.assertEqual(tuple(self.a.mover.position), start)
                self.assertFalse(self.hooks.flights)

    def test_sweep_front_reach_boundary(self):
        for offset in (-0.01, 0.01):
            with self.subTest(offset=offset):
                self.setUp()
                self.a.mover.position[2] = 20 + 0.5 + self.target(self.a).radius + offset
                self.cast()
                for _ in range(100):
                    self.run.step({})
                self.assertEqual(self.health(self.a), 145 if offset < 0 else 175)
                self.assertFalse(self.hooks.flights)

    def test_lethal_hook_never_moves_dead_widow(self):
        vitals = self.fight.vitals_of(self.target(self.a))
        vitals.damage(150)
        self.cast()
        for _ in range(50):
            self.run.step({})
        self.assertTrue(vitals.dead)
        self.assertFalse(self.hooks.flights)
        self.assertEqual(tuple(self.a.mover.position), (0, 0, 10))

    def test_active_pull_stored_digest_and_caster_replacement_release(self):
        self.cast()
        self.wait_for_pull()

        def stored_flight():
            # Keep external references fixed and inspect the production stored layout.
            flight, = self.hooks.flights.values()
            boundary = {
                'combat': self.fight, 'caster': self.b, 'victim': self.a,
                'hook_graph': flight.instance,
            }
            graph = server_snapshot._Graph(boundary, server_snapshot.WorldRegistry())
            root = graph.value(self.hooks)
            return root, graph.finish()

        active = stored_flight()
        flight, = self.hooks.flights.values()
        flight.last_tick += 1
        self.assertNotEqual(stored_flight(), active)
        flight.last_tick -= 1
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E0000000000006]))
        self.assertFalse(self.hooks.flights)
        self.assertFalse(self.hooks.controlled(self.a.body))


class WidowmakerChainHookBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_consumes_hook_damage_without_pull(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[ROADHOG]))
        self.b.mover.position[:] = (0, 0, 10)
        self.b.mover.body = None
        self.activate()
        self.step(enemy=HOOK)
        self.idle(70)
        fight = self.run.match.combat
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 170)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(tuple(self.a.mover.position), (0, 0, 0))
