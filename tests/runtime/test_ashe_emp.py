"""Actual build 104319 Sombra EMP against Ashe and B.O.B."""

import math
import os
import unittest
from pathlib import Path

import test_ashe_bob as bob_fixtures
import test_ashe_controls as controls
import test_ashe_hack as hack_fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat, sombra_hack
from ow174.game.content import HEROES_PATH, find_hero
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires build 104319 data")
class AsheEmpTests(unittest.TestCase):
    fight = hack_fixtures.AsheHackTests.fight
    arena = hack_fixtures.AsheHackTests.arena

    def pulse(self, run, sombra):
        script = sombra.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1400)
        script.set_ult_charge(cost)
        run.step({sombra: (0, 0, 32)})
        for _ in range(21):
            run.step({})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        return sombra_hack.instance_for(sombra, sombra_hack.EMP)

    def receiver(self, actor):
        child = sombra_hack.instance_for(actor, sombra_hack.EMP_CHILD)
        effect = sombra_hack.instance_for(actor, sombra_hack.HACK)
        self.assertIsNotNone(child)
        self.assertIsNotNone(effect)
        wait = effect.states[58]
        self.assertEqual(wait.ms, 5000)
        return wait

    def test_emp_blocks_abilities_without_suppressing_viper_and_releases_at_expiry(self):
        run, sombra, ashe = self.arena()
        fight = run.match.combat
        run.step({sombra: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertFalse(sombra_hack.locked(ashe))

        pulse = self.pulse(run, sombra)
        self.assertEqual(pulse.find_var(0, 1648).value(), 15)
        wait = self.receiver(ashe)
        self.assertEqual(wait.start // 16, run.frame)
        self.assertFalse(fight.is_stunned(ashe))
        script = ashe.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        yaw, pitch = aim(fight._eye(ashe), fight._eye(sombra))
        run.step({ashe: (yaw, pitch, 1 | 8 | 16 | 32)})
        self.assertEqual(fight.vitals[sombra.body].total, 120)
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        self.assertFalse(fight.dynamite.grenades or fight.companions.active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)

        deadline = (wait.start + wait.ms) // script.frame_ms
        while run.frame < deadline - 1:
            run.step({})
        self.assertTrue(sombra_hack.locked(ashe, run.frame))
        run.step({ashe: (yaw, pitch, 32)})
        self.assertFalse(sombra_hack.locked(ashe))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        run.step({})
        run.step({ashe: (yaw, pitch, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_emp_excludes_allies_range_and_terrain(self):
        for case in ("ally", "range", "terrain"):
            with self.subTest(case=case):
                run, sombra, ashe = self.arena(distance=20 if case == "range" else 5, ally=case == "ally")
                if case == "terrain":
                    run.match.collision_world = wall_scene(2.5)
                self.pulse(run, sombra)
                self.assertIsNone(sombra_hack.instance_for(ashe, sombra_hack.EMP_CHILD))
                self.assertFalse(sombra_hack.locked(ashe))

    def test_emp_breaks_live_projected_barrier_and_locks_ashe_without_health_damage(self):
        barrier = controls.AsheBarrierTests()
        run, sombra, zarya, ashe = barrier.projected_arena()
        for actor, hero in ((sombra, "sombra"), (ashe, "ashe")):
            self.assertTrue(run.match.switch_hero(actor, find_hero(hero)))
        zarya.team = ashe.team = 1 - sombra.team
        for actor, x, z in ((sombra, 0, 0), (zarya, 4, 6), (ashe, 0, 6)):
            actor.mover.position[:] = (x, 1, z)
            actor.mover.body = None
        for _ in range(35):
            run.step({})
        direction, projected = barrier.projected_input(run, zarya, ashe)
        run.step({zarya: (*direction, 16)})
        self.assertTrue(projected.states[10].active)
        self.pulse(run, sombra)
        self.receiver(ashe)
        self.assertFalse(projected.states[10].active)
        self.assertNotIn(ashe.body, run.match.combat.personal_barriers)
        self.assertEqual(run.match.combat.vitals[ashe.body].total, 200)
        pools = run.match.combat.vitals[zarya.body].now
        self.assertEqual(pools[combat.HEALTH_POOL], 200)
        self.assertEqual(pools[combat.SHIELDS_POOL], 0)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_emp_pauses_active_bob_gun_and_preserves_lifetime(self):
        run, ashe, sombra, actor = bob_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(sombra, find_hero("sombra")))
        sombra.mover.position[:] = (0, 1, 7)
        sombra.mover.body = None
        fight = run.match.combat
        fight.vitals[sombra.body] = combat.Vitals(sombra.body, 10000)
        while run.frame < actor.ready + 20:
            run.step({})
        self.assertGreater(actor.shots, 0)
        self.pulse(run, sombra)
        self.receiver(ashe)
        wait = self.receiver(actor.bot)
        deadline = math.ceil((wait.start + wait.ms) / 16)
        shots, expiry = actor.shots, actor.until
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(actor.shots, shots)
        run.step({})
        self.assertEqual(actor.shots, shots + 1)
        self.assertEqual(actor.until, expiry)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200)

    def test_emp_refreshes_the_lock_from_its_own_impact_after_ordinary_hack(self):
        run, sombra, ashe = self.arena()
        old = hack_fixtures.AsheHackTests().cast(run, sombra, ashe)
        before = old.start + old.ms
        self.pulse(run, sombra)
        wait = self.receiver(ashe)
        self.assertIsNot(wait, old)
        self.assertGreater(wait.start + wait.ms, before)
        while run.frame < math.ceil(before / 16):
            run.step({})
        self.assertTrue(sombra_hack.locked(ashe, run.frame))
        self.assertEqual(wait.ms, 5000)

    def test_lethal_viper_hit_during_emp_windup_prevents_pulse(self):
        run, sombra, ashe = self.arena()
        fight = run.match.combat
        fight.vitals[sombra.body] = combat.Vitals(sombra.body, 25)
        script = sombra.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({sombra: (0, 0, 32)})
        yaw, pitch = aim(fight._eye(ashe), fight._eye(sombra))
        run.step({ashe: (yaw, pitch, 1)})
        self.assertTrue(fight.vitals[sombra.body].dead)
        for _ in range(50):
            run.step({})
        self.assertIsNone(sombra_hack.instance_for(ashe, sombra_hack.EMP_CHILD))
        self.assertFalse(sombra_hack.locked(ashe))

    def test_emp_selects_winston_shell_independent_of_owner_and_respects_cover_team_and_range(self):
        for case in ("enemy", "ally", "range", "terrain"):
            with self.subTest(case=case):
                distance = {"enemy": 18, "range": 27}.get(case, 10)
                run, sombra, winston = self.arena(distance=distance, ally=case == "ally")
                self.assertTrue(run.match.switch_hero(winston, find_hero("winston")))
                sombra.mover.position[:] = (0, 1, 0)
                sombra.mover.body = None
                winston.mover.position[:] = (0, 1, distance)
                winston.mover.body = None
                if case == "ally":
                    winston.team = sombra.team
                for _ in range(40):
                    run.step({})
                run.step({winston: (0, 0, 16)})
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                actor, = fight.world_barriers.actors.values()
                self.assertEqual(actor.health, 700)
                winston.mover.position[:] = (0, 1, 29)
                winston.mover.body = None
                if case == "terrain":
                    run.match.collision_world = wall_scene(2.5)
                self.pulse(run, sombra)
                self.assertFalse(sombra_hack.locked(winston))
                if case == "enemy":
                    self.assertNotIn(actor.entity, fight.world_barriers.actors)
                    self.assertEqual(actor.health, 0)
                else:
                    self.assertIn(actor.entity, fight.world_barriers.actors)
                    self.assertEqual(actor.health, 700)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_COLLISION"), "requires the authored Sigma mesh")
    def test_emp_breaks_deployed_sigma_mesh_while_owner_is_out_of_range(self):
        run, sombra, sigma = self.arena(distance=13)
        self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
        sombra.mover.position[:] = (0, 1, 0)
        sombra.mover.body = None
        sigma.mover.position[:] = (0, 1, 13)
        sigma.mover.body = None
        for _ in range(45):
            run.step({})
        fight = run.match.combat
        shield = fight.sigma_barriers.actors[sigma.body]
        yaw, pitch = aim(fight._eye(sigma), fight._eye(sombra))
        for _ in range(12):
            run.step({sigma: (yaw, pitch, 2)})
        for _ in range(12):
            run.step({})
        self.assertEqual(shield.mode, 3)
        self.assertEqual(shield.health, 700)
        sigma.mover.position[:] = (0, 1, 27)
        sigma.mover.body = None
        self.pulse(run, sombra)
        self.assertGreater(math.dist(fight._eye(sigma), fight._eye(sombra)), 20)
        self.assertEqual(shield.health, 0)
        self.assertNotIn(shield, tuple(fight.sigma_barriers.enemies(sombra)))
        self.assertFalse(sombra_hack.locked(sigma))

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_emp_during_bob_charge_stops_motion_until_timeout_and_delays_gun(self):
        run, sombra, ashe = self.arena(distance=12)
        sombra.mover.position[:] = (6, 1, 12)
        fight = run.match.combat
        fight.vitals[sombra.body] = combat.Vitals(sombra.body, 10000)
        ashe.body_script.set_ult_charge(ashe.body_script.component.entity_var(ULT_COST).value())
        run.step({ashe: (0, 0, 32)})
        for _ in range(12):
            run.step({})
        actor, = fight.companions.active.values()
        self.assertEqual(actor.phase, "charge")
        self.assertGreater(actor.bot.position[2], 2)
        self.pulse(run, sombra)
        wait = self.receiver(actor.bot)
        self.assertEqual(actor.phase, "charge")
        charge_end = actor.charge_until
        deadline = math.ceil((wait.start + wait.ms) / 16)
        self.assertLess(charge_end, deadline)
        position = actor.bot.position
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(actor.bot.position, position)
            self.assertEqual(actor.shots, 0)
        self.assertEqual(actor.stopped_reason, "timeout")
        self.assertLess(actor.ready, deadline)
        run.step({})
        self.assertEqual(actor.shots, 1)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_emp_on_ashe_does_not_pause_bob_outside_pulse_range(self):
        run, ashe, sombra, actor = bob_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(sombra, find_hero("sombra")))
        for player, z in ((ashe, -30), (sombra, -24)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        fight = run.match.combat
        fight.vitals[sombra.body] = combat.Vitals(sombra.body, 10000)
        while run.frame < actor.ready + 20:
            run.step({})
        self.assertGreater(actor.shots, 0)
        self.pulse(run, sombra)
        self.receiver(ashe)
        self.assertFalse(sombra_hack.locked(actor.bot))
        shots = actor.shots
        health = fight.vitals[sombra.body].total
        for _ in range(32):
            run.step({})
        self.assertTrue(sombra_hack.locked(ashe))
        self.assertGreater(actor.shots, shots)
        self.assertEqual(fight.vitals[sombra.body].total, health - 14 * (actor.shots - shots))
