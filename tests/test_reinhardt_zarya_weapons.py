"""Actual Particle Cannon commands against Reinhardt's armour and field."""

import unittest

import test_game_reinhardt as fixtures
import test_reinhardt_ana_biotic as ana_fixture
from test_game_combat import aim

from ow174.game import defenses, deflection, sigma_grasp, stats, zarya_barrier, zarya_weapons
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


class ReinhardtZaryaWeaponsTests(unittest.TestCase):
    def arena(self, distance=6):
        case = ana_fixture.ReinhardtAnaBioticTests()
        self.addCleanup(case.doCleanups)
        case.setup_teams()
        self.assertTrue(case.match.switch_hero(case.enemy, heroes()[zarya_barrier.HERO]))
        case.place(case.enemy, (0, 1, distance))
        case.advance(20)
        self.case = case
        self.gun = next(i for i in case.enemy.body_script.component.instances.values()
                        if i.graph.index == zarya_weapons.GRAPH)
        return case

    def step(self, frames=1, *, attack=0, rein=0, miss=False):
        case = self.case
        direction = aim(case.fight._eye(case.enemy), (0, 2.3, 0)) if not miss else (16384, 0)
        for _ in range(frames):
            case.run.step({case.enemy: (*direction, attack), case.rein: (0, 0, rein)})

    def rate(self):
        state = self.gun.states[10]
        self.assertTrue(state.active)
        return expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))

    def test_actual_beam_selects_reinhardt_and_releases_without_late_damage(self):
        case = self.arena()
        self.step(10, attack=1)
        self.assertEqual(self.gun.vars[536].value(), expr.Entity(case.rein.body))
        self.assertEqual(self.rate(), 75)
        amount = self.rate() * .016 / 2
        self.assertAlmostEqual(case.victim.total, 500 - 10 * amount)
        self.assertLess(self.gun.vars[53].value(), 100)
        before = case.victim.total
        zarya_weapons.beam(case.fight, case.enemy, Command(case.run.frame), case.enemy.body_script)
        self.assertEqual(case.victim.total, before)
        self.step(35)
        self.assertEqual(case.victim.total, before)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_actual_beam_and_grenade_consume_front_barrier(self):
        for attack in (1, 2):
            with self.subTest(attack=attack):
                case = self.arena()
                self.step(20, rein=2)
                field = case.fight.barriers[case.rein.body]
                self.assertTrue(field.active)
                if attack == 1:
                    self.step(10, attack=1, rein=2)
                    expected = 1600
                    for _ in range(10):
                        expected = expr.f32(expected - self.rate() * .016)
                else:
                    self.step(attack=2, rein=2)
                    self.step(45, rein=2)
                    expected = 1553
                self.assertEqual(field.current, expected)
                self.assertEqual(case.victim.total, 500)

    def test_actual_grenade_spends_25_ammo_and_hits_armour(self):
        case = self.arena()
        self.step(attack=2)
        self.assertEqual(self.gun.vars[53].value(), 75)
        self.assertEqual(case.fight.rockets[0].definition, zarya_weapons.SECONDARY)
        self.step(45)
        self.assertEqual(case.victim.total, 458)
        self.assertFalse(case.fight.rockets)

    def test_beam_respects_team_range_static_wall_and_miss(self):
        for reason in ('ally', 'range', 'wall', 'miss'):
            with self.subTest(reason=reason):
                case = self.arena(distance=16 if reason == 'range' else 6)
                if reason == 'ally':
                    case.enemy.team = case.rein.team
                elif reason == 'wall':
                    fixtures.charge_wall(case, 3)
                self.step(35, attack=1, miss=reason == 'miss')
                self.assertEqual(case.victim.total, 500)

    def test_fire_strike_into_personal_barrier_increases_authored_beam_rate(self):
        case = self.arena()
        self.step(attack=1)
        base = self.rate()
        self.step(10)
        self.step(attack=8)
        self.step(rein=16)
        self.step(53)
        energy = case.enemy.body_script.component.entity_var(87).value()
        self.assertAlmostEqual(energy, 20, places=4)
        before = case.victim.total
        self.step(10, attack=1)
        rate = self.rate()
        self.assertGreater(rate, base)
        self.assertEqual(rate, 94)
        self.assertAlmostEqual(case.victim.total, before - 10 * rate * .016 / 2)
        self.step(10)
        before = case.victim.total
        self.step(attack=2)
        grenade, = (r for r in case.fight.rockets if r.definition == zarya_weapons.SECONDARY)
        self.assertAlmostEqual(grenade.blast.amount, 56.6, places=4)
        self.step(45)
        self.assertAlmostEqual(case.victim.total, before - grenade.blast.amount + 5)

    def test_actual_earthshatter_interrupts_held_beam_then_fire_recovers(self):
        case = self.arena()
        self.step(12, attack=1)
        self.assertLess(case.victim.total, 500)
        case.rein.body_script.set_ult_charge(1540)
        self.step(attack=1, rein=32)
        for _ in range(60):
            self.step(attack=1)
            if case.fight.is_stunned(case.enemy):
                break
        self.assertTrue(case.fight.is_stunned(case.enemy))
        before = case.victim.total
        self.step(100, attack=1)
        self.assertTrue(case.fight.is_stunned(case.enemy))
        self.assertEqual(case.victim.total, before)
        for _ in range(120):
            self.step(attack=1)
            if case.victim.total < before:
                break
        self.assertFalse(case.fight.is_stunned(case.enemy))
        self.assertLess(case.victim.total, before)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_beam_breaking_tick_does_not_spill_into_body(self):
        case = self.arena()
        self.step(20, rein=2)
        field = case.fight.barriers[case.rein.body]
        field.current = 1
        self.step(attack=1, rein=2)
        self.assertEqual(field.current, 0)
        self.assertFalse(field.active)
        self.assertEqual(case.victim.total, 500)
        self.step(attack=1, rein=2)
        self.assertAlmostEqual(case.victim.total, 499.4)

    def test_four_actual_grenades_deplete_magazine_and_reload_restores_it(self):
        case = self.arena()
        for ammo in (75, 50, 25, 0):
            self.step(attack=2)
            self.assertEqual(self.gun.vars[53].value(), ammo)
            if ammo:
                self.step(70)
        self.step(45)
        self.assertEqual(case.victim.total, 332)
        self.assertEqual(self.gun.vars[53].value(), 0)
        self.step(120)
        self.assertEqual(self.gun.vars[53].value(), 100)

    def test_actual_ice_wall_blocks_beam_until_removed(self):
        case = self.arena()
        self.assertTrue(case.match.switch_hero(case.other, heroes()[defenses.MEI]))
        case.place(case.other, (0, 1, 7))
        self.step(15)
        angles = aim(case.fight._eye(case.other), (0, 1, 3))
        for button in (16, 0, 1, 0):
            case.run.step({case.other: (*angles, button)})
        self.assertEqual(len(case.fight.walls.pieces), 5)
        self.step(15, attack=1)
        self.assertEqual(case.victim.total, 500)
        case.run.step({case.other: (*angles, 16)})
        case.run.step({case.other: (*angles, 0)})
        self.assertFalse(case.fight.walls.pieces)
        self.step(15, attack=1)
        self.assertLess(case.victim.total, 500)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_actual_deflect_returns_secondary_to_body_or_field(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                case = self.arena()
                case.enemy.team = case.rein.team
                case.other.team = 1
                self.assertTrue(case.match.switch_hero(case.other, heroes()[deflection.GENJI]))
                case.place(case.enemy, (0, 1, 0))
                case.place(case.other, (0, 1, 6))
                case.place(case.rein, (2, 1, 2))
                case.advance(12)
                shot = aim(case.fight._eye(case.enemy), (0, 2.4, 6))
                reflect = aim(case.fight._eye(case.other), (2, 2.2, 2))
                yaw, _ = aim(case.fight._eye(case.rein), (0, 2.5, 6))
                buttons = 2 if shield else 0
                for _ in range(20):
                    case.run.step({case.rein: (yaw, 0, buttons)})
                case.run.step({case.other: (*reflect, 16), case.rein: (yaw, 0, buttons)})
                for _ in range(10):
                    case.run.step({case.other: (*reflect, 0), case.rein: (yaw, 0, buttons)})
                case.run.step({case.enemy: (*shot, 2), case.other: (*reflect, 0),
                               case.rein: (yaw, 0, buttons)})
                redirected = False
                for _ in range(60):
                    case.run.step({case.other: (*reflect, 0), case.rein: (yaw, 0, buttons)})
                    redirected |= any(r.definition == zarya_weapons.SECONDARY and r.reflected
                                      and r.shooter is case.other for r in case.fight.rockets)
                self.assertTrue(redirected)
                self.assertEqual(case.enemy.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(case.victim.total, 500 if shield else 458)
                if shield:
                    self.assertEqual(case.fight.barriers[case.rein.body].current, 1553)
                else:
                    self.assertGreaterEqual(case.other.stats.value(stats.HERO_DAMAGE), 42)

    def test_grasp_absorbs_charged_secondary_with_no_splash(self):
        case = self.arena()
        self.step(attack=8)
        self.step(rein=16)
        self.step(53)
        self.assertAlmostEqual(case.enemy.body_script.component.entity_var(87).value(), 20, places=4)
        self.assertTrue(case.match.switch_hero(case.other, heroes()[sigma_grasp.SIGMA]))
        case.place(case.other, (0, 1, 3))
        self.step(12)
        case.run.step({case.other: (0, 0, 8)})
        self.step(10)
        self.step(attack=2)
        self.step(65)
        grasp = next(i for i in case.other.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRAPH)
        self.assertAlmostEqual(grasp.vars[21312].value(), 56.6, places=4)
        self.assertEqual(case.victim.total, 500)
        target = case.fight._target_entity(case.other.body)
        self.assertEqual(case.fight.vitals_of(target).total, 400)
        self.step(70)
        self.assertAlmostEqual(case.fight.vitals_of(target).grasp_shields, 33.96, places=4)

    def test_actual_beam_hits_active_deflect_and_grasp(self):
        for hero, graph, index, button in (
            (deflection.GENJI, 0x03A3, 10, 16),
            (sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1, 8),
        ):
            with self.subTest(hero=hero):
                case = self.arena()
                self.assertTrue(case.match.switch_hero(case.other, heroes()[hero]))
                case.place(case.other, (0, 1, 3))
                self.step(12)
                case.run.step({case.other: (0, 0, button)})
                self.step(10)
                self.assertIsNotNone(defenses.active_state(case.other, hero, graph, index))
                vitals = case.fight.vitals_of(case.fight._target_entity(case.other.body))
                before = vitals.total
                self.step(10, attack=1)
                self.assertAlmostEqual(vitals.total, before - 12)
                self.assertEqual(case.victim.total, 500)
                self.assertFalse(case.fight.rockets)
