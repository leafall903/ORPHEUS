"""Actual Orisa Halt against Reinhardt."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_orisa_fusion as fixture
from test_game_combat import aim

from ow174.game import deflection, halt, npc, npc_control, sigma_grasp
from ow174.game.content import SOLDIER, heroes

GRAPH_INPUT = Path(__file__).resolve().parents[3] / "013E" / "orisa_halt_174.json"


@unittest.skipUnless(GRAPH_INPUT.is_file() and (fixtures.NPC_DATA / "pve_npcs_174.json").is_file()
                     and fixtures.NPC_MOVERS.is_file(), "prepared Halt and NPC inputs required")
class TalonHaltTests(unittest.TestCase):
    scenario = fixtures.TalonInteractionTests.scenario
    place = staticmethod(fixtures.HammerTests.place)

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.addClassCleanup(fixtures.TalonInteractionTests.tearDownClass)
        cls.catalog = fixtures.TalonInteractionTests.catalog

    def arena(self, definition, position=(1.5, 1, 5)):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_ORISA_HALT": str(GRAPH_INPUT)}))
        bot, _, health = self.scenario(definition, position)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team = self.enemy.team = self.other.team = 0
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E000000000013E]))
        self.place(self.enemy, (0, 1, 0))
        self.place(self.rein, (15, 1, 5))
        self.place(self.other, (20, 1, 5))
        self.manager = self.match.combat.halt
        self.step(12)
        return bot, health

    def step(self, frames=1, *, fire=0, rein=0, yaw=0):
        for _ in range(frames):
            self.run.step({self.enemy: (0, 0, fire), self.rein: (yaw, 0, rein)})

    def cast(self):
        self.step(fire=2)
        self.step(24)
        [orb] = self.manager.orbs.values()
        self.assertIsNone(orb.requested)
        self.step(fire=2)
        self.assertIsNotNone(orb.requested)
        return orb

    def post_slow(self, bot):
        self.cast()
        for _ in range(70):
            self.step()
            pull = self.manager.pulls.get(bot.entity)
            if pull is not None:
                instance = pull.script
                wait = instance.states.get(instance.graph.node(5).state)
                if wait is not None and wait.active:
                    return wait
        self.fail("actual Halt did not start its post-pull wait")

    def test_actual_post_slow_caps_four_talon_patrols_and_then_recovers(self):
        for definition in self.catalog.values():
            if definition.name == "Heavy Assault":
                continue
            with self.subTest(npc=definition.name):
                bot, health = self.arena(definition)
                total = health.total
                wait = self.post_slow(bot)
                x, y, z = bot.position
                bot.route = [(x + 20, y, z)]
                bot.wait = 0.0
                bot.index = -1
                slowed, recovered = [], []
                ramp = next((instance for instance in bot.body_script.component.instances.values()
                             if instance.graph.index == 0x1460), None)
                reached_cap = reached_recovery_cap = False
                for _ in range(90):
                    samples = slowed if wait.active else recovered
                    multiplier = 1.0
                    if ramp is not None and (state := ramp.states.get(14)) is not None and state.active:
                        multiplier = ramp.find_var(0, 7779).value()
                    self.step()
                    speed = math.hypot(bot.velocity[0], bot.velocity[2])
                    samples.append(speed)
                    if samples is slowed:
                        # Halt subtracts from the speed scalar after the Assassin's ramp multiplies it.
                        cap = bot.speed * (multiplier - .65)
                        self.assertLessEqual(speed, cap + 1e-5)
                        reached_cap |= math.isclose(speed, cap, abs_tol=1e-5)
                    else:
                        cap = bot.speed * multiplier
                        self.assertLessEqual(speed, cap + 1e-5)
                        reached_recovery_cap |= math.isclose(speed, cap, abs_tol=1e-5)
                self.assertGreater(len(slowed), 30)
                self.assertTrue(reached_cap)
                self.assertTrue(reached_recovery_cap)
                if ramp is not None:
                    self.assertGreater(max(slowed), bot.speed * .35)
                    self.assertGreater(max(recovered), bot.speed)
                else:
                    self.assertAlmostEqual(max(recovered), bot.speed, places=5)
                self.assertEqual(health.total, total)
                self.assertNotIn(bot.entity, self.manager.pulls)
                self.assertEqual(bot.body_script.frame, self.run.frame)
                _, live = bot.body_script.initial
                self.assertFalse(any(graph == 0x0CCE for graph, _ in live.instances.values()))

    def test_actual_hammer_kill_retires_live_post_slow(self):
        definition = next(d for d in self.catalog.values() if d.name == "Trooper")
        bot, health = self.arena(definition)
        health.damage(health.total - 60, bypass_armour=True)
        wait = self.post_slow(bot)
        x, _, z = bot.position
        self.place(self.rein, (x, 1, z - 2))
        self.step(rein=1)
        for _ in range(40):
            if bot.dead:
                break
            self.assertTrue(wait.active)
            self.step()
        self.assertTrue(bot.dead)
        self.assertEqual(health.total, 0)
        self.assertNotIn(bot.entity, self.manager.pulls)
        self.assertFalse(wait.active)
        self.assertIsNotNone(bot.body_script)
        self.assertIsNotNone(bot.body_script.death_frame)

    def test_four_actual_talon_bodies_move_and_heavy_retains_authored_immunity(self):
        self.assertEqual(len(self.catalog), 5)
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                bot, health = self.arena(definition)
                origin, total = bot.position, health.total
                self.cast()
                self.step(25)
                self.assertEqual(bot.position, origin)
                self.step(5)
                if definition.name == "Heavy Assault":
                    immunity = bot.body_script.component.entity_var(6601)
                    self.assertTrue(immunity.value())
                    self.assertTrue(any(link.state.instance.graph.index == 0x1254 for link in immunity.links))
                    self.assertEqual(bot.position, origin)
                    self.assertNotIn(bot.entity, self.manager.pulls)
                else:
                    pull = self.manager.pulls[bot.entity]
                    self.assertEqual(pull.script.graph.index, 0xCCE)
                    self.assertIsNotNone(pull.mover)
                    host = bot.body_script.component.world
                    self.assertEqual(host.movement_flags(bot.entity), pull.mover.flags)
                    self.assertGreater(bot.position[2], origin[2] + .3)
                    self.assertLess(bot.position[0], origin[0])
                    self.assertEqual(bot.last_tick, self.run.frame)
                self.assertEqual(health.total, total)
                self.step(90)
                self.assertNotIn(bot.entity, self.manager.pulls)
                self.assertFalse(self.manager.orbs)
                self.assertAlmostEqual(bot.position[1], origin[1], delta=.03)

    def test_actual_reinhardt_pin_owns_target_pose_through_halt(self):
        definition = next(d for d in self.catalog.values() if d.name == "Trooper")
        bot, health = self.arena(definition, (0, 1, 5))
        self.place(self.rein, (0, 1, 1))
        self.step(rein=8)
        for _ in range(75):
            self.step()
            if self.match.combat.pinned(bot.entity):
                break
        self.assertTrue(self.match.combat.pinned(bot.entity))
        self.place(self.enemy, (1.5, 1, bot.position[2] - 2))
        total = health.total
        orb = self.cast()
        self.assertLess(math.dist(bot.position, orb.rocket.position), halt.RADIUS)
        self.step(32)
        self.assertTrue(self.match.combat.pinned(bot.entity))
        self.assertNotIn(bot.entity, self.manager.pulls)
        self.assertEqual(health.total, total)

    def test_halt_takes_over_airborne_charge_bump_and_releases_at_the_new_pose(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        bot, health = self.arena(definition, (1.6, 1, 3))
        fight = self.match.combat
        total = health.total
        self.place(self.rein, (0, 1, 0))
        self.step(rein=8)
        for _ in range(75):
            self.step()
            if bot.entity in fight.knockbacks:
                break
        self.assertEqual(health.total, total - 50)
        impulse = fight.knockbacks[bot.entity]

        x, _, z = bot.position
        self.place(self.enemy, (x + 2, 1, z + 3))
        self.cast()
        self.assertTrue(impulse.airborne)
        for _ in range(45):
            self.step()
            pull = self.manager.pulls.get(bot.entity)
            if pull is not None and pull.mover is not None:
                break
        else:
            self.fail("Halt did not acquire the airborne Assassin")
        self.assertIs(pull.mover, impulse)
        self.assertNotIn(bot.entity, fight.knockbacks)
        self.assertTrue(pull.mover.airborne)

        for _ in range(70):
            previous = bot.position
            self.step()
            self.assertLess(math.dist(bot.position, previous), .5)
            if pull.mover is None:
                break
        else:
            self.fail("Halt did not release movement ownership")
        self.assertIs(fight.knockbacks.get(bot.entity), impulse)
        self.step(90)
        self.assertNotIn(bot.entity, fight.knockbacks)
        self.assertAlmostEqual(bot.position[1], 1, delta=.03)
        self.assertEqual(health.total, total - 50)

    def hammer_during_halt(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        bot, health = self.arena(definition)
        fight = self.match.combat
        total = health.total
        self.place(self.rein, (0, 1, 3))
        self.cast()
        self.step()
        self.step(rein=1)
        for _ in range(50):
            self.step()
            if health.total < total:
                break
        self.assertEqual(health.total, total - 85)
        pull = self.manager.pulls[bot.entity]
        self.assertIsNotNone(pull.mover)
        impulse = fight.knockbacks[bot.entity]
        self.assertIsNot(pull.mover, impulse)
        return bot, health, pull, impulse

    def test_hammer_impulse_during_halt_resumes_from_the_pulled_pose(self):
        bot, health, pull, impulse = self.hammer_during_halt()
        fight = self.match.combat
        total = health.total
        contact_pose = tuple(impulse.position)

        for _ in range(40):
            previous = bot.position
            self.step()
            self.assertLess(math.dist(bot.position, previous), .5)
            if pull.mover is None:
                break
        else:
            self.fail("Halt did not release movement ownership")
        self.assertIs(fight.knockbacks[bot.entity], impulse)
        self.assertEqual(tuple(impulse.position), bot.position)
        self.assertGreater(math.dist(bot.position, contact_pose), 1)
        self.assertGreater(bot.velocity[0], 0)
        self.step(90)
        self.assertNotIn(bot.entity, fight.knockbacks)
        self.assertAlmostEqual(bot.position[1], 1, delta=.03)
        self.assertEqual(health.total, total)

    def test_death_and_removal_clear_overlapping_pull_and_impulse(self):
        for ending in ("death", "remove"):
            with self.subTest(ending=ending):
                bot, health, pull, _ = self.hammer_during_halt()
                fight = self.match.combat
                old = bot.entity
                if ending == "death":
                    health.damage(health.total - 1, bypass_armour=True)
                    x, y, z = bot.position
                    self.place(self.other, (x + 3, y, z))
                    target = tuple(a + b * .016 for a, b in zip((x, y + 1, z), bot.velocity, strict=True))
                    angles = aim(fight._eye(self.other), target)
                    self.run.step({self.other: (*angles, 1)})
                    self.assertTrue(bot.dead)
                    self.assertEqual(health.killer, self.other.body)
                    self.assertEqual(health.total, 0)
                    self.assertIsNotNone(bot.body_script.death_frame)
                else:
                    self.assertEqual(npc.remove_spawned(self.match), (old,))
                    self.assertNotIn(bot, self.match.bots)
                    self.assertIsNone(bot.body_script)
                self.assertNotIn(old, self.manager.pulls)
                self.assertNotIn(old, fight.knockbacks)
                self.assertTrue(pull.script.stopped)
                self.assertIsNone(pull.mover)

                pose = bot.position
                self.place(self.rein, (15, 1, 3))
                definition = next(d for d in self.catalog.values() if d.name == "Assassin")
                replacement = npc.spawn(self.match, definition, pose, team=bot.team)
                self.assertNotEqual(replacement.entity, old)
                self.step(90)
                self.assertEqual(bot.position, pose)
                self.assertEqual(replacement.position, pose)
                self.assertEqual(fight.vitals[replacement.entity].total,
                                 definition.health + definition.armour + definition.shields)
                self.assertNotIn(replacement.entity, self.manager.pulls)
                self.assertNotIn(replacement.entity, fight.knockbacks)

    def test_trooper_can_hit_reinhardt_during_halt_lifetime(self):
        definition = next(d for d in self.catalog.values() if d.name == "Trooper")
        bot, _ = self.arena(definition)
        self.cast()
        self.step(26)
        self.assertIn(bot.entity, self.manager.pulls)
        x, y, z = bot.position
        self.place(self.rein, (x, y, z - 2))
        fight = self.match.combat
        self.rein.mover.yaw, self.rein.mover.pitch = aim(fight._eye(self.rein), (x, y + 1, z))
        self.assertEqual(npc_control.start(self.match, self.rein), bot.entity)
        victim = fight.vitals_of(fight._target_entity(self.rein.body))
        for _ in range(40):
            self.step()
            if victim.total < 500:
                break
        self.assertEqual(victim.total, 485)
        self.assertIn(bot.entity, self.manager.pulls)


@unittest.skipUnless(GRAPH_INPUT.is_file(), "authored Halt graph supplement required")
class ReinhardtHaltTests(unittest.TestCase):
    place = staticmethod(fixture.ReinhardtFusionTests.place)
    health = fixture.ReinhardtFusionTests.health
    shield = fixture.ReinhardtFusionTests.shield
    advance = fixture.ReinhardtFusionTests.advance

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_ORISA_HALT": str(GRAPH_INPUT)}))
        fixture.ReinhardtFusionTests.setUp(self)
        self.place(self.enemy, (0, 1, 0))
        self.place(self.rein, (1.5, 1, 5))
        self.manager = self.fight.halt

    def step(self, frames=1, *, fire=0, rein=0, yaw=0):
        for _ in range(frames):
            self.run.step({self.enemy: (0, 0, fire), self.rein: (yaw, 0, rein)})

    def detonate(self, *, start_charge=False):
        self.step(fire=2, rein=8 if start_charge else 0)
        self.step(24)
        [orb] = self.manager.orbs.values()
        self.assertEqual(orb.rocket.speed, 25)
        self.assertIsNone(orb.requested)
        self.step(fire=2)
        self.assertEqual(orb.requested, self.run.frame)
        return orb

    def test_actual_second_press_freezes_then_runs_original_move_to_without_damage(self):
        before = tuple(self.rein.mover.position)
        orb = self.detonate()
        frozen = orb.rocket.position
        self.step(24)
        self.assertEqual(orb.rocket.position, frozen)
        self.assertEqual(tuple(self.rein.mover.position), before)
        self.assertEqual(self.manager.pulls[self.rein.body].script.graph.index, 0xCCE)
        self.step(10)
        self.assertGreater(self.rein.mover.position[2], before[2] + 1)
        self.assertLess(self.rein.mover.position[0], before[0])
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(self.manager.orbs)
        self.step(60)
        self.assertNotIn(self.rein.body, self.manager.pulls)

    def test_halt_interrupts_active_charge(self):
        self.detonate(start_charge=True)
        active = False
        for _ in range(55):
            self.step()
            active |= self.rein.body in self.fight.charge_drives
            if self.manager.moving(self.rein.body):
                break
        self.assertTrue(active)
        self.assertTrue(self.manager.moving(self.rein.body))
        self.step(rein=8)
        self.assertNotIn(self.rein.body, self.fight.charge_drives)
        self.assertFalse(self.fight.charge_contacts)
        self.assertEqual(self.health(self.enemy), 450)
        self.assertEqual(self.health(self.rein), 500)

    def test_barrier_stops_orb_and_pull_damages_neither_pool(self):
        self.place(self.rein, (0, 1, 5))
        self.step(15, rein=2, yaw=-32768)
        self.step(fire=2, rein=2, yaw=-32768)
        orb = None
        for _ in range(30):
            self.step(rein=2, yaw=-32768)
            orb = next(iter(self.manager.orbs.values()), orb)
            if orb is not None and orb.requested is not None:
                break
        self.assertIsNotNone(orb)
        self.assertIsNotNone(orb.requested)
        self.assertLess(orb.rocket.position[2], 4)
        self.step(34, rein=2, yaw=-32768)
        self.assertEqual(self.shield().current, 1600)
        self.assertEqual(self.health(self.rein), 500)
        self.assertLess(self.rein.mover.position[2], 5)

    def test_ally_range_and_static_cover_deny_pull(self):
        for condition in ("ally", "range", "cover"):
            with self.subTest(condition=condition):
                if condition != "ally":
                    self.setUp()
                if condition == "ally":
                    self.rein.team = self.enemy.team
                elif condition == "range":
                    self.place(self.rein, (10, 1, 5))
                else:
                    self.place(self.rein, (1.5, 1, 7))
                    fixtures.charge_wall(self, 4)
                before = tuple(self.rein.mover.position)
                if condition == "cover":
                    self.step(fire=2)
                else:
                    self.detonate()
                self.step(75)
                self.assertEqual(tuple(self.rein.mover.position), before)
                self.assertNotIn(self.rein.body, self.manager.pulls)

    def test_early_second_press_and_caster_replacement_retire_flight(self):
        self.step(fire=2)
        self.step(14)
        self.step(fire=2)
        [orb] = self.manager.orbs.values()
        self.assertIsNone(orb.requested)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        self.assertNotIn(orb.rocket, self.fight.rockets)
        self.step(70)
        self.assertFalse(self.manager.orbs)
        self.assertFalse(self.manager.pulls)

    def test_reflected_orb_rejects_original_caster_and_pulls_former_ally_on_contact(self):
        self.rein.team = self.enemy.team
        self.other.team = self.enemy.team + 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[deflection.GENJI]))
        self.place(self.other, (0, 1, 6))
        self.place(self.rein, (1.5, 1, -1))
        fixtures.charge_wall(self, -5)
        self.step(12)
        genji = (-32768, 0, 0)
        self.run.step({self.other: (-32768, 0, 16)})
        self.run.step({self.other: genji, self.enemy: (0, 0, 2)})
        cast_frame = self.run.frame
        orb = None
        for _ in range(24):
            self.run.step({self.other: genji})
            orb = next(iter(self.manager.orbs.values()), orb)
            if orb is not None and orb.rocket.reflected:
                break
        self.assertIsNotNone(orb)
        shot = orb.rocket
        self.assertEqual(shot.definition, halt.PROJECTILE)
        self.assertTrue(shot.reflected)
        self.assertIs(shot.shooter, self.other)
        self.assertEqual(shot.halt_body, self.other.body)
        self.assertEqual(shot.team, self.other.team)
        self.assertIsNone(orb.requested)
        self.assertFalse(self.manager.receive(orb.entity, halt.DETONATE, self.enemy.body))
        self.place(self.enemy, (8, 1, 0))
        while self.run.frame < cast_frame + 24:
            self.run.step({self.other: genji})
        self.run.step({self.other: genji, self.enemy: (0, 0, 2)})
        self.assertIsNone(orb.requested)
        for _ in range(35):
            self.run.step({self.other: genji})
            if self.rein.body in self.manager.pulls:
                break
        self.assertIn(self.rein.body, self.manager.pulls)
        self.assertEqual(self.manager.pulls[self.rein.body].source_team, self.other.team)
        self.step(40)
        self.assertLess(self.rein.mover.position[2], -2)
        self.assertEqual(self.health(self.rein), 500)

    def test_grasp_consumes_orb_without_pull_or_shield_credit(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 5))
        self.place(self.enemy, (0, 1, -5))
        self.step(15)
        self.run.step({self.other: (-32768, 0, 8)})
        self.step(5)
        self.step(fire=2)
        orb = None
        for _ in range(50):
            self.run.step({self.other: (-32768, 0, 0)})
            orb = next(iter(self.manager.orbs.values()), orb)
            if orb is not None and not self.manager.orbs:
                break
        self.assertIsNotNone(orb)
        self.assertIsNone(orb.requested)
        self.assertFalse(self.manager.orbs)
        self.assertFalse(self.manager.pulls)
        target = self.fight._target_entity(self.other.body)
        self.step(130)
        self.assertEqual(self.fight.vitals_of(target).grasp_shields, 0)
        self.assertEqual(self.health(self.rein), 500)
