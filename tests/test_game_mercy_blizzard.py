"""Actual Mei Blizzard launches and exposures against Mercy."""

import unittest

import test_game_mercy_frost as frost
import test_game_mercy_matrix as matrix_tests
import test_game_mercy_resurrect as resurrect_tests
from test_game_combat import aim
from test_game_mover import FLOOR, wall

from ow174.game import cryo, defense_matrix, mei_blizzard
from ow174.game.collision import World
from ow174.game.combat import TICK
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY


class MercyBlizzardTests(unittest.TestCase):
    def encounter(self):
        helper = frost.MercyFrostTests()
        f, health = helper.encounter()
        self.addCleanup(helper.doCleanups)
        f.enemy.body_script.ult_seconds = 0
        return f, health

    def launch(self, f, target=None):
        x, y, z = (target or f.mercy).mover.position
        direction = aim(f.fight._eye(f.enemy), (x, y, z))
        for frame in range(32):
            f.run.step({f.enemy: (*direction, 32 if frame == 0 else 0)})

    def land(self, f):
        for _ in range(120):
            if f.fight.blizzard.areas:
                break
            f.run.step({})
        area, = f.fight.blizzard.areas.values()
        return area

    def test_live_blizzard_starts_exposure_when_same_body_revival_protection_ends(self):
        res = resurrect_tests.ResurrectTests()
        res.setUp(ally_hero=MERCY)
        self.addCleanup(res.doCleanups)
        self.assertTrue(res.match.switch_hero(res.enemy, heroes()[cryo.HERO]))
        res.enemy.mover.position[:] = (4, 1, 3)
        res.enemy.mover.body = None
        script = res.enemy.body_script
        script.ult_seconds = 0
        body = res.ally.body
        res.cast()
        health = res.fight.vitals_of(res.target)
        caster = res.fight.vitals_of(next(t for t in res.fight._targets() if t.owner is res.mercy))
        protection_end = health.invulnerable_until

        script.set_ult_charge(script.component.entity_var(338).value())
        self.launch(res, res.ally)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        area = self.land(res)
        while (res.run.frame + 1) * TICK < protection_end:
            res.run.step({})
            self.assertEqual(health.total, 200)
            self.assertFalse(health.frost_sources)
            self.assertNotIn(body, area.frost_due)
        self.assertLess(res.fight.now, protection_end)
        self.assertLess(caster.total, 200)

        res.run.step({})
        self.assertGreaterEqual(res.fight.now, protection_end)
        self.assertEqual(res.ally.body, body)
        self.assertIs(res.fight.blizzard.areas[area.entity], area)
        self.assertAlmostEqual(health.total, 200 - mei_blizzard.DAMAGE_RATE * TICK)
        self.assertEqual(res.fight.frost_progress(res.ally), mei_blizzard.FROST_INITIAL)
        self.assertIn(body, area.frost_due)
        self.assertFalse(res.fight.is_stunned(res.ally))

        while res.run.frame * mei_blizzard.FRAME_MS < area.ends:
            res.run.step({})
            if res.fight.is_stunned(res.ally):
                break
        self.assertEqual(res.fight.frost_progress(res.ally), 1)
        self.assertLess(res.run.frame * mei_blizzard.FRAME_MS, area.ends)

    def test_authored_cast_ground_delay_damage_freeze_and_end(self):
        f, health = self.encounter()
        self.launch(f)
        self.assertFalse(f.fight.blizzard.flights)
        self.assertEqual(health.total, 200)
        script = f.enemy.body_script
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1610)
        script.set_ult_charge(cost)
        self.launch(f)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(len(f.fight.blizzard.flights), 1)
        area = self.land(f)
        self.assertEqual(health.total, 200)
        self.assertEqual(area.ends - area.starts, 4250)
        while (f.run.frame + 1) * 16 <= area.starts:
            f.run.step({})
        self.assertEqual(health.total, 200)
        for _ in range(210):
            f.run.step({})
            if f.fight.frost_progress(f.mercy) == 1:
                break
        self.assertLess(health.total, 160)
        self.assertTrue(f.fight.is_stunned(f.mercy))
        position = tuple(f.mercy.mover.position)
        for _ in range(20):
            frost.MercyFrostTests.walk(f, buttons=1 | 8 | 16 | 32)
        self.assertEqual(tuple(f.mercy.mover.position), position)
        self.assertEqual(f.mercy.body_script.buttons, 0)
        while f.run.frame * 16 < area.ends:
            f.run.step({})
        self.assertFalse(f.fight.blizzard.areas)
        self.assertAlmostEqual(health.total, 115, places=4)
        for _ in range(100):
            f.run.step({})
        self.assertFalse(f.fight.is_stunned(f.mercy))
        self.assertGreater(health.total, 115)

    def test_friendly_mercy_ignores_actual_blizzard(self):
        f, health = self.encounter()
        f.enemy.team = f.mercy.team
        script = f.enemy.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.launch(f)
        area = self.land(f)
        while f.run.frame * 16 < area.ends:
            f.run.step({})
        self.assertEqual(health.total, 200)
        self.assertEqual(f.fight.frost_progress(f.mercy), 0)


    def test_static_wall_blocks_area_and_uncovering_mercy_restores_exposure(self):
        f, health = self.encounter()
        script = f.enemy.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.launch(f)
        area = self.land(f)
        x, y, z = area.position
        f.mercy.mover.position[:] = (x + 4, y, z)
        f.mercy.mover.body = None
        clear_world = f.match.collision_world
        blocked_world = World(FLOOR + wall(x + 2, z - 4, x + 2, z + 4))
        f.match.collision_world = blocked_world
        f.mercy.mover.use(world=blocked_world)
        for _ in range(55):
            f.run.step({})
        self.assertEqual(health.total, 200)
        self.assertEqual(f.fight.frost_progress(f.mercy), 0)
        f.match.collision_world = clear_world
        f.mercy.mover.use(world=clear_world)
        for _ in range(30):
            f.run.step({})
        self.assertLess(health.total, 200)
        self.assertGreaterEqual(f.fight.frost_progress(f.mercy), 0.5)


    def test_actual_matrix_absorbs_launched_blizzard_before_area_creation(self):
        if not defense_matrix.absorbable(mei_blizzard.DEFINITION):
            self.skipTest("prepared 104319 Blizzard projectile tags required")
        helper = matrix_tests.MercyMatrixTests()
        run, mei, dva, matrix, health = helper.scenario()
        self.addCleanup(helper.doCleanups)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        mei.mover.position[:] = (0, 1, 5)
        mei.mover.body = None
        for _ in range(45):
            run.step({})
        pose = (0, 0, SECONDARY)
        for _ in range(6):
            run.step({dva: pose})
        self.assertTrue(matrix.state(3).active)
        script = mei.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        direction = aim(fight._eye(mei), (0, 1, 0))
        for frame in range(32):
            run.step({mei: (*direction, 32 if frame == 0 else 0), dva: pose})
        self.assertEqual(len(fight.blizzard.flights), 1)
        for _ in range(45):
            run.step({dva: pose})
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        self.assertEqual(health.total, 600)
