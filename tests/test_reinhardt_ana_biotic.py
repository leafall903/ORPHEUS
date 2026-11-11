"""Actual Ana grenade and Soldier field against Reinhardt's healing pools."""

import unittest

import test_game_reinhardt as fixtures
import test_reinhardt_ana_sleep as sleep_fixture
from test_game_combat import aim

from ow174.game import ana_biotic, combat, projectiles, sigma_grasp, stats
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class ReinhardtAnaBioticTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    arena = sleep_fixture.ReinhardtAnaSleepTests.arena
    health = fixtures.HammerTests.health
    reflected_ana_projectile = sleep_fixture.ReinhardtAnaSleepTests.reflected_ana_projectile

    def advance(self, count=1, *, ana=0, rein=0, soldier=0, miss=False):
        for _ in range(count):
            direction = (16384, 0) if miss else aim(self.fight._eye(self.enemy), (0, 2, 0))
            self.run.step({self.enemy: (*direction, ana), self.rein: (0, 0, rein),
                           self.other: (0, 0, soldier)})

    def setup_teams(self, *, ally=False):
        self.arena()
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.rein.team = self.other.team = 0
        self.enemy.team = 0 if ally else 1
        self.place(self.other, (1.5, 1, 0))
        self.victim = self.fight.vitals_of(self.fight._target_entity(self.rein.body))

    def grenade(self, *, rein=0, miss=False):
        self.advance(ana=16, rein=rein, miss=miss)
        for _ in range(65):
            self.advance(rein=rein, miss=miss)
            if self.victim.healing_block or self.victim.healing_boost:
                return

    def start_field(self):
        self.advance(soldier=16)
        for _ in range(85):
            self.advance()
            if any(shot.field_tick is not None for shot in self.fight.projectiles.shots):
                return
        self.fail('actual Soldier canister did not open a field')

    def test_enemy_grenade_blocks_actual_soldier_field_until_expiry(self):
        self.setup_teams()
        self.grenade()
        self.assertEqual(self.victim.total, 455)
        _, deadline = self.victim.healing_block[-1]
        self.assertAlmostEqual(deadline - self.fight.now, ana_biotic.DURATION)
        self.start_field()
        deadline_tick = round(deadline / combat.TICK)
        while self.run.frame < deadline_tick - 4:
            self.advance()
        self.assertEqual(self.victim.total, 455)
        self.run.frame = deadline_tick + 3
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertGreaterEqual(self.fight.now, deadline)
        self.assertAlmostEqual(self.victim.total, 455 + 4 * projectiles.HEAL_PER_SECOND * combat.TICK)

    def test_allied_grenade_heals_then_amplifies_actual_field(self):
        self.setup_teams(ally=True)
        self.victim.damage(350)
        before = self.victim.total
        self.grenade()
        self.assertEqual(self.victim.total, before + 100)
        _, deadline = self.victim.healing_boost[-1]
        self.assertAlmostEqual(deadline - self.fight.now, ana_biotic.DURATION)
        self.assertFalse(self.victim.healing_block)
        self.start_field()
        before = self.victim.total
        self.advance(6)
        base = projectiles.HEAL_PER_SECOND * combat.TICK
        self.assertAlmostEqual(self.victim.total - before, 6 * base * 1.5)
        deadline_tick = round(deadline / combat.TICK)
        while self.run.frame < deadline_tick - 4:
            self.advance()
        before = self.victim.total
        self.run.frame = deadline_tick + 3
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertAlmostEqual(self.victim.total - before, (3 * 1.5 + 4) * base)
        before = self.victim.total
        self.advance(6)
        self.assertAlmostEqual(self.victim.total - before, 6 * base)

    def test_two_allied_grenades_refresh_one_amplification_window(self):
        self.setup_teams(ally=True)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sleep_fixture.ANA]))
        self.place(self.other, (0, 1, -6))
        self.advance(12)
        self.victim.damage(400)
        before = self.victim.total
        self.grenade()
        self.assertEqual(self.victim.total, before + 100)
        before = self.victim.total
        first_start, first_end = self.victim.healing_boost[0]
        self.advance(soldier=16)
        for _ in range(65):
            self.advance()
            if self.victim.total > before:
                break
        self.assertEqual(self.victim.total, before + 150)
        [(start, end)] = self.victim.healing_boost
        self.assertEqual(start, first_start)
        self.assertGreater(end, first_end)
        self.assertAlmostEqual(end - self.fight.now, ana_biotic.DURATION)

    def test_enemy_grenade_denies_field_healing_during_allied_amplification(self):
        self.setup_teams(ally=True)
        self.victim.damage(350)
        before = self.victim.total
        self.grenade()
        self.assertEqual(self.victim.total, before + 100)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sleep_fixture.ANA]))
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        self.other.team = 1
        self.place(self.other, (0, 1, -6))
        self.place(self.enemy, (1.5, 1, 0))
        self.advance(12)
        self.advance(soldier=16)
        for _ in range(65):
            self.advance()
            if self.victim.healing_block:
                break
        self.assertTrue(self.victim.healing_block)
        self.assertTrue(self.victim.healing_boost)
        before = self.victim.total
        self.advance(ana=16)
        for _ in range(85):
            self.advance()
            if any(shot.field_tick is not None for shot in self.fight.projectiles.shots):
                break
        self.assertTrue(any(shot.field_tick is not None for shot in self.fight.projectiles.shots))
        self.advance(12)
        self.assertTrue(self.victim.healing_boost)
        self.assertEqual(self.victim.total, before)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_front_barrier_intercepts_grenade_with_no_body_spillover(self):
        for broken in (False, True):
            with self.subTest(broken=broken):
                self.setup_teams()
                self.advance(20, rein=2)
                barrier = self.fight.barriers[self.rein.body]
                self.assertTrue(barrier.active)
                if broken:
                    barrier.current = 50
                self.grenade(rein=2)
                self.assertEqual(barrier.current, 0 if broken else 1550)
                self.assertEqual(barrier.active, not broken)
                self.assertEqual(self.victim.total, 500)
                self.assertFalse(self.victim.healing_block)

    def test_wall_impact_can_heal_ana_without_affecting_reinhardt_behind_it(self):
        self.setup_teams()
        ana = self.fight.vitals_of(self.fight._target_entity(self.enemy.body))
        ana.damage(130)
        fixtures.charge_wall(self, 3)
        self.grenade()
        self.assertEqual(ana.total, 170)
        self.assertTrue(ana.healing_boost)
        self.assertEqual(self.victim.total, 500)
        self.assertFalse(self.victim.healing_block)

    def test_miss_and_static_wall_do_not_apply_enemy_status(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                self.setup_teams()
                if wall:
                    fixtures.charge_wall(self, 2)
                self.grenade(miss=not wall)
                self.assertEqual(self.victim.total, 500)
                self.assertFalse(self.victim.healing_block)

    def test_death_and_replacement_clear_body_local_grenade_status(self):
        self.setup_teams()
        self.victim.now.update({combat.HEALTH_POOL: 50, combat.ARMOUR_POOL: 0})
        self.grenade()
        self.assertTrue(self.victim.dead)
        self.assertFalse(self.victim.healing_block)
        self.setup_teams(ally=True)
        self.grenade()
        self.assertGreater(self.victim.healing_boost[-1][1], self.fight.now)
        old = self.rein.body
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        self.assertNotEqual(self.rein.body, old)
        self.assertNotIn(old, self.fight.vitals)
        new = self.fight.vitals_of(self.fight._target_entity(self.rein.body))
        self.assertFalse(new.healing_boost)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_reflected_grenade_uses_genji_team_for_enemy_ally_and_barrier(self):
        for mode in ('enemy', 'ally', 'barrier'):
            with self.subTest(mode=mode):
                step = self.reflected_ana_projectile(
                    attack=16, shield=mode == 'barrier', allied_to_genji=mode == 'ally')
                victim = self.fight.vitals_of(self.fight._target_entity(self.rein.body))
                before = victim.total
                for _ in range(35):
                    step()
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE), 0)
                if mode == 'ally':
                    self.assertEqual(victim.total, before + 100)
                    self.assertTrue(victim.healing_boost)
                    self.assertFalse(victim.healing_block)
                    self.assertEqual(self.other.stats.value(stats.HEALING), 100)
                elif mode == 'enemy':
                    self.assertEqual(victim.total, 455)
                    self.assertTrue(victim.healing_block)
                    self.assertFalse(victim.healing_boost)
                    self.assertGreaterEqual(self.other.stats.value(stats.HERO_DAMAGE), 45)
                else:
                    self.assertEqual(victim.total, 500)
                    self.assertFalse(victim.healing_block)
                    self.assertEqual(self.fight.barriers[self.rein.body].current, 1550)

    def test_actual_grasp_consumes_grenade_and_converts_blast_payload(self):
        self.setup_teams()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 3))
        self.advance(12)
        self.advance(soldier=8)
        self.advance(10)
        self.advance(ana=16)
        self.advance(70)
        instance = next(i for i in self.other.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRAPH)
        self.assertEqual(instance.vars[21312].value(), 50)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.victim.total, 500)
        self.assertFalse(self.victim.healing_block)
        self.assertEqual(self.health(self.other), 400)
        self.advance(80)
        self.assertAlmostEqual(self.health(self.other), 430, places=4)

    def test_allied_grasp_does_not_consume_a_friendly_grenade(self):
        self.setup_teams(ally=True)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
        self.place(self.other, (0, 1, 3))
        self.advance(12)
        self.victim.damage(140)
        before = self.victim.total
        self.advance(soldier=8)
        self.advance(10)
        self.grenade()
        instance = next(i for i in self.other.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRAPH)
        self.assertTrue(instance.states[1].active)
        self.assertFalse(instance.find_var(0, 21312).value())
        self.assertEqual(self.victim.total, before + 100)
        self.assertTrue(self.victim.healing_boost)

    def test_released_grenade_survives_actual_rifle_kill_of_ana(self):
        self.setup_teams()
        ana = self.fight.vitals_of(self.fight._target_entity(self.enemy.body))
        ana.damage(190)
        self.advance(ana=16)
        for _ in range(60):
            self.advance()
            if any(rocket.definition == ana_biotic.PROJECTILE for rocket in self.fight.rockets):
                break
        self.assertTrue(self.fight.rockets)
        angles = aim(self.fight._eye(self.other), (0, 2, 6))
        self.run.step({self.other: (*angles, 1)})
        self.assertTrue(ana.dead)
        self.assertTrue(self.fight.rockets)
        self.assertEqual(self.victim.total, 500)
        self.advance(30)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.victim.total, 455)
        self.assertTrue(self.victim.healing_block)

    def test_actual_earthshatter_interrupts_grenade_action_after_release(self):
        self.setup_teams()
        rein = self.rein.body_script
        rein.set_ult_charge(rein.component.entity_var(338).value())
        self.advance(rein=32)
        self.advance(28)
        self.advance(ana=16)
        graph = next(i for i in self.enemy.body_script.component.instances.values()
                     if i.graph.index == ana_biotic.GRAPH)
        ability = next(state for state in graph.states.values() if isinstance(state, nodes.Ability))
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertTrue(any(rocket.definition == ana_biotic.PROJECTILE for rocket in self.fight.rockets))
        for _ in range(30):
            self.advance()
            if self.fight.controlled(self.enemy.body):
                break
        self.assertTrue(self.fight.controlled(self.enemy.body))
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(self.victim.total, 500)
        self.assertTrue(self.fight.rockets)
        self.advance(35)
        self.assertEqual(self.victim.total, 455)
        self.assertTrue(self.victim.healing_block)

    def test_actual_earthshatter_blocks_fresh_grenade_input_during_control(self):
        self.setup_teams()
        rein = self.rein.body_script
        rein.set_ult_charge(rein.component.entity_var(338).value())
        self.advance(rein=32)
        self.advance(45)
        self.assertTrue(self.fight.controlled(self.enemy.body))
        self.advance(ana=16)
        self.advance(40)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.victim.total, 500)
        self.assertFalse(self.victim.healing_block)
