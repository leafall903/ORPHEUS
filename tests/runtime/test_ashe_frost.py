"""Live build 104319 Endothermic Blaster frost against Ashe."""

import os
import unittest
from pathlib import Path

import test_ashe_bob as bob_fixtures
import test_ashe_combat as fixtures
import test_ashe_controls as controls
import test_ashe_hack as hack_fixtures
import test_mccree_flashbang as flash_fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat, mei_frost, sombra_hack
from ow174.game.content import HEROES_PATH, find_hero
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires build 104319 data")
class AsheFrostTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    step = flash_fixtures.FlashbangTests.step

    def arena(self, *, distance=5, ally=False):
        run, ashe, mei = self.fight(distance=distance, team_game=ally)
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        mei.mover.position[:] = (0, ashe.mover.position[1], distance)
        if ally:
            mei.team = ashe.team
        for _ in range(50):
            run.step({})
        for target in run.match.combat._targets():
            run.match.combat.vitals_of(target)
        return run, mei, ashe

    def freeze(self, run, mei, ashe):
        fight = run.match.combat
        body = combat._source_entity(ashe)
        for _ in range(120):
            self.step(run, mei, ashe, cast=1)
            if body in fight.freezes:
                return fight.freezes[body]
        self.fail("actual Endothermic Blaster did not freeze its target")

    def test_single_spray_hit_slows_movement_then_expires(self):
        run, mei, ashe = self.arena()
        fight = run.match.combat
        self.step(run, mei, ashe, cast=1)
        for _ in range(20):
            self.step(run, mei, ashe)
        self.assertEqual(fight.vitals[ashe.body].total, 197.25)
        progress, touched, stacking = fight.frost_hits[ashe.body][(mei.body, 0)]
        self.assertFalse(stacking)
        self.assertAlmostEqual(progress, 0.2)
        self.assertFalse(fight.is_stunned(ashe))

        start = tuple(ashe.mover.position)
        for _ in range(10):
            self.step(run, mei, ashe, forward=127)
        self.assertGreater(ashe.mover.position[2], start[2])
        self.assertAlmostEqual(ashe.mover.values.run_forward, ashe.mover.data.run_forward * 0.8, places=5)
        while run.frame < touched + 64:
            self.step(run, mei, ashe)
        self.assertNotIn(ashe.body, fight.frost_hits)
        self.assertEqual(ashe.mover.values.run_forward, ashe.mover.data.run_forward)

    def test_thirtieth_hit_freezes_aim_movement_and_abilities_until_thaw(self):
        run, mei, ashe = self.arena()
        fight = run.match.combat
        deadline, yaw, pitch = self.freeze(run, mei, ashe)
        self.assertEqual(fight.vitals[ashe.body].total, 200 - 30 * 2.75)
        self.assertEqual(deadline - run.frame, 82)
        script = ashe.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        weapon = next(instance for instance in script.component.instances.values()
                      if instance.graph.index == 0x14FC)
        position = tuple(ashe.mover.position)
        buttons = 1 | 2 | 8 | 16 | 32 | 0x400 | 0x800
        for _ in range(20):
            self.step(run, mei, ashe, cast=1, target_buttons=buttons, forward=127)
        self.assertLess(fight.vitals[ashe.body].total, 200 - 30 * 2.75)
        self.assertEqual(fight.freezes[ashe.body], (deadline, yaw, pitch))

        while run.frame < deadline - 1:
            run.step({ashe: (10000, 500, buttons)})
        self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), (yaw, pitch))
        self.assertEqual(tuple(ashe.mover.position), position)
        self.assertEqual(weapon.vars[53].value(), 12)
        self.assertEqual(fight.vitals[mei.body].total, 250)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(fight.dynamite.grenades)
        self.assertFalse(fight.companions.active)
        self.assertTrue(fight.is_stunned(ashe))

        run.step({ashe: (10000, 500, 32)})
        self.assertNotIn(ashe.body, fight.freezes)
        self.assertFalse(fight.is_stunned(ashe))
        self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), (10000, 500))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_team_range_and_terrain_exclude_spray_damage_and_frost(self):
        for case in ("ally", "range", "terrain"):
            with self.subTest(case=case):
                run, mei, ashe = self.arena(distance=15 if case == "range" else 5, ally=case == "ally")
                if case == "terrain":
                    run.match.collision_world = wall_scene(2.5)
                for _ in range(35):
                    self.step(run, mei, ashe, cast=1)
                fight = run.match.combat
                self.assertEqual(fight.vitals[ashe.body].total, 200)
                self.assertNotIn(ashe.body, fight.frost_hits)
                self.assertNotIn(ashe.body, fight.freezes)

    def test_switch_retires_freeze_and_new_body_accepts_weapon_commands(self):
        run, mei, ashe = self.arena()
        fight = run.match.combat
        self.freeze(run, mei, ashe)
        old_body = ashe.body
        hero = ashe.hero
        self.assertTrue(run.match.switch_hero(ashe, find_hero("soldier")))
        self.assertTrue(run.match.switch_hero(ashe, hero))
        self.assertNotEqual(ashe.body, old_body)
        self.assertNotIn(old_body, fight.freezes)
        self.assertFalse(fight.is_stunned(ashe))
        ashe.mover.position[:] = (0, mei.mover.position[1], 0)
        for _ in range(50):
            self.step(run, mei, ashe)
        before = fight.vitals[mei.body].total
        self.step(run, mei, ashe, target_buttons=1)
        self.assertLess(fight.vitals[mei.body].total, before)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_spray_penetrates_bob_and_pauses_his_active_gun_until_thaw(self):
        run, ashe, mei, actor = bob_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        mei.mover.position[:] = (0, 1, 7)
        mei.mover.body = None
        fight = run.match.combat
        while run.frame < actor.ready + 12:
            run.step({})
        self.assertGreater(actor.shots, 0)
        deadline, _, _ = self.freeze(run, mei, actor.bot)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200 - 30 * 2.75)
        self.assertLess(fight.vitals[ashe.body].total, 200)
        shots = actor.shots
        health = fight.vitals[mei.body].total
        expiry = actor.until
        while run.frame < deadline - 1:
            self.step(run, mei, actor.bot)
            self.assertEqual(actor.shots, shots)
        self.assertEqual(fight.vitals[mei.body].total, health)
        self.step(run, mei, actor.bot)
        self.assertEqual(actor.shots, shots + 1)
        self.assertEqual(fight.vitals[mei.body].total, health - 14)
        self.assertEqual(actor.until, expiry)
        self.assertNotIn(actor.bot.entity, fight.freezes)

    def test_projected_barrier_absorbs_spray_without_frost(self):
        barrier = controls.AsheBarrierTests()
        run, mei, zarya, ashe = barrier.projected_arena()
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        self.assertTrue(run.match.switch_hero(ashe, find_hero("ashe")))
        zarya.team = ashe.team = 1 - mei.team
        for player, x, z in ((mei, 0, 0), (zarya, 4, 5), (ashe, 0, 5)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(35):
            run.step({})
        direction, projected = barrier.projected_input(run, zarya, ashe)
        run.step({zarya: (*direction, 16)})
        self.assertTrue(projected.states[10].active)
        self.step(run, mei, ashe, cast=1)
        for _ in range(20):
            self.step(run, mei, ashe)
        fight = run.match.combat
        self.assertEqual(fight.personal_barriers[ashe.body][2], 197.25)
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        self.assertNotIn(ashe.body, fight.frost_hits)

    def test_explicit_spray_rate_minimum_and_duration_change_actual_freeze(self):
        run, mei, ashe = self.arena()
        fight = run.match.combat
        component = mei.body_script.component
        component.set(mei_frost.MINIMUM, 0.6)
        component.set(mei_frost.RATE, 5.0)
        component.set(mei_frost.DURATION, 0.5)
        self.step(run, mei, ashe, cast=1)
        for _ in range(20):
            self.step(run, mei, ashe)
        self.assertEqual(fight.vitals[ashe.body].total, 197.25)
        self.assertAlmostEqual(fight.frost_hits[ashe.body][(mei.body, 0)][0], 0.6)
        deadline, _, _ = self.freeze(run, mei, ashe)
        self.assertEqual(fight.vitals[ashe.body].total, 200 - 4 * 2.75)
        self.assertEqual(deadline - run.frame, 41)
        while run.frame < deadline - 1:
            self.step(run, mei, ashe, target_buttons=1)
        self.assertTrue(fight.is_stunned(ashe))
        before = fight.vitals[mei.body].total
        self.step(run, mei, ashe, target_buttons=1)
        self.assertFalse(fight.is_stunned(ashe))
        self.assertLess(fight.vitals[mei.body].total, before)

    def test_zero_spray_rate_retains_minimum_and_never_freezes(self):
        run, mei, ashe = self.arena()
        fight = run.match.combat
        mei.body_script.component.set(mei_frost.RATE, 0.0)
        for _ in range(112):
            self.step(run, mei, ashe, cast=1)
        self.assertLess(fight.vitals[ashe.body].total, 200 - 30 * 2.75)
        self.assertAlmostEqual(fight.frost_hits[ashe.body][(mei.body, 0)][0], 0.2)
        self.assertNotIn(ashe.body, fight.freezes)

    def test_thaw_restores_rifle_while_overlapping_hack_still_blocks_abilities(self):
        run, sombra, mei, ashe = controls.AsheBarrierTests().projected_arena()
        for player, hero in ((sombra, "sombra"), (mei, "mei"), (ashe, "ashe")):
            self.assertTrue(run.match.switch_hero(player, find_hero(hero)))
        sombra.team = mei.team = 1 - ashe.team
        for player, x, z in ((sombra, 2, 4), (mei, -2, 4), (ashe, 0, 0)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(50):
            run.step({})
        fight = run.match.combat
        receiver = hack_fixtures.AsheHackTests().cast(run, sombra, ashe)
        deadline, _, _ = self.freeze(run, mei, ashe)
        script = ashe.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        hack_end = (receiver.start + receiver.ms) // script.frame_ms
        self.assertLess(deadline + 1, hack_end)
        yaw, pitch = aim(fight._eye(ashe), fight._eye(sombra))
        target = next(item for item in fight._targets() if item.owner is sombra)
        health = fight.vitals_of(target).total
        for _ in range(20):
            run.step({ashe: (yaw, pitch, 1 | 8 | 16 | 32)})
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertTrue(fight.is_stunned(ashe))
        while run.frame < deadline - 1:
            run.step({})
        run.step({ashe: (yaw, pitch, 1 | 8 | 16 | 32)})
        self.assertFalse(fight.is_stunned(ashe))
        self.assertTrue(sombra_hack.locked(ashe))
        self.assertLess(fight.vitals_of(target).total, health)
        self.assertFalse(fight.dynamite.grenades or fight.companions.active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        while run.frame <= hack_end:
            run.step({})
        run.step({ashe: (yaw, pitch, 32)})
        self.assertFalse(sombra_hack.locked(ashe))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_already_thrown_dynamite_fuses_and_burns_during_freeze(self):
        run, ashe, mei, enemy = controls.AsheBarrierTests().projected_arena()
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        mei.team = enemy.team = 1 - ashe.team
        for player, x, z in ((ashe, 0, 0), (mei, 4, 0), (enemy, 0, 16.4)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(50):
            run.step({})
        fight = run.match.combat
        for i in range(20):
            run.step({ashe: (0, 0, 16 if i < 5 else 0)})
        grenade, = fight.dynamite.grenades
        while run.frame < grenade.fuse - 40:
            run.step({})
        mei.body_script.component.set(mei_frost.MINIMUM, 1.0)
        deadline, _, _ = self.freeze(run, mei, ashe)
        self.assertLess(run.frame, grenade.fuse)
        self.assertGreater(deadline, grenade.fuse)
        target = next(item for item in fight._targets() if item.owner is enemy)
        vitals = fight.vitals_of(target)
        before = vitals.total
        while run.frame < grenade.fuse - 1:
            run.step({})
        self.assertEqual(vitals.total, before)
        run.step({})
        self.assertTrue(fight.is_stunned(ashe))
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(vitals.total, before - 50)
        burn = fight.dynamite.burns[(ashe.body, enemy.body)]
        self.assertEqual(burn.start, grenade.fuse)
        self.assertLess(burn.next_damage, deadline)
        while run.frame < burn.next_damage - 1:
            run.step({})
        run.step({})
        self.assertTrue(fight.is_stunned(ashe))
        self.assertEqual(vitals.total, before - 55)
