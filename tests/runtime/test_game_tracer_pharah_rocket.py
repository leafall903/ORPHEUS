"""Actual Pharah Rocket Launcher and Tracer combat inputs."""

import os
import unittest
from dataclasses import replace

import test_game_tracer_body_protection as fixtures
import test_game_tracer_dva_mech as mech_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_hammer import REINHARDT
from test_game_tracer_zarya_barrier import ZARYA

from ow174.game import combat, deflection, dva, sigma_barrier, sigma_grasp, stats, tracer, zarya_barrier
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

GENJI = 0x02E0000000000029
WINSTON = 0x02E0000000000009


class TracerPharahRocketTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.TracerBodyProtectionTests()
        self.case.arena(0x02E0000000000008, behind=False, distance=8)
        self.run, self.fight = self.case.run, self.case.fight
        self.player, self.pharah = self.case.tracer, self.case.enemy
        self.health = self.case.health(self.player)
        x, y, z = self.player.mover.position
        self.incoming = aim(self.fight._eye(self.pharah), (x, y + 1, z))

    def rocket(self, *, buttons=0, angles=None, frames=65):
        self.run.step({self.pharah: (*(self.incoming if angles is None else angles), FIRE),
                       self.player: (0, 0, buttons)})
        self.assertEqual(len(self.fight.rockets), 1)
        for _ in range(frames):
            self.run.step({})
        self.assertFalse(self.fight.rockets)

    def hits(self):
        return [notice["+0x78"] for notice in self.pharah.client.messages(combat.HIT)]

    def test_actual_barrage_rejects_undercharge_and_ends_its_channel(self):
        script = self.pharah.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x002B)
        volley = instance.state(1)
        script.set_ult_charge(cost - 1)
        self.run.step({self.pharah: (16384, 0, 32)})
        self.assertFalse(volley.active)
        self.assertEqual(charge.value(), cost - 1)
        self.assertFalse(self.fight.rockets)
        self.run.step({})
        script.set_ult_charge(cost)
        self.run.step({self.pharah: (16384, 0, 32)})
        self.assertTrue(volley.active)
        self.assertEqual(charge.value(), 0)
        self.assertTrue(self.fight.rockets)
        for _ in range(60):
            self.run.step({})
        self.assertTrue(volley.active)
        for _ in range(300):
            self.run.step({})
        self.assertFalse(volley.active)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.hits())
        self.run.step({self.pharah: (16384, 0, 32)})
        self.assertFalse(volley.active)
        self.assertFalse(self.fight.rockets)
        self.run.step({})
        script.set_ult_charge(cost)
        self.run.step({self.pharah: (16384, 0, 32)})
        self.assertTrue(volley.active)
        self.assertEqual(charge.value(), 0)
        self.assertTrue(self.fight.rockets)

    def test_actual_barrage_first_rocket_obeys_damage_team_cover_and_recall(self):
        for gate in ("direct", "ally", "cover", "recall"):
            with self.subTest(gate=gate):
                self.setUp()
                match = self.run.match
                if gate == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.pharah.team = self.player.team
                elif gate == "cover":
                    x, y, z = self.player.mover.position
                    match.collision_world = World(box(x-4, x+4, y, y+4, z+1, z+1.2))
                script = self.pharah.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.run.step({self.pharah: (*self.incoming, 32),
                               self.player: (0, 0, 16 if gate == "recall" else 0)})
                first, = self.fight.rockets
                for _ in range(32):
                    self.run.step({})
                    if all(rocket is not first for rocket in self.fight.rockets):
                        break
                if gate == "direct":
                    self.assertFalse(any(rocket is first for rocket in self.fight.rockets))
                    self.assertEqual([hit["+0x4"] for hit in self.hits()], [10, 30])
                    self.assertEqual(self.health.total, 110)
                else:
                    self.assertEqual(self.health.total, 150)
                    self.assertFalse(self.hits())
                if gate == "recall":
                    self.assertTrue(tracer.recalling(self.player))

    def test_actual_barrage_kills_stationary_tracer_and_finishes_channel(self):
        script = self.pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x002B)
        volley = instance.state(1)
        self.run.step({self.pharah: (*self.incoming, 32)})
        self.assertTrue(volley.active)
        start_tick = self.fight.tick
        health = [150]
        for _ in range(100):
            self.run.step({self.pharah: (*self.incoming, 0)})
            if self.health.total != health[-1]:
                health.append(self.health.total)
            if self.health.dead:
                break
        self.assertEqual(health, [150, 110, 70, 30, 0])
        self.assertEqual(self.health.killer, self.pharah.body)
        hits = self.hits()
        self.assertEqual([hit["+0x4"] for hit in hits], [10, 30, 10, 30, 10, 30, 10, 20])
        self.assertEqual([hit["+0x8"] for hit in hits], [0] * 7 + [combat.KILLING])
        self.assertTrue(volley.active)
        for _ in range(160):
            self.run.step({self.pharah: (*self.incoming, 0)})
            if not volley.active:
                break
        self.assertFalse(volley.active)
        self.assertEqual(self.fight.tick - start_tick, 156)
        self.assertEqual(volley.reported, 76)
        self.assertTrue(self.fight.rockets)
        for _ in range(40):
            self.run.step({self.pharah: (*self.incoming, 0)})
            if not self.fight.rockets:
                break
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.fight.tick - start_tick, 188)
        self.assertEqual(self.hits(), hits)

    def test_actual_blink_evades_barrage_and_reentry_restores_damage(self):
        for gate in ("before_impact", "after_impact", "reentry", "recall_reentry"):
            with self.subTest(gate=gate):
                self.setUp()
                start_x = self.player.mover.position[0]
                script = self.pharah.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x002B)
                volley = instance.state(1)
                aim_held = {self.pharah: (*self.incoming, 0)}
                self.run.step({self.pharah: (*self.incoming, 32)})
                first, = self.fight.rockets
                if gate != "before_impact":
                    for _ in range(32):
                        self.run.step(aim_held)
                        if self.health.total < 150:
                            break
                    self.assertEqual(self.health.total, 110)
                else:
                    self.assertTrue(any(rocket is first for rocket in self.fight.rockets))
                before = self.health.total
                hits_before = self.hits()
                reported = volley.reported
                self.run.step({**aim_held, self.player: (16384, 0, 8)})
                self.assertGreater(self.player.mover.position[0] - start_x, 6)
                for _ in range(20 if gate == "recall_reentry" else 50):
                    self.run.step(aim_held)
                self.assertTrue(volley.active)
                self.assertGreater(volley.reported, reported)
                self.assertEqual(self.health.total, before)
                self.assertEqual(self.hits(), hits_before)
                if gate == "reentry":
                    self.run.step({**aim_held, self.player: (-16384, 0, 8)})
                    self.assertAlmostEqual(self.player.mover.position[0], start_x, delta=0.1)
                    for _ in range(20):
                        if self.health.total < before:
                            break
                        self.run.step(aim_held)
                    self.assertEqual(self.health.total, 70)
                elif gate == "recall_reentry":
                    self.run.step({**aim_held, self.player: (0, 0, 16)})
                    self.assertTrue(tracer.recalling(self.player))
                    for _ in range(85):
                        self.run.step(aim_held)
                        if not tracer.recalling(self.player):
                            break
                        self.assertEqual(self.hits(), hits_before)
                    self.assertFalse(tracer.recalling(self.player))
                    self.assertAlmostEqual(self.player.mover.position[0], start_x, delta=0.1)
                    self.assertEqual(self.health.total, 150)
                    for _ in range(20):
                        if self.health.total < 150:
                            break
                        self.run.step(aim_held)
                    self.assertEqual(self.health.total, 110)

    def test_actual_pulse_kill_interrupts_barrage_and_preserves_released_rockets(self):
        self.pharah.mover.position[2] = self.player.mover.position[2] + 4
        self.pharah.mover.body = None
        pulse = self.player.body_script
        pulse.set_ult_charge(pulse.component.entity_var(ULT_COST).value())
        self.run.step({self.player: (0, 2059, 32)})
        bombs = self.fight.pulse_bombs.bombs
        for _ in range(45):
            if bombs and bombs[0].attached == self.pharah.body:
                break
            self.run.step({})
        bomb, = bombs
        self.assertEqual(bomb.attached, self.pharah.body)

        script = self.pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x002B)
        volley = instance.state(1)
        self.run.step({self.pharah: (16384, 0, 32)})
        self.assertTrue(volley.active)
        first, = self.fight.rockets
        for _ in range(90):
            self.run.step({self.pharah: (16384, 0, 0)})
            if self.case.vitals.dead:
                break
        self.assertTrue(self.case.vitals.dead)
        self.assertEqual(self.case.vitals.killer, self.player.body)
        self.assertFalse(bombs)
        self.assertFalse(volley.active)
        self.assertTrue(any(rocket is first for rocket in self.fight.rockets))
        flown_at_death = first.flown
        reported = volley.reported
        released = tuple(self.fight.rockets)
        for _ in range(25):
            self.run.step({self.pharah: (16384, 0, 32)})
            self.assertTrue(all(any(rocket is old for old in released) for rocket in self.fight.rockets))
        self.assertEqual(volley.reported, reported)
        self.assertGreater(first.flown, flown_at_death)
        for _ in range(180):
            self.run.step({})
        self.assertFalse(self.fight.rockets)

    def test_actual_concussion_pushes_without_damage_and_respects_protection(self):
        for gate in ("hostile", "ally", "cover", "recall", "bubble"):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.player.mover.position
                match = self.run.match
                bubble = None
                pharah_z = self.pharah.mover.position[2]
                if gate == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.pharah.team = self.player.team
                elif gate == "cover":
                    match.collision_world = World(box(x-4, x+4, y, y+4, z+1, z+1.2))
                elif gate == "bubble":
                    winston = self.shield_owner(WINSTON, offset=-3.5, allied=False)
                    self.run.step({winston: (0, 0, 16)})
                    for _ in range(8):
                        self.run.step({})
                    bubble = self.fight.winston_barriers.actors[winston.body]
                self.run.step({self.pharah: (*self.incoming, 16),
                               self.player: (0, 0, 16 if gate == "recall" else 0)})
                for _ in range(30):
                    if self.fight.rockets:
                        break
                    self.run.step({self.pharah: (*self.incoming, 0)})
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, combat.PHARAH_CONCUSSION)
                self.assertEqual(rocket.blast.amount, 0)
                self.assertEqual(rocket.blast_impulse, (20, 5, ((8, 1, 1),)))
                for _ in range(42):
                    self.run.step({})
                if gate == "hostile":
                    self.assertLess(self.player.mover.position[2], z - 3)
                    self.assertGreater(self.pharah.mover.position[2], pharah_z + 3)
                    self.assertEqual(self.case.vitals.total, 200)
                else:
                    self.assertAlmostEqual(self.player.mover.position[2], z, delta=0.02)
                if bubble is not None:
                    self.assertEqual(bubble.health, 700)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())

    def test_actual_concussion_during_pulse_windup_and_flight(self):
        for impact_before_release in (True, False):
            with self.subTest(impact_before_release=impact_before_release):
                self.setUp()
                start_z = self.player.mover.position[2]
                pulse = self.player.body_script
                pulse.set_ult_charge(pulse.component.entity_var(ULT_COST).value())
                aim = {self.player: (16384, 0, 0), self.pharah: (*self.incoming, 0)}
                bombs = self.fight.pulse_bombs.bombs
                if impact_before_release:
                    self.run.step({**aim, self.pharah: (*self.incoming, 16)})
                    for _ in range(30):
                        if self.fight.rockets:
                            break
                        self.run.step(aim)
                    rocket, = self.fight.rockets
                    for _ in range(3):
                        self.run.step(aim)
                    self.assertIn(rocket, self.fight.rockets)
                    self.run.step({**aim, self.player: (16384, 0, 32)})
                else:
                    self.run.step({self.player: (16384, 0, 32), self.pharah: (*self.incoming, 16)})
                self.assertFalse(bombs)
                self.assertEqual(pulse.component.entity_var(ULT_CHARGE).value(), 0)
                for _ in range(18):
                    self.run.step(aim)
                    if bombs:
                        break
                bomb, = bombs
                if impact_before_release:
                    self.assertNotIn(rocket, self.fight.rockets)
                    self.assertLess(self.player.mover.position[2], start_z - 0.1)
                    self.assertEqual(bomb.position, self.fight._eye(self.player))
                else:
                    self.assertAlmostEqual(bomb.position[2], start_z, delta=0.02)
                    release_age = bomb.age
                    for _ in range(35):
                        self.run.step(aim)
                        if self.player.mover.position[2] < start_z - 0.1:
                            break
                    self.assertLess(self.player.mover.position[2], start_z - 0.1)
                    self.assertTrue(any(current is bomb for current in bombs))
                    self.assertGreater(bomb.age, release_age)
                    self.assertAlmostEqual(bomb.position[2], start_z, delta=0.02)
                self.assertEqual(self.health.total, 150)

    def test_actual_concussion_rejects_cooldown_then_reuses(self):
        positions = {owner: tuple(owner.mover.position) for owner in (self.player, self.pharah)}
        instances = self.pharah.body_script.component.instances.values()
        ability = next(i for i in instances if i.graph.index == 0x007B)
        volley = ability.state(0)

        def press():
            self.run.step({self.pharah: (*self.incoming, 16)})
            for _ in range(50):
                self.run.step({self.pharah: (*self.incoming, 0)})

        press()
        self.assertEqual(volley.counter, 1)
        self.assertLess(self.player.mover.position[2], positions[self.player][2] - 3)
        self.assertGreater(ability.vars[61].value(), 8)
        press()
        self.assertEqual(volley.counter, 1)
        self.assertFalse(self.fight.rockets)
        for _ in range(650):
            if ability.vars[61].value() == 0:
                break
            self.run.step({})
        self.assertEqual(ability.vars[61].value(), 0)
        for owner, position in positions.items():
            owner.mover.position[:] = position
            owner.mover.body = None
        press()
        self.assertEqual(volley.counter, 2)
        self.assertLess(self.player.mover.position[2], positions[self.player][2] - 3)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())

    @unittest.skipUnless(deflection.TAGS_PATH, "external projectile tags required")
    def test_actual_matrix_absorbs_concussion_without_impulse(self):
        mech_tests.TracerDvaMechTests.setUpClass()
        for gate in ("hostile", "inactive"):
            with self.subTest(gate=gate):
                self.setUp()
                defender, matrix = self.matrix_defender(gate)
                before = defender.mover.position[2]
                self.run.step({**matrix, self.pharah: (*self.incoming, 16)})
                observed = False
                for _ in range(55):
                    self.run.step({**matrix, self.pharah: (*self.incoming, 0)})
                    observed |= bool(self.fight.rockets)
                self.assertTrue(observed)
                self.assertFalse(self.fight.rockets)
                if gate == "hostile":
                    self.assertAlmostEqual(defender.mover.position[2], before, delta=0.02)
                else:
                    self.assertLess(defender.mover.position[2], before - 2)
                self.assertEqual(self.case.health(defender).total, 600)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())

    @unittest.skipUnless(deflection.TAGS_PATH, "external projectile tags required")
    def test_actual_concussion_deflect_redirects_impulse_and_grasp_grants_no_shield(self):
        for hero, button in ((GENJI, 16), (sigma_grasp.HERO, 8)):
            with self.subTest(hero=hero):
                self.setUp()
                defender = self.shield_owner(hero, offset=3, allied=False)
                facing = aim(self.fight._eye(defender), self.fight._eye(self.pharah))
                self.run.step({defender: (*facing, button)})
                before = self.pharah.mover.position[2]
                tracer_z = self.player.mover.position[2]
                defender_z = defender.mover.position[2]
                self.run.step({self.pharah: (*self.incoming, 16), defender: (*facing, 0)})
                launched = None
                for _ in range(58):
                    self.run.step({defender: (*facing, 0), self.pharah: (*self.incoming, 0)})
                    if self.fight.rockets:
                        launched = self.fight.rockets[0]
                self.assertIsNotNone(launched)
                self.assertFalse(self.fight.rockets)
                self.assertAlmostEqual(self.player.mover.position[2], tracer_z, delta=0.02)
                self.assertEqual(self.health.total, 150)
                self.assertEqual(self.case.vitals.total, 200)
                self.assertFalse(self.hits())
                self.assertFalse(defender.client.messages(combat.HIT))
                if hero == GENJI:
                    self.assertIs(launched.shooter, defender)
                    self.assertEqual(launched.team, defender.team)
                    self.assertGreater(self.pharah.mover.position[2], before + 3)
                else:
                    self.assertIs(launched.shooter, self.pharah)
                    self.assertAlmostEqual(defender.mover.position[2], defender_z, delta=0.02)
                    instances = defender.body_script.component.instances.values()
                    grasp = next(i for i in instances if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value() or 0, 0)
                    for _ in range(150):
                        self.run.step({})
                    self.assertFalse(grasp.state(1).active)
                    self.assertFalse(self.case.health(defender).temporary_shields)

    def test_actual_rocket_direct_splash_death_and_tracer_return_fire(self):
        self.run.step({self.player: (*self.case.aim, FIRE)})
        self.assertEqual(self.case.vitals.total, 188)
        self.rocket()
        self.assertEqual(self.health.total, 30)
        self.assertEqual([hit["+0x4"] for hit in self.hits()], [40, 80])
        self.assertTrue(all(hit["+0x8"] == 0 for hit in self.hits()))
        self.rocket()
        self.assertTrue(self.health.dead)
        self.assertTrue(self.hits()[-1]["+0x8"] & combat.KILLING)

    def test_actual_rocket_rejects_miss_range_cover_ally_and_recall(self):
        for gate in ("miss", "range", "cover", "ally", "recall"):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.player.mover.position
                match = self.run.match
                angles = None
                if gate == "miss":
                    angles = (16384, 0)
                elif gate == "range":
                    self.pharah.mover.position[2] = z + 105
                    self.pharah.mover.body = None
                    angles = aim(self.fight._eye(self.pharah), (x, y + 1, z))
                elif gate == "cover":
                    match.collision_world = World(box(x-4, x+4, y, y+4, z+1, z+1.2))
                elif gate == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.pharah.team = self.player.team
                self.rocket(buttons=16 if gate == "recall" else 0,
                            angles=angles, frames=190)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())
                self.assertFalse(tracer.recalling(self.player))

    def test_actual_rocket_wall_impact_deals_splash_without_direct_hit(self):
        x, y, z = self.player.mover.position
        self.player.mover.position[0] += 0.7
        self.player.mover.body = None
        self.run.match.collision_world = World(box(x-4, x+4, y, y+4, z-0.4, z-0.2))
        self.rocket()
        hit, = self.hits()
        self.assertEqual(hit["+0x4"], 80)
        self.assertEqual(self.health.total, 70)

    def test_actual_blink_dodges_an_in_flight_rocket(self):
        start_x = self.player.mover.position[0]
        self.run.step({self.pharah: (*self.incoming, FIRE)})
        self.assertEqual(len(self.fight.rockets), 1)
        self.run.step({self.player: (16384, 0, 8)})
        self.assertGreater(self.player.mover.position[0] - start_x, 6)
        for _ in range(190):
            self.run.step({})
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())

    def matrix_defender(self, gate):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.player.team, self.pharah.team = 0, 1
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.player, dva.HERO)
        self.assertTrue(match.switch_hero(defender, replace(find_hero("dva"), body=dva.MECH)))
        defender.team = self.pharah.team if gate == "ally" else self.player.team
        for _ in range(110):
            self.run.step({})
        x, y, z = self.player.mover.position
        defender.mover.position[:] = (x, y, z + 3)
        defender.mover.body = None
        faced = self.player if gate == "rear" else self.pharah
        facing = aim(self.fight._eye(defender), self.fight._eye(faced))
        matrix = {defender: (*facing, 0 if gate == "inactive" else 2)}
        for _ in range(10):
            self.run.step(matrix)
        instances = defender.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == 0x02CC)
        self.assertEqual(instance.state(3).active, gate != "inactive")
        return defender, matrix

    def test_actual_matrix_consumes_hostile_primary_rocket_when_active(self):
        mech_tests.TracerDvaMechTests.setUpClass()
        for gate in ("hostile", "inactive", "ally", "rear"):
            with self.subTest(gate=gate):
                self.setUp()
                defender, matrix = self.matrix_defender(gate)
                self.run.step({**matrix, self.pharah: (*self.incoming, FIRE)})
                rocket, = self.fight.rockets
                self.assertIsNone(rocket.definition)
                for _ in range(24):
                    self.run.step(matrix)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(bool(self.hits()), gate != "hostile")
                defender_health = self.case.health(defender).total
                if gate in ("inactive", "rear"):
                    self.assertLess(defender_health, 600)
                else:
                    self.assertEqual(defender_health, 600)
                if gate not in ("inactive", "rear"):
                    self.assertEqual(self.health.total, 30 if gate == "ally" else 150)

    def test_actual_genji_reflects_primary_rocket_and_owns_return_damage(self):
        for gate in ("front", "rear", "ally"):
            with self.subTest(gate=gate):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                self.player.team, self.pharah.team = 0, 1
                genji, _ = support_tests.PulseDeflectTests().support(self.run, self.player, GENJI)
                genji.team = self.pharah.team if gate == "ally" else self.player.team
                for _ in range(30):
                    self.run.step({})
                x, y, z = self.player.mover.position
                genji.mover.position[:] = (x, y, z + 3)
                genji.mover.body = None
                faced = self.player if gate == "rear" else self.pharah
                facing = aim(self.fight._eye(genji), self.fight._eye(faced))
                self.run.step({genji: (*facing, 16)})
                for _ in range(8):
                    self.run.step({genji: (*facing, 0)})
                self.run.step({self.pharah: (*self.incoming, FIRE)})
                rocket, = self.fight.rockets
                launch_life = rocket.left
                for _ in range(50):
                    self.run.step({genji: (*facing, 0)})
                self.assertFalse(self.fight.rockets)
                if gate == "front":
                    self.assertIs(rocket.shooter, genji)
                    self.assertEqual(rocket.team, genji.team)
                    self.assertLess(rocket.left, launch_life)
                    self.assertEqual(self.case.vitals.total, 80)
                    self.assertEqual(self.case.health(genji).total, 200)
                    self.assertEqual(self.health.total, 150)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
                    hits = genji.client.messages(combat.HIT)
                    self.assertEqual([n["+0x78"]["+0x4"] for n in hits], [40, 80])
                    self.assertFalse(self.hits())
                else:
                    self.assertIs(rocket.shooter, self.pharah)
                    self.assertEqual(self.case.vitals.total, 200)
                    self.assertEqual(self.case.health(genji).total, 80 if gate == "rear" else 200)
                    self.assertEqual(self.health.total, 150 if gate == "rear" else 30)

    def test_actual_matrix_consumes_reflected_rocket_after_team_changes(self):
        mech_tests.TracerDvaMechTests.setUpClass()
        genji, _ = support_tests.PulseDeflectTests().support(self.run, self.player, GENJI)
        defender, _ = self.matrix_defender("ally")
        genji.team = self.player.team
        x, y, z = self.player.mover.position
        genji.mover.position[:] = (x, y, z + 3)
        genji.mover.body = None
        defender.mover.position[:] = (x, y, z + 6)
        defender.mover.body = None
        facing = aim(self.fight._eye(genji), self.fight._eye(self.pharah))
        matrix_facing = aim(self.fight._eye(defender), self.fight._eye(genji))
        matrix = {defender: (*matrix_facing, 2)}
        self.run.step({**matrix, genji: (*facing, 16)})
        for _ in range(8):
            self.run.step({**matrix, genji: (*facing, 0)})
        self.run.step({**matrix, self.pharah: (*self.incoming, FIRE)})
        rocket, = self.fight.rockets
        for _ in range(45):
            self.run.step({**matrix, genji: (*facing, 0)})
        self.assertFalse(self.fight.rockets)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.team, genji.team)
        self.assertEqual(self.case.vitals.total, 200)
        self.assertEqual(self.case.health(defender).total, 600)
        self.assertEqual(self.case.health(genji).total, 200)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_actual_grasp_consumes_primary_and_grants_scoped_shields(self):
        for gate in ("front", "rear", "ally"):
            with self.subTest(gate=gate):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                self.player.team, self.pharah.team = 0, 1
                sigma, _ = support_tests.PulseDeflectTests().support(self.run, self.player, sigma_grasp.HERO)
                sigma.team = self.pharah.team if gate == "ally" else self.player.team
                for _ in range(30):
                    self.run.step({})
                x, y, z = self.player.mover.position
                sigma.mover.position[:] = (x, y, z + 3)
                sigma.mover.body = None
                faced = self.player if gate == "rear" else self.pharah
                facing = aim(self.fight._eye(sigma), self.fight._eye(faced))
                self.run.step({sigma: (*facing, 8)})
                instances = sigma.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == sigma_grasp.GRASP)
                self.assertTrue(instance.state(1).active)
                self.run.step({self.pharah: (*self.incoming, FIRE), sigma: (*facing, 0)})
                self.assertEqual(len(self.fight.rockets), 1)
                for _ in range(35):
                    self.run.step({sigma: (*facing, 0)})
                self.assertFalse(self.fight.rockets)
                vitals = self.case.health(sigma)
                self.assertEqual(instance.vars[21312].value() or 0, 120 if gate == "front" else 0)
                self.assertEqual(bool(self.hits()), gate != "front")
                self.assertEqual(self.health.total, 30 if gate == "ally" else 150)
                if gate != "front":
                    self.assertEqual(vitals.total, 280 if gate == "rear" else 400)
                    continue
                self.assertEqual(vitals.total, 400)
                for _ in range(150):
                    if sigma_grasp.SHIELD_TAG in vitals.temporary_shields:
                        break
                    self.run.step({sigma: (*facing, 0)})
                self.assertFalse(instance.state(1).active)
                pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertEqual(pool.now, 72)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
                self.rocket()
                self.assertEqual(pool.now, 0)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 52)
                self.assertEqual(vitals.total, 352)
                self.assertEqual(self.health.total, 150)

    def shield_owner(self, hero, *, offset, allied):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.player.team, self.pharah.team = 0, 1
        owner, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero)
        owner.team = self.pharah.team if allied else self.player.team
        for _ in range(30):
            self.run.step({})
        x, y, z = self.player.mover.position
        owner.mover.position[:] = (x, y, z + offset)
        owner.mover.body = None
        return owner

    def projected_barrier(self):
        zarya = self.shield_owner(ZARYA, offset=0, allied=False)
        zarya.mover.position[0] += 4
        facing = aim(self.fight._eye(zarya), self.fight._eye(self.player))
        self.run.step({zarya: (*facing, 16)})
        pet = zarya_barrier.active(self.player)
        self.assertIsNotNone(pet)
        self.assertEqual(pet.instance.component.entity, zarya.body)
        self.assertEqual(zarya_barrier.barrier_health(pet, self.health), 200)
        return zarya, pet

    def test_actual_projected_barrier_breaking_rocket_has_no_spill(self):
        zarya, pet = self.projected_barrier()
        energy = zarya.body_script.component.entity_var(87)
        self.rocket()
        self.assertEqual(zarya_barrier.barrier_health(pet, self.health), 80)
        self.assertAlmostEqual(energy.value(), 24, places=4)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
        self.run.step({self.pharah: (*self.incoming, FIRE)})
        self.assertEqual(len(self.fight.rockets), 1)
        for _ in range(30):
            before_impact = energy.value()
            self.run.step({})
            if not self.fight.rockets:
                break
        self.assertFalse(self.fight.rockets)
        self.assertIsNone(zarya_barrier.active(self.player))
        self.assertFalse(self.health.particle_barriers)
        self.assertAlmostEqual(energy.value() - before_impact, 16, places=4)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
        for _ in range(65):
            self.run.step({})
        self.rocket()
        self.assertEqual(self.health.total, 30)

    def test_actual_projected_barrier_spends_wall_splash_without_direct_hit(self):
        x, y, z = self.player.mover.position
        self.player.mover.position[0] += 0.7
        self.player.mover.body = None
        zarya, pet = self.projected_barrier()
        self.run.match.collision_world = World(box(x-4, x+4, y, y+4, z-0.4, z-0.2))
        self.rocket()
        self.assertEqual(zarya_barrier.barrier_health(pet, self.health), 120)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 16, places=4)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())

    def test_actual_winston_barrier_occludes_first_barrage_rocket(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                self.setUp()
                winston = self.shield_owner(WINSTON, offset=-3.5, allied=allied)
                self.run.step({winston: (0, 0, 16)})
                for _ in range(8):
                    self.run.step({})
                bubble = self.fight.winston_barriers.actors[winston.body]
                self.assertEqual(bubble.health, 700)
                script = self.pharah.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.run.step({self.pharah: (*self.incoming, 32)})
                first, = self.fight.rockets
                self.assertEqual(first.definition, combat.PHARAH_BARRAGE)
                for _ in range(32):
                    self.run.step({})
                    if not any(rocket is first for rocket in self.fight.rockets):
                        break
                self.assertFalse(any(rocket is first for rocket in self.fight.rockets))
                self.assertEqual(bubble.health, 700 if allied else 660)
                self.assertEqual(self.health.total, 110 if allied else 150)
                self.assertEqual(bool(self.hits()), allied)

    @unittest.skipUnless(deflection.TAGS_PATH, "external projectile tags required")
    def test_actual_barrage_first_rocket_deflect_and_grasp(self):
        for hero, button in ((GENJI, 16), (sigma_grasp.HERO, 8)):
            with self.subTest(hero=hero):
                self.setUp()
                defender = self.shield_owner(hero, offset=3, allied=False)
                facing = aim(self.fight._eye(defender), self.fight._eye(self.pharah))
                self.run.step({defender: (*facing, button)})
                for _ in range(8):
                    self.run.step({defender: (*facing, 0)})
                script = self.pharah.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.run.step({defender: (*facing, 0), self.pharah: (*self.incoming, 32)})
                first, = self.fight.rockets
                for _ in range(32):
                    self.run.step({defender: (*facing, 0)})
                    if not any(rocket is first for rocket in self.fight.rockets):
                        break
                self.assertFalse(any(rocket is first for rocket in self.fight.rockets))
                self.assertFalse(self.hits())
                self.assertEqual(self.health.total, 150)
                if hero == GENJI:
                    self.assertIs(first.shooter, defender)
                    self.assertEqual(self.case.health(defender).total, 200)
                    self.assertEqual(self.case.vitals.total, 160)
                else:
                    instances = defender.body_script.component.instances.values()
                    grasp = next(i for i in instances if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value(), 40)
                    self.assertEqual(self.case.health(defender).total, 400)

    @unittest.skipUnless(deflection.TAGS_PATH, "external projectile tags required")
    def test_actual_matrix_consumes_consecutive_barrage_rockets(self):
        mech_tests.TracerDvaMechTests.setUpClass()
        for gate in ("hostile", "inactive"):
            with self.subTest(gate=gate):
                self.setUp()
                defender, matrix = self.matrix_defender(gate)
                script = self.pharah.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.run.step({**matrix, self.pharah: (*self.incoming, 32)})
                observed = {}
                for _ in range(36):
                    for rocket in self.fight.rockets:
                        observed[id(rocket)] = rocket
                    self.run.step({**matrix, self.pharah: (*self.incoming, 0)})
                retired = observed.keys() - {id(rocket) for rocket in self.fight.rockets}
                self.assertGreaterEqual(len(retired), 3)
                self.assertTrue(all(observed[key].definition == combat.PHARAH_BARRAGE for key in retired))
                if gate == "hostile":
                    self.assertEqual(self.case.health(defender).total, 600)
                    self.assertEqual(self.health.total, 150)
                    self.assertFalse(self.hits())
                else:
                    self.assertLess(self.case.health(defender).total, 600)
                    self.assertTrue(self.hits())

    def test_actual_projected_barrier_breaking_barrage_rocket_has_no_spill(self):
        zarya, pet = self.projected_barrier()
        script = self.pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.pharah: (*self.incoming, 32)})
        pools = [200]
        for _ in range(60):
            self.run.step({self.pharah: (*self.incoming, 0)})
            self.assertEqual(self.health.total, 150)
            if not pet.active:
                pools.append(0)
                break
            remaining = zarya_barrier.barrier_health(pet, self.health)
            if remaining != pools[-1]:
                pools.append(remaining)
        self.assertEqual(pools, [200, 160, 120, 80, 40, 0])
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 40, places=4)
        self.assertIsNone(zarya_barrier.active(self.player))
        self.assertFalse(self.hits())
        for _ in range(16):
            self.run.step({self.pharah: (*self.incoming, 0)})
            if self.health.total < 150:
                break
        self.assertEqual(self.health.total, 110)
        self.assertEqual([hit["+0x4"] for hit in self.hits()], [10, 30])

    def test_actual_winston_barrier_occludes_primary_rocket_and_splash(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                self.setUp()
                winston = self.shield_owner(WINSTON, offset=-3.5, allied=allied)
                self.run.step({winston: (0, 0, 16)})
                for _ in range(8):
                    self.run.step({})
                bubble = self.fight.winston_barriers.actors[winston.body]
                self.assertEqual(bubble.health, 700)
                self.rocket()
                self.assertEqual(bubble.health, 700 if allied else 580)
                self.assertEqual(self.health.total, 30 if allied else 150)
                self.assertEqual(bool(self.hits()), allied)
                if not allied:
                    for expected in (460, 340, 220, 100, 0):
                        self.rocket()
                        self.assertEqual(bubble.health, expected)
                        self.assertEqual(self.health.total, 150)
                        self.assertFalse(self.hits())
                    instances = self.pharah.body_script.component.instances.values()
                    launcher = next(i for i in instances if i.graph.index == 0x0023)
                    self.assertEqual(launcher.vars[53].value(), 0)
                    self.run.step({self.pharah: (*self.incoming, FIRE)})
                    self.assertFalse(self.fight.rockets)
                    for _ in range(160):
                        self.run.step({})
                    self.assertEqual(launcher.vars[53].value(), 6)
                    self.rocket()
                    self.assertEqual(self.health.total, 30)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "external C015 model required")
    def test_actual_sigma_barrier_blocks_primary_splash_until_recalled(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                self.setUp()
                sigma = self.shield_owner(sigma_barrier.HERO, offset=-1, allied=allied)
                for _ in range(8):
                    self.run.step({sigma: (0, 0, 2)})
                for _ in range(27):
                    self.run.step({})
                barrier, = self.fight.sigma_barriers.values()
                self.assertTrue(barrier.active and barrier.child_request_seen)
                self.assertEqual(barrier.phase, 3)
                self.assertEqual(barrier.vitals.total, 700)
                separation = barrier.position[2] - self.player.mover.position[2]
                self.assertGreater(separation, 0)
                self.assertLess(separation, 2.5)
                self.rocket()
                self.assertEqual(barrier.vitals.total, 700 if allied else 580)
                self.assertEqual(self.health.total, 30 if allied else 150)
                self.assertEqual(self.case.health(sigma).total, 400)
                self.assertEqual(bool(self.hits()), allied)
                if not allied:
                    self.run.step({sigma: (0, 0, 2)})
                    for _ in range(2):
                        self.run.step({})
                    self.assertFalse(barrier.active)
                    self.run.step({sigma: (0, 0, 2)})
                    self.run.step({})
                    self.assertFalse(barrier.active)
                    self.rocket()
                    self.assertEqual(self.health.total, 30)
                    for _ in range(160):
                        self.run.step({})
                    for _ in range(8):
                        self.run.step({sigma: (0, 0, 2)})
                    for _ in range(27):
                        self.run.step({})
                    self.assertTrue(barrier.active)
                    before = barrier.vitals.total
                    self.rocket()
                    self.assertEqual(barrier.vitals.total, before - 120)
                    self.assertEqual(self.health.total, 30)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "external barrier mesh required")
    def test_actual_reinhardt_barrier_occludes_primary_rocket_and_splash(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                self.setUp()
                rein = self.shield_owner(REINHARDT, offset=1, allied=allied)
                raised = {rein: (0, 0, 2)}
                for _ in range(18):
                    self.run.step(raised)
                barrier = self.fight.reinhardt_barriers.states[rein.body]
                self.assertTrue(barrier.active)
                self.assertEqual(barrier.current, 1600)
                self.run.step({**raised, self.pharah: (*self.incoming, FIRE)})
                self.assertEqual(len(self.fight.rockets), 1)
                for _ in range(28):
                    self.run.step(raised)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(barrier.current, 1600 if allied else 1480)
                self.assertEqual(self.case.health(rein).total, 500)
                self.assertEqual(self.health.total, 30 if allied else 150)
                self.assertEqual(bool(self.hits()), allied)
