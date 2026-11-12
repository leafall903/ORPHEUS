"""Actual scoped cone/pellet authority; native caller identities remain declared policy."""

import math
import os
import unittest
from unittest.mock import patch

from test_game_combat import aim
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch
from test_widowmaker_npcs import _ActualNpcMatch

from ow174.game import combat, npc_control, weapon_launch
from ow174.game.collision import World


class WidowmakerSpreadTests(_WidowmakerMatch, unittest.TestCase):
    def test_actual_primary_reports_ramp_cones_and_spend_one_ammo_per_ray(self):
        self.b.spawned = False
        observed = []
        original = weapon_launch.directions

        def capture(actor, state, forward, number, time, activation):
            result = original(actor, state, forward, number, time, activation)
            if actor is self.a and state.node.pos == 9:
                observed.append((state.spread_at(time - state.start - state.offset), forward, result[0]))
            return result

        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        with patch.object(weapon_launch, 'directions', capture):
            self.press(FIRE, 20)
        self.assertGreaterEqual(len(observed), 3)
        self.assertEqual(ammo - weapon.vars[53].value(), len(observed))
        self.assertEqual([round(x[0] * 3, 5) for x in observed[:3]], [1, 2, 3])
        for normalized, forward, ray in observed:
            self.assertNotEqual(ray, forward)
            dot = sum(a*b for a, b in zip(forward, ray, strict=True))
            norm = math.sqrt(sum(x*x for x in ray))
            angle = math.degrees(math.acos(max(-1, min(1, dot / norm))))
            self.assertLessEqual(angle, normalized * 3 + 1e-5)

    def test_scoped_zero_angle_retains_exact_command_ray(self):
        observed = []
        original = weapon_launch.directions

        def capture(actor, state, forward, number, time, activation):
            result = original(actor, state, forward, number, time, activation)
            if actor is self.a and state.node.pos == 19:
                observed.append((forward, result))
            return result

        with patch.object(weapon_launch, 'directions', capture):
            self.press(SCOPE, 100)
            self.press(SCOPE | FIRE)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][1], (observed[0][0],))
        self.assertEqual(self.health(self.b), 80)

    def test_real_primary_spread_can_hit_an_off_axis_body_missed_by_center_ray(self):
        self.b.mover.position[:] = (0.65, 0, 60)
        self.b.mover.body = None
        self.b.mover.history.clear()
        fight = self.run.match.combat
        fight.history.pop(self.b.body, None)
        self.run.step({})
        direction = aim(fight._eye(self.a), (0, 1, 60))
        target = self.target(self.b)
        self.assertIsNone(combat.ray_capsule(fight._eye(self.a), combat.aim(*direction),
                                           *target.segment(), target.radius))
        self.run.step({self.a: (*direction, FIRE)})
        self.assertLess(self.health(self.b), 200)
        self.assertTrue(self.a.client.messages(combat.HIT))

    def test_actual_primary_spread_can_clear_cover_that_blocks_center_ray(self):
        self.b.mover.position[:] = (0, 0, 10)
        self.b.mover.body = None
        self.b.mover.history.clear()
        fight = self.run.match.combat
        fight.history.pop(self.b.body, None)
        self.run.step({})
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-5, 0, 5, 5, 0, 5, 5, 1.525, 5),
            (-5, 0, 5, 5, 1.525, 5, -5, 1.525, 5),
        ])
        self.assertIsNotNone(combat.ray_world(self.run.match.collision_world,
                                              fight._eye(self.a), (0, 0, 1), 10))
        self.run.step({self.a: (0, 0, FIRE)})
        self.assertLess(self.health(self.b), 200)


@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_TEST_DATA'), 'requires actual NPC bundle')
class EnforcerPelletTests(_ActualNpcMatch, unittest.TestCase):
    def first_volley(self):
        self.b.spawned = False
        bot = self.spawn(self.catalog['2'], (0, 0, 3))
        fight = self.run.match.combat
        self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3))
        npc_control.start(self.run.match, self.a)
        for _ in range(180):
            self.run.step({})
            rockets = [r for r in fight.rockets if r.shooter is bot]
            if rockets:
                return bot, rockets
        self.fail('actual Enforcer volley did not launch')

    def test_one_authored_volley_launches25_rays_and_spends_one_round(self):
        bot, rockets = self.first_volley()
        self.assertEqual(len(rockets), 25)
        self.assertEqual(len({r.direction for r in rockets}), 25)
        self.assertTrue(all(r.shot is None for r in rockets))  # authored SHOTS_ONLY sync
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(weapon.vars[53].value(), 3)
        for _ in range(4):
            self.run.step({})
        self.assertEqual(self.health(self.a), 125)

    def test_wall_intercepts_all25_already_launched_projectiles(self):
        bot, rockets = self.first_volley()
        self.assertEqual(len(rockets), 25)
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-5, 0, 1.5, 5, 0, 1.5, 5, 4, 1.5),
            (-5, 0, 1.5, 5, 4, 1.5, -5, 4, 1.5),
        ])
        for _ in range(10):
            self.run.step({})
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(any(r.shooter is bot for r in self.run.match.combat.rockets))
