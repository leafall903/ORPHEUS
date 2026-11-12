"""Real 064A Blizzard against Ashe and her companion."""

import os
import unittest
from pathlib import Path

import test_ashe_bob as bob_fixtures
import test_ashe_controls as controls
import test_ashe_frost as frost_fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat, mei_frost
from ow174.game.content import HEROES_PATH, find_hero
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires build 104319 data")
class AsheBlizzardTests(unittest.TestCase):
    arena = frost_fixtures.AsheFrostTests.arena
    fight = frost_fixtures.AsheFrostTests.fight

    def launch(self, run, mei, point):
        fight = run.match.combat
        script = mei.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        yaw, pitch = aim(fight._eye(mei), point)
        for i in range(31):
            run.step({mei: (yaw, pitch, 32 if i == 0 else 0)})
            self.assertFalse(fight.blizzard.flights)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        run.step({mei: (yaw, pitch, 0)})
        flight, = fight.blizzard.flights
        return flight

    def landed(self, run):
        for _ in range(160):
            areas = run.match.combat.blizzard.areas
            if areas:
                area, = areas.values()
                return area
            run.step({})
        self.fail("Blizzard did not create a landed area")

    def finish(self, run, area):
        while run.frame * 16 < area.ends:
            run.step({})
        self.assertFalse(run.match.combat.blizzard.areas)

    def test_actual_cast_damage_freeze_and_area_expiry(self):
        run, mei, ashe = self.arena()
        fight = run.match.combat
        self.assertEqual(mei.body_script.component.entity_var(ULT_COST).value(), 1610)
        run.step({mei: (-32768, 3135, 32)})
        for _ in range(40):
            run.step({})
        self.assertFalse(fight.blizzard.flights or fight.blizzard.areas)

        flight = self.launch(run, mei, tuple(ashe.mover.position))
        self.assertEqual(flight.left, 60)
        self.assertAlmostEqual(flight.gravity, 9.8, places=6)
        self.assertAlmostEqual(sum(axis * axis for axis in flight.velocity) ** 0.5, 20)
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        area = self.landed(run)
        self.assertEqual(area.starts - run.frame * 16, 200)
        self.assertEqual(area.ends - area.starts, 4250)
        while run.frame * 16 <= area.starts:
            self.assertEqual(fight.vitals[ashe.body].total, 200)
            run.step({})
        self.assertEqual(fight.frost_hits[ashe.body][(mei.body, area.entity)][0], 0.5)

        while ashe.body not in fight.freezes and run.frame * 16 < area.ends:
            run.step({})
        deadline, yaw, pitch = fight.freezes[ashe.body]
        self.assertEqual(deadline - run.frame, 82)
        position = tuple(ashe.mover.position)
        script = ashe.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        for _ in range(15):
            run.step({ashe: (9000, 500, 1 | 8 | 16 | 32)})
        self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), (yaw, pitch))
        self.assertEqual(tuple(ashe.mover.position), position)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(fight.dynamite.grenades or fight.companions.active)
        self.assertEqual(fight.freezes[ashe.body][0], deadline)
        self.finish(run, area)
        self.assertAlmostEqual(fight.vitals[ashe.body].total, 115)
        self.assertNotIn(ashe.body, fight.freezes)

    def test_ally_range_and_terrain_keep_ashe_out_of_both_effects(self):
        for case in ("ally", "range", "terrain"):
            with self.subTest(case=case):
                run, mei, ashe = self.arena(distance=20 if case == "range" else 5, ally=case == "ally")
                x, y, z = mei.mover.position
                if case == "terrain":
                    run.match.collision_world = wall_scene(2.5)
                self.launch(run, mei, (x, y, z - 5))
                area = self.landed(run)
                if case == "terrain":
                    self.assertAlmostEqual(area.position[2], 2.5)
                self.finish(run, area)
                fight = run.match.combat
                self.assertEqual(fight.vitals[ashe.body].total, 200)
                self.assertNotIn(ashe.body, fight.frost_hits)
                self.assertNotIn(ashe.body, fight.freezes)

    def test_live_projected_barrier_absorbs_landed_blizzard_tick(self):
        barrier = controls.AsheBarrierTests()
        run, mei, zarya, ashe = barrier.projected_arena()
        for player, hero in ((mei, "mei"), (ashe, "ashe")):
            self.assertTrue(run.match.switch_hero(player, find_hero(hero)))
        zarya.team = ashe.team = 1 - mei.team
        for player, x, z in ((mei, 0, 0), (zarya, 4, 5), (ashe, 0, 5)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(35):
            run.step({})
        self.launch(run, mei, tuple(ashe.mover.position))
        area = self.landed(run)
        direction, projected = barrier.projected_input(run, zarya, ashe)
        run.step({zarya: (*direction, 16)})
        self.assertTrue(projected.states[10].active)
        while run.frame * 16 <= area.starts:
            run.step({})
        fight = run.match.combat
        elapsed = run.frame * 16 - area.starts
        self.assertAlmostEqual(fight.personal_barriers[ashe.body][2], 200 - 20 * elapsed / 1000)
        target = next(target for target in fight._targets() if target.owner is ashe)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertNotIn(ashe.body, area.frost_due)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_actual_blizzard_pauses_bob_gun_and_leaves_actor_lifetime_intact(self):
        run, _ashe, mei, actor = bob_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        mei.mover.position[:] = (0, 1, 7)
        mei.mover.body = None
        fight = run.match.combat
        fight.vitals[mei.body] = combat.Vitals(mei.body, 10000)
        while run.frame < actor.ready + 12:
            run.step({})
        self.launch(run, mei, actor.bot.position)
        area = self.landed(run)
        entity = actor.bot.entity
        while entity not in fight.freezes and run.frame * 16 < area.ends:
            run.step({})
        deadline, _, _ = fight.freezes[entity]
        shots, expiry = actor.shots, actor.until
        self.assertGreater(shots, 0)
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(actor.shots, shots)
        run.step({})
        self.assertEqual(actor.shots, shots + 1)
        self.assertEqual(actor.until, expiry)
        self.finish(run, area)
        self.assertAlmostEqual(fight.vitals[entity].total, 1115)

    def test_ashe_lethal_viper_interrupts_windup_but_released_area_survives(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, mei, ashe = self.arena()
                fight = run.match.combat
                fight.vitals[mei.body] = combat.Vitals(mei.body, 25)
                if released:
                    self.launch(run, mei, tuple(ashe.mover.position))
                else:
                    mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
                    yaw, pitch = aim(fight._eye(mei), ashe.mover.position)
                    run.step({mei: (yaw, pitch, 32)})
                yaw, pitch = aim(fight._eye(ashe), fight._eye(mei))
                run.step({ashe: (yaw, pitch, 1)})
                self.assertTrue(fight.vitals[mei.body].dead)
                self.assertEqual(fight.vitals[mei.body].killer, ashe.body)
                if released:
                    self.finish(run, self.landed(run))
                    self.assertAlmostEqual(fight.vitals[ashe.body].total, 115)
                else:
                    for _ in range(100):
                        run.step({})
                    self.assertFalse(fight.blizzard.flights or fight.blizzard.areas)
                    self.assertEqual(fight.vitals[ashe.body].total, 200)

    def test_actual_deflect_reassigns_landed_area_and_damage_credit(self):
        run, mei, genji, ashe = controls.AsheBarrierTests().projected_arena()
        for player, hero in ((mei, "mei"), (genji, "genji"), (ashe, "ashe")):
            self.assertTrue(run.match.switch_hero(player, find_hero(hero)))
        ashe.team = mei.team = 1 - genji.team
        for player, x, z in ((mei, 0, 6), (genji, 0, 0), (ashe, 2, 5)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        flight = self.launch(run, mei, (0, fight._eye(mei)[1], 0))
        run.step({genji: (0, 0, 16)})
        for _ in range(30):
            if flight.reflections:
                break
            run.step({genji: (0, 0, 0)})
        self.assertEqual(flight.reflections, 1)
        self.assertIs(flight.owner, genji)
        area = self.landed(run)
        self.assertIs(area.owner, genji)
        while run.frame * 16 <= area.starts:
            run.step({})
        self.assertIn((genji.body, area.entity), fight.frost_hits[ashe.body])
        self.assertTrue(any(hit["+0x78"]["+0x0"]["+0x0"] == ashe.body
                            for hit in genji.client.messages(combat.HIT)))

    def test_airborne_matrix_absorbs_actual_throw(self):
        fixture = controls.AsheMatrixTests()
        run, dva, mei, matrix = fixture.matrix_arena()
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        mei.mover.position[:] = (0, 1, 4)
        mei.mover.body = None
        fight = run.match.combat
        for _ in range(40):
            run.step({})
        for target in fight._targets():
            fight.vitals_of(target)
        health = fight.vitals[dva.body].total
        flight = self.launch(run, mei, (0, fight._eye(mei)[1], 0))
        fixture.step(run, dva, mei, cast=2)
        self.assertTrue(any(state.active and state.node.pos == 9 for state in matrix.states.values()))
        for _ in range(100):
            fixture.step(run, dva, mei, cast=2)
            self.assertFalse(fight.blizzard.areas)
        self.assertNotIn(flight, fight.blizzard.flights)
        self.assertIsNone(flight.shot)
        self.assertEqual(fight.vitals[dva.body].total, health)
        self.assertNotIn(dva.body, fight.frost_hits)

    def test_real_spray_and_blizzard_stack_only_when_explicitly_enabled(self):
        for stacking in (False, True):
            with self.subTest(stacking=stacking):
                run, mei, ashe = self.arena()
                fight = run.match.combat
                mei.body_script.component.set(mei_frost.STACKING, stacking)
                self.launch(run, mei, tuple(ashe.mover.position))
                area = self.landed(run)
                while run.frame * 16 < area.starts + 800:
                    run.step({})
                keys = {(mei.body, 0), (mei.body, area.entity)}
                yaw, pitch = aim(fight._eye(mei), fight._eye(ashe))
                previous = {}
                for _ in range(45):
                    run.step({mei: (yaw, pitch, 1)})
                    if ashe.body in fight.freezes:
                        break
                    previous = fight.frost_hits[ashe.body].copy()
                self.assertEqual(set(previous), keys)
                progress = [sample[0] for sample in previous.values()]
                self.assertLess(max(progress), 0.9)
                self.assertTrue(all(sample[2] == stacking for sample in previous.values()))
                if stacking:
                    self.assertLess(sum(progress), 1.0)
                    self.assertGreater(sum(progress), 0.95)
                    deadline, _, _ = fight.freezes[ashe.body]
                    self.assertEqual(deadline - run.frame, 82)
                    health = fight.vitals[ashe.body].total
                    for _ in range(10):
                        run.step({mei: (yaw, pitch, 1)})
                    self.assertLess(fight.vitals[ashe.body].total, health)
                    self.assertEqual(fight.freezes[ashe.body][0], deadline)
                else:
                    self.assertGreater(sum(progress), 1.0)
                    self.assertAlmostEqual(mei_frost.combined_progress(previous, run.frame), max(progress))
                    self.assertNotIn(ashe.body, fight.freezes)
