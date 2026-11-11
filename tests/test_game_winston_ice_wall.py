"""Actual Mei Ice Wall input through Tesla and Heavy charge."""

import unittest

import test_game_winston_frost as fixtures
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.content import heroes


class WinstonIceWallTests(unittest.TestCase):
    setUp = fixtures.WinstonFrostTests.setUp
    move = staticmethod(fixtures.WinstonFrostTests.move)
    health = fixtures.WinstonFrostTests.health
    step = fixtures.WinstonFrostTests.step

    def deploy(self):
        self.move(self.caster, (0, 1, 6))
        angle = aim(self.fight._eye(self.caster), (0, 1, 3))
        for button in (16, 0, 1, 0):
            self.run.step({self.caster: (*angle, button)})
        pieces = self.fight.walls.pieces
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.impact_marked for piece in pieces.values()))
        return min(pieces.values(), key=lambda piece: abs(piece.feet[0]))

    def test_tesla_damages_hostile_pillar_and_reaches_mei_after_expiry(self):
        centre = self.deploy()
        before = self.health(self.caster)
        for _ in range(15):
            self.run.step({self.owner: (0, 0, 1)})
        self.assertEqual(self.health(self.caster), before)
        self.assertAlmostEqual(centre.health, 400 - 15 * .96)
        self.run.frame = centre.expires
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(self.fight.walls.pieces)
        self.run.step({self.owner: (0, 0, 1)})
        self.assertLess(self.health(self.caster), before)

    def test_owner_cancels_ice_wall_and_early_recast_cannot_deploy(self):
        self.deploy()
        angle = aim(self.fight._eye(self.caster), (0, 1, 3))
        for button in (16, 0, 16, 0, 1):
            self.run.step({self.caster: (*angle, button)})
        self.assertFalse(self.fight.walls.pieces)

    def test_breaking_tesla_tick_does_not_spill_through_pillar(self):
        centre = self.deploy()
        centre.health = .5
        charge = self.owner.body_script.component.entity_var(342)
        before = self.health(self.caster)
        self.run.step({self.owner: (0, 0, 1)})
        self.assertNotIn(centre.entity, self.fight.walls.pieces)
        self.assertEqual(self.health(self.caster), before)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.owner.stats.value(stats.HERO_DAMAGE), 0)
        self.run.step({self.owner: (0, 0, 1)})
        self.assertAlmostEqual(before - self.health(self.caster), .96)
        self.assertAlmostEqual(charge.value(), .96)
        self.assertAlmostEqual(self.owner.stats.value(stats.HERO_DAMAGE), .96)

    def test_friendly_pillar_blocks_tesla_without_losing_health(self):
        self.caster.team = self.owner.team
        self.ally.team = 1
        self.move(self.ally, (0, 1, 6))
        centre = self.deploy()
        before = self.health(self.ally)
        self.run.step({self.owner: (0, 0, 1)})
        self.assertEqual(centre.health, 400)
        self.assertEqual(self.health(self.ally), before)

    def test_breaking_helix_impact_keeps_pillar_occlusion_for_its_splash(self):
        self.caster.team = self.owner.team
        self.ally.team = 1
        centre = self.deploy()
        centre.health = 1
        self.move(self.owner, (0, 1, 1.5))
        self.move(self.ally, (0, 1, 6))
        self.move(self.caster, (12, 1, 0))
        before = self.health(self.owner)
        yaw, pitch = aim(self.fight._eye(self.ally), self.fight._eye(self.owner))
        self.run.step({self.ally: (yaw, pitch, 2)})
        for _ in range(35):
            self.run.step({})
            if centre.entity not in self.fight.walls.pieces:
                break
        self.assertNotIn(centre.entity, self.fight.walls.pieces)
        self.assertEqual(self.health(self.owner), before)

    def test_melee_and_primal_hit_pillar_without_reaching_enemy_behind_it(self):
        for primal, button, damage in ((False, 0x800, 30), (True, 1, 40)):
            with self.subTest(primal=primal):
                if primal:
                    self.setUp()
                centre = self.deploy()
                self.move(self.owner, (0, 1, 1.5))
                self.move(self.caster, (0, 1, 3.65))
                script = self.owner.body_script
                charge = script.component.entity_var(342)
                if primal:
                    script.set_ult_charge(script.component.entity_var(338).value())
                    self.run.step({self.owner: (0, 0, 32)})
                    for _ in range(35):
                        self.run.step({})
                before = self.health(self.caster)
                for frame in range(70):
                    self.run.step({self.owner: (0, 0, button if primal or frame == 0 else 0)})
                    if centre.health < 400:
                        break
                self.assertEqual(centre.health, 400 - damage)
                self.assertEqual(self.health(self.caster), before)
                self.assertEqual(self.caster.mover.velocity, (0, 0, 0))
                self.assertEqual(charge.value(), 0)
                self.assertEqual(self.owner.stats.value(stats.HERO_DAMAGE), 0)
                self.run.step({self.caster: (0, 0, 16)})
                self.assertFalse(self.fight.walls.pieces)
                for _ in range(65):
                    self.run.step({})
                for frame in range(70):
                    self.run.step({self.owner: (0, 0, button if primal or frame == 0 else 0)})
                    if self.health(self.caster) < before:
                        break
                self.assertEqual(before - self.health(self.caster), damage)
                self.assertEqual(charge.value(), 0 if primal else damage)
                self.assertEqual(self.owner.stats.value(stats.HERO_DAMAGE), damage)

    def test_jump_takeoff_and_landing_damage_pillar_and_block_body_impulse(self):
        centre = self.deploy()
        self.move(self.owner, (0, 1, 1.5))
        self.move(self.caster, (0, 1, 3.8))
        charge = self.owner.body_script.component.entity_var(342)
        before = self.health(self.caster)
        self.run.step({self.owner: (0, -16384, 8)})
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertEqual(centre.health, 399)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.owner.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(235):
            self.run.step({})
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertLess(centre.health, 399)
        self.assertGreater(centre.health, 349)
        self.assertEqual(self.health(self.caster), before)
        self.assertEqual(self.caster.mover.velocity, (0, 0, 0))
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_wall_stops_winston_walking_and_cancel_reopens_path(self):
        centre = self.deploy()
        for _ in range(75):
            self.step(forward=127)
        face = centre.feet[2] - centre.depth
        self.assertLess(self.owner.mover.position[2], face)
        self.assertGreater(self.owner.mover.position[2], face - .8)
        self.run.step({self.caster: (0, 0, 16)})
        for _ in range(35):
            self.step(forward=127)
        self.assertGreater(self.owner.mover.position[2], centre.feet[2] + centre.depth)

    def test_jump_pack_lands_on_wall_top_and_removal_does_not_replay_impact(self):
        self.move(self.owner, (0, 1, 3))
        self.run.step({self.owner: (0, -16384, 8)})
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        for _ in range(75):
            self.run.step({})
            if self.owner.mover.position[1] > 6:
                break
        self.assertGreater(self.owner.mover.position[1], 6)
        centre = self.deploy()
        top = centre.feet[1] + centre.height
        for _ in range(240):
            self.run.step({})
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertAlmostEqual(self.owner.mover.position[1], top, delta=.03)
        remaining = centre.health
        self.assertAlmostEqual(remaining, 350, delta=.001)
        for _ in range(12):
            self.run.step({})
        self.assertAlmostEqual(self.owner.mover.position[1], top, delta=.03)
        self.assertEqual(centre.health, remaining)
        self.run.step({self.caster: (0, 0, 16)})
        for _ in range(90):
            self.run.step({})
        self.assertAlmostEqual(self.owner.mover.position[1], 1., delta=.03)
        self.assertFalse(self.owner.mover.airborne)
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)

    def test_wall_placement_lifts_winston_into_stable_grounded_pose(self):
        self.move(self.owner, (0, 1, 3))
        centre = self.deploy()
        for _ in range(20):
            self.run.step({})
        self.assertAlmostEqual(self.owner.mover.position[1], centre.feet[1] + centre.height, delta=.03)
        self.assertFalse(self.owner.mover.airborne)
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertEqual(centre.health, 400)

    def test_flashbang_cancels_preview_before_confirmation(self):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[0x02E0000000000042]))
        self.move(self.caster, (0, 1, 6))
        self.move(self.ally, (0, 1, 4))
        place = aim(self.fight._eye(self.caster), (0, 1, 3))
        flash = aim(self.fight._eye(self.ally), (0, 2, 6))
        self.run.step({self.caster: (*place, 16)})
        self.assertTrue(self.fight.walls.inputs[self.caster.body].preview)
        self.run.step({self.ally: (*flash, 16)})
        for _ in range(40):
            self.run.step({})
            if self.fight.is_stunned(self.caster):
                break
        self.assertTrue(self.fight.is_stunned(self.caster))
        self.assertFalse(self.fight.walls.inputs[self.caster.body].preview)
        self.run.frame += 65
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.run.step({self.caster: (*place, 1)})
        self.assertFalse(self.fight.walls.pieces)
