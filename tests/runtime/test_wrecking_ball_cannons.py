"""Target-build Quad Cannons through the shared body and combat paths."""

import math
import unittest
from unittest.mock import patch

from ow174.game import combat, weapon_spread
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run, aim

WRECKING_BALL = 0x02E00000000001CA
QUAD_CANNONS = 0x1514
AMMO = 53
SPREAD = 231
HUD_SPREAD = 243
RELOAD = 0x400
ROLL = 0x08


class WreckingBallCannonsTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(KANEZAKA_DM, ("Wrecking Ball", "Target"))
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.ball, self.victim = self.run.players
        self.assertTrue(self.match.switch_hero(self.ball, heroes()[WRECKING_BALL]))
        x, y, z = self.ball.mover.position
        self.victim.mover.position = [x, y, z + 2.0]
        self.fight = self.match.combat
        self.yaw, self.pitch = aim(self.fight._eye(self.ball), (x, y + 1.1, z + 2.0))
        self.advance(24)
        target = next(item for item in self.fight._targets() if item.owner is self.victim)
        self.vitals = self.fight.vitals_of(target)
        self.weapon = next(
            instance for instance in self.ball.body_script.component.instances.values()
            if instance.graph.index == QUAD_CANNONS
        )

    def advance(self, ticks, buttons=0):
        for _ in range(ticks):
            self.run.step({self.ball: (self.yaw, self.pitch, buttons)})

    def test_held_primary_spends_ammo_hits_and_stops_on_release(self):
        self.assertEqual(self.weapon.vars[AMMO].value(), 80)
        self.advance(9, FIRE)
        self.assertEqual((self.weapon.vars[AMMO].value(), self.vitals.total), (76, 180.0))

        self.advance(24)
        self.assertEqual((self.weapon.vars[AMMO].value(), self.vitals.total), (76, 180.0))
        self.advance(1, RELOAD)
        self.advance(160)
        self.assertEqual(self.weapon.vars[AMMO].value(), 80)

        self.advance(1, FIRE)
        self.assertEqual((self.weapon.vars[AMMO].value(), self.vitals.total), (79, 175.0))

    def test_held_primary_updates_owner_spread_and_recovers_on_release(self):
        body = self.ball.body_script
        hud = body.component.entity_var(HUD_SPREAD)
        spread = self.weapon.vars[SPREAD]
        baseline = body.snapshot()
        self.assertEqual(hud.value(), 0.0)

        self.advance(9, FIRE)
        self.assertEqual(self.weapon.vars[AMMO].value(), 76)
        self.assertAlmostEqual(spread.value(), 0.2)
        self.assertAlmostEqual(hud.value(), 0.4)
        _, keys, sent = body.delta(baseline, set(), cmfd=1, correction=False)
        self.assertIn(("var", 0, HUD_SPREAD), keys)
        self.assertAlmostEqual(sent.vars[(0, HUD_SPREAD)][0], 0.4)

        self.advance(41, FIRE)
        self.assertEqual(self.weapon.vars[AMMO].value(), 60)
        self.assertEqual((spread.value(), hud.value()), (1.0, 2.0))

        self.advance(13)
        self.assertTrue(self.weapon.state(5).active)
        self.assertEqual(hud.value(), 2.0)
        self.advance(4)
        recovering = spread.value()
        self.assertAlmostEqual(recovering, 1.0 - 4.0 * 0.064, places=3)
        self.assertAlmostEqual(hud.value(), 2.0 * recovering)

        self.advance(1, FIRE)
        self.assertAlmostEqual(spread.value(), recovering + 0.05)
        self.assertAlmostEqual(hud.value(), 2.0 * (recovering + 0.05))
        self.advance(55)
        self.assertEqual((spread.value(), hud.value()), (0.0, 0.0))

    def test_empty_magazine_reloads_and_held_fire_resumes(self):
        self.advance(200, FIRE)
        ammo = self.weapon.vars[AMMO]
        self.assertEqual(ammo.value(), 0)
        self.advance(50, FIRE)
        self.assertEqual(ammo.value(), 0)
        self.advance(40, FIRE)
        self.assertEqual(ammo.value(), 80)
        self.advance(50, FIRE)
        self.assertLess(ammo.value(), 80)
        self.assertGreater(ammo.value(), 0)

    def test_primary_keeps_rounds_until_the_guns_are_out_again(self):
        self.advance(9, FIRE)
        ammo = self.weapon.vars[AMMO]
        self.assertEqual(ammo.value(), 76)
        self.advance(1)
        self.advance(1, ROLL)
        self.advance(1)
        self.advance(8, FIRE)
        self.assertEqual(self.ball.mover.data.stand_radius, 0.5)
        self.assertEqual(ammo.value(), 76)

        self.advance(21, FIRE)
        self.assertEqual(self.ball.mover.data.stand_radius, 0.7)
        self.assertEqual(ammo.value(), 76)
        self.advance(22, FIRE)
        self.assertLess(ammo.value(), 76)

    def test_primary_uses_authored_damage_falloff(self):
        volley = self.weapon.state(9)
        hit = combat.hit_def(volley, volley.node.field("m_6396149F"))
        self.assertEqual((hit.amount, hit.falloff_start, hit.falloff_end), (5.0, 15.0, 25.0))
        self.assertAlmostEqual(hit.scale(20.0), 0.65)
        self.assertAlmostEqual(hit.scale(30.0), 0.30)

    def test_primary_combat_rays_use_authored_cone_at_each_shot(self):
        forward = combat.aim(self.yaw, self.pitch)
        with (
            patch.object(self.fight, "_hitscan", wraps=self.fight._hitscan) as hitscan,
            patch.object(weapon_spread, "seed_base", wraps=weapon_spread.seed_base) as seed_base,
        ):
            self.advance(50, FIRE)
        rays = [call.args[3] for call in hitscan.call_args_list]
        self.assertEqual(len(rays), 20)
        self.assertEqual(len(set(rays)), 20)
        volley = self.weapon.state(9)
        self.assertEqual(volley.node.pos, 24)
        self.assertEqual([call.args[2] for call in seed_base.call_args_list], [9] * 20)
        for number, ray in enumerate(rays, 1):
            dot = math.fsum(a * b for a, b in zip(forward, ray, strict=True))
            self.assertGreater(dot, math.cos(math.radians(number * 0.1)) - 1e-7)
        self.assertLess(min(math.fsum(a * b for a, b in zip(forward, ray, strict=True))
                            for ray in rays), math.cos(math.radians(1.0)))
        self.assertEqual((self.weapon.vars[AMMO].value(), self.vitals.total), (60, 100.0))
