"""Actual Tracer and Reinhardt attacks against Orisa's 0CF4 Fortify."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim

from ow174.game import body_protection, combat, orisa_barrier, zarya_barrier
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_COST


class TracerOrisaFortifyTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.orisa, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[orisa_barrier.HERO]))
        self.x, self.y, self.z = self.tracer.mover.position
        self.move(self.orisa, (self.x, self.y, self.z + 3))
        for _ in range(20):
            self.run.step({})
        self.health = self.vitals(self.orisa)

    @staticmethod
    def move(player, position):
        player.mover.position = list(position)
        player.mover.body = None

    def vitals(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is player))

    def fortify(self):
        self.run.step({self.orisa: (0, 0, 8)})
        state = body_protection.fortify(self.orisa)
        self.assertIsNotNone(state)
        self.assertTrue(state.instance.component.entity_var(9161).value())
        return state

    def wait_for_end(self, state):
        for _ in range(260):
            if not state.active:
                break
            self.run.step({})
        self.assertFalse(state.active)
        self.assertIsNone(body_protection.fortify(self.orisa))
        self.assertFalse(state.instance.component.entity_var(9161).value())

    def test_fortify_reduces_pistol_damage_until_expiry_and_reuses_after_cooldown(self):
        state = self.fortify()
        facing = aim(self.fight._eye(self.tracer), (self.x, self.y + 1, self.z + 3))
        self.run.step({self.tracer: (*facing, FIRE)})
        self.assertAlmostEqual(self.health.total, 446.4, places=4)
        self.wait_for_end(state)
        self.run.step({self.tracer: (*facing, FIRE)})
        self.assertAlmostEqual(self.health.total, 440.4, places=4)
        self.run.step({})
        self.run.step({self.orisa: (0, 0, 8)})
        self.assertIsNone(body_protection.fortify(self.orisa))
        ability = state.instance.states[0]
        self.assertGreater(ability.remaining(), 0)
        for _ in range(660):
            self.run.step({})
            if not ability.remaining():
                break
        self.assertEqual(ability.remaining(), 0)
        self.assertIs(self.fortify(), state)

    def test_fortify_suppresses_authored_soldier_critical_until_expiry(self):
        soldier = self.tracer
        self.assertTrue(self.match.switch_hero(soldier, heroes()[SOLDIER]))
        self.move(soldier, (self.x, self.y, self.z))
        for _ in range(20):
            self.run.step({})
        x, y, z = self.fight._eye(self.orisa)
        facing = aim(self.fight._eye(soldier), (x, y + 0.2, z))
        state = self.fortify()
        self.run.step({soldier: (*facing, FIRE)})
        self.assertAlmostEqual(self.health.total, 443, places=4)
        self.assertFalse(soldier.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"] & combat.CRITICAL)
        self.wait_for_end(state)
        self.run.step({soldier: (*facing, FIRE)})
        self.assertAlmostEqual(self.health.total, 408, places=4)
        self.assertTrue(soldier.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"] & combat.CRITICAL)

    def test_actual_tracer_headshots_resume_critical_damage_after_fortify(self):
        x, y, z = self.fight._eye(self.orisa)
        facing = aim(self.fight._eye(self.tracer), (x, y + 0.2, z))
        state = self.fortify()
        self.run.step({self.tracer: (*facing, FIRE)})
        self.assertAlmostEqual(self.health.total, 446.4, places=4)
        self.assertFalse(self.tracer.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"] & combat.CRITICAL)
        self.wait_for_end(state)
        self.run.step({self.tracer: (*facing, FIRE)})
        self.assertAlmostEqual(self.health.total, 432.4, places=4)
        self.assertTrue(self.tracer.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"] & combat.CRITICAL)

    def test_fortify_reduces_tracer_melee_before_armour(self):
        self.move(self.orisa, (self.x, self.y, self.z + 2))
        for _ in range(10):
            self.run.step({})
        self.fortify()
        self.run.step({self.tracer: (0, 0, 0x800)})
        for _ in range(38):
            self.run.step({})
        self.assertAlmostEqual(self.health.total, 437, places=4)

    def test_zarya_barrier_absorbs_first_pellet_before_fortify_reduces_second(self):
        zarya, _ = support_tests.PulseDeflectTests().support(
            self.run, self.orisa, hero=0x02E0000000000068,
        )
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.orisa.team = zarya.team = 1
        self.tracer.team = 0
        self.move(zarya, (self.x + 5, self.y, self.z + 3))
        for _ in range(24):
            self.run.step({})
        self.fortify()
        facing = aim(self.fight._eye(zarya), self.fight._eye(self.orisa))
        self.run.step({zarya: (*facing, 16)})
        pet = zarya_barrier.active(self.orisa)
        self.assertIsNotNone(pet)
        self.health.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = 6
        sight = aim(self.fight._eye(self.tracer), (self.x, self.y + 1, self.z + 3))
        self.run.step({self.tracer: (*sight, FIRE)})
        self.assertIsNone(zarya_barrier.active(self.orisa))
        self.assertAlmostEqual(self.health.total, 448.2, places=4)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 1.2, places=5)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 1)

    def test_fortify_activated_after_pulse_contact_reduces_detonation_damage(self):
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (0, 0, 32)})
        for _ in range(60):
            if any(bomb.stuck for bomb in self.fight.pulse_bombs.bombs):
                break
            self.run.step({})
        bomb, = self.fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, self.orisa.body)
        self.assertEqual(self.health.total, 447.5)
        state = self.fortify()
        for _ in range(70):
            self.run.step({})
        self.assertTrue(state.active)
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertAlmostEqual(self.health.total, 242.5, places=4)

    def test_fortify_reduces_earthshatter_damage_and_rejects_stun(self):
        caster, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E0000000000007,
        )
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.orisa.team = self.tracer.team = 0
        caster.team = 1
        self.move(caster, (self.x, self.y, self.z - 3))
        self.move(self.tracer, (self.x, self.y, self.z + 5))
        tracer_health = self.vitals(self.tracer)
        state = self.fortify()
        self.cast_earthshatter(caster)
        self.assertAlmostEqual(self.health.total, 425, places=4)
        self.assertFalse(self.fight.is_stunned(self.orisa))
        self.assertTrue(state.active)
        self.assertEqual(tracer_health.total, 100)
        self.assertTrue(self.fight.is_stunned(self.tracer))
        self.wait_for_end(state)
        self.cast_earthshatter(caster)
        self.assertAlmostEqual(self.health.total, 380, places=4)
        self.assertTrue(self.fight.is_stunned(self.orisa))

    def test_sleep_dart_keeps_damage_but_cannot_put_fortified_orisa_to_sleep(self):
        for fortified in (False, True):
            with self.subTest(fortified=fortified):
                self.setUp()
                self.check_sleep_dart(fortified)

    def check_sleep_dart(self, fortified):
        ana = self.attacker(0x02E000000000013B)
        state = self.fortify()
        if not fortified:
            self.wait_for_end(state)
        rocket = self.fire_projectile_at_orisa(ana, 8, aim_height=1.1)
        self.assertEqual(rocket.definition, combat.SLEEP_DART)
        self.assertAlmostEqual(self.health.total, 448.5 if fortified else 447.5, places=4)
        self.assertEqual(self.fight.is_stunned(self.orisa), not fortified)
        self.assertEqual(self.health.sleep_until > self.fight.tick, not fortified)
        self.check_orisa_return_fire(expected=fortified)

    def test_accretion_keeps_damage_but_cannot_stun_or_move_fortified_orisa(self):
        for fortified in (False, True):
            with self.subTest(fortified=fortified):
                self.setUp()
                self.check_accretion(fortified)

    def check_accretion(self, fortified):
        sigma = self.attacker(0x02E000000000023B)
        state = self.fortify()
        if not fortified:
            self.wait_for_end(state)
        start = tuple(self.orisa.mover.position)
        rocket = self.fire_projectile_at_orisa(sigma, 16, aim_height=0.7)
        self.assertIsNotNone(rocket.accretion_source)
        self.assertAlmostEqual(self.health.total, 418 if fortified else 390, places=4)
        self.assertEqual(self.fight.is_stunned(self.orisa), not fortified)
        self.assertEqual(bool(self.orisa.mover.external_impulse), not fortified)
        if fortified:
            self.assertEqual(tuple(self.orisa.mover.position), start)
        self.check_orisa_return_fire(expected=fortified)

    def test_fortify_clears_partial_frost_and_freeze_can_build_again_after_expiry(self):
        mei = self.attacker(0x02E00000000000DD, distance=3)
        sight = aim(self.fight._eye(mei), (self.x, self.y + 1, self.z + 3))
        for _ in range(35):
            self.run.step({mei: (*sight, FIRE)})
        body = self.orisa.body
        self.assertGreater(self.fight.frost_hits[body][0], 0.2)
        self.assertFalse(self.fight.is_stunned(self.orisa))

        state = self.fortify()
        before = self.health.total
        for _ in range(113):
            self.run.step({mei: (*sight, FIRE)})
            self.assertNotIn(body, self.fight.frost_hits)
            self.assertFalse(self.fight.is_stunned(self.orisa))
        self.assertTrue(state.active)
        self.assertLess(self.health.total, before)
        self.wait_for_end(state)
        for _ in range(115):
            self.run.step({mei: (*sight, FIRE)})
            if self.fight.is_stunned(self.orisa):
                break
        self.assertTrue(self.fight.is_stunned(self.orisa))
        self.assertEqual(self.fight.frost_hits[body][0], 1)

    def attacker(self, hero, *, distance=8):
        caster, _ = support_tests.PulseDeflectTests().support(self.run, self.orisa, hero=hero)
        self.move(caster, (self.x, self.y, self.z + 3 - distance))
        self.move(self.tracer, (self.x + 7, self.y, self.z + 3))
        for _ in range(30):
            self.run.step({})
        return caster

    def fire_projectile_at_orisa(self, caster, button, *, aim_height):
        x, y, z = self.orisa.mover.position
        sight = aim(self.fight._eye(caster), (x, y + aim_height, z))
        self.run.step({caster: (*sight, button)})
        for _ in range(60):
            if self.fight.rockets:
                break
            self.run.step({caster: (*sight, 0)})
        rocket, = self.fight.rockets
        for _ in range(42):
            self.run.step({caster: (*sight, 0)})
            if rocket not in self.fight.rockets:
                break
        self.assertNotIn(rocket, self.fight.rockets)
        return rocket

    def check_orisa_return_fire(self, *, expected):
        x, y, z = self.tracer.mover.position
        sight = aim(self.fight._eye(self.orisa), (x, y + 1, z))
        self.run.step({self.orisa: (*sight, FIRE)})
        for _ in range(24):
            self.run.step({self.orisa: (*sight, 0)})
        self.assertEqual(self.vitals(self.tracer).total, 139 if expected else 150)

    def cast_earthshatter(self, caster):
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({caster: (0, 0, 32)})
        for _ in range(75):
            self.run.step({})
        self.assertFalse(self.fight.earthshatters.waves)
