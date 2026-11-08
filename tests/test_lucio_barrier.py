"""Executed Sound Barrier graph, health absorption and lifetime (reconstructed host)."""

import struct
import unittest
from dataclasses import replace

import test_lucio_combat as fixtures
import test_lucio_pve as pve

from ow174.game import combat, lucio
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(graph.graph(0x073B), "requires actual073B status supplement")
class SoundBarrierTests(unittest.TestCase):
    def scenario(self, charged=True):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        source.body_script.ult_seconds = 0  # isolate explicit charge from placeholder passive rate
        component = source.body_script.component
        self.assertEqual(component.entity_var(ULT_COST).value(), 2940)
        if charged:
            source.body_script.set_ult_charge(2940)
        return helper, run, source, enemy, ally

    def cast(self, run, source):
        run.step({source: (0, 0, 32)})  # authored ultimate logical button37
        for _ in range(41):
            run.step({})

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))

    def test_charge_gate_cast_delay_and_team_shields(self):
        helper, run, source, enemy, ally = self.scenario(charged=False)
        self.cast(run, source)
        self.assertEqual(helper.health(run, source), 200)
        self.assertEqual(helper.health(run, ally), 200)
        self.assertFalse(run.match.combat.lucio_barriers.leases)
        source.body_script.set_ult_charge(2940)
        run.step({source: (0, 0, 32)})
        self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(40):
            run.step({})
            self.assertEqual(helper.health(run, ally), 200)
        run.step({})
        self.assertEqual(helper.health(run, source), 950)
        self.assertEqual(helper.health(run, ally), 950)
        self.assertEqual(helper.health(run, enemy), 200)

    def test_temporary_shields_absorb_before_base_pools_and_cannot_be_healed(self):
        _, run, source, enemy, ally = self.scenario()
        fight = run.match.combat
        vitals = combat.Vitals(ally.body, 200, armour=50, shields=100)
        fight.vitals[ally.body] = vitals
        self.cast(run, source)
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 300, False, None, run.frame)
        self.assertEqual(vitals.total, 800)
        self.assertEqual(vitals.now, {combat.HEALTH_POOL: 200, combat.ARMOUR_POOL: 50,
                                      combat.SHIELDS_POOL: 100})
        self.assertEqual(vitals.heal(100), 0)
        fight._damage(enemy, target, 500, False, None, run.frame)
        self.assertEqual(vitals.total, 300)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 50)
        self.assertEqual(vitals.heal(100), 50)
        self.assertFalse(vitals.temporary_shields)

    def test_expiry_clears_wire_pool_without_damaging_or_healing_base_health(self):
        helper, run, source, _, ally = self.scenario()
        self.cast(run, source)
        vitals = self.vitals(run, ally)
        active = vitals.component(vitals.ids)[51][combat.SHIELDS_POOL * 16 + 1]
        pool_id, maximum, current, kind, _, ordinary, _, _ = struct.unpack("<IffI4B", active)
        self.assertGreater(pool_id, 0)
        self.assertEqual((maximum, current, kind, ordinary), (750, 750, 2, 0))
        for _ in range(375):
            run.step({})
        self.assertEqual(helper.health(run, ally), 200)
        self.assertFalse(vitals.temporary_shields)
        cleared = vitals.component(vitals.ids)[51][combat.SHIELDS_POOL * 16 + 1]
        self.assertEqual(struct.unpack("<I", cleared[:4])[0], 0)
        self.assertFalse(run.match.combat.lucio_barriers.leases)

    def test_input_replay_and_repress_without_charge_do_not_refresh_shields(self):
        _, run, source, _, ally = self.scenario()
        self.cast(run, source)
        for _ in range(5):
            source.take_commands([Command(run.frame, buttons=32)])
        for offset in range(10):
            run.step({source: (0, 0, 32 if offset % 2 == 0 else 0)})
        self.assertEqual(self.vitals(run, ally).temporary_shields[lucio.BARRIER_KEY].current, 730)

    def test_ffa_range_and_real_wall_exclude_other_receivers(self):
        for policy in ("ffa", "range", "wall"):
            with self.subTest(policy=policy):
                helper, run, source, _, ally = self.scenario()
                if policy == "ffa":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                elif policy == "range":
                    ally.mover.position[:] = (0, 1, 32)
                else:
                    run.match.collision_world = World([
                        (-50, 1, -50, -50, 1, 50, 50, 1, 50),
                        (-50, 1, -50, 50, 1, 50, 50, 1, -50),
                        (-5, 1, 2, 5, 1, 2, 5, 6, 2),
                        (-5, 1, 2, 5, 6, 2, -5, 6, 2),
                    ])
                self.cast(run, source)
                self.assertEqual(helper.health(run, ally), 200)
                self.assertEqual(helper.health(run, source), 950)

    def test_airborne_slam_waits_for_physics_landing(self):
        helper, run, source, _, ally = self.scenario()
        source.mover.position[:] = (0, 100, 0)
        run.step({source: (0, 0, 32)})
        for _ in range(80):
            run.step({})
        self.assertGreater(source.mover.air_ticks, 0)
        self.assertEqual(helper.health(run, ally), 200)
        for _ in range(300):
            if source.mover.air_ticks == 0:
                break
            run.step({})
        self.assertEqual(source.mover.air_ticks, 0)
        self.assertEqual(helper.health(run, ally), 950)

    def test_death_before_slam_prevents_grant_but_death_after_slam_keeps_allied_shields(self):
        for before in (True, False):
            with self.subTest(before_slam=before):
                helper, run, source, enemy, ally = self.scenario()
                if before:
                    run.step({source: (0, 0, 32)})
                else:
                    self.cast(run, source)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is source)
                fight._damage(enemy, target, 2000, False, None, run.frame)
                for _ in range(60 if before else 1):
                    run.step({})
                self.assertTrue(fight.is_dead(source))
                self.assertEqual(helper.health(run, ally), 200 if before else 948)

    def test_two_casts_refresh_one_pool_instead_of_stacking(self):
        helper, run, source, second, ally = self.scenario()
        run.match.switch_hero(second, heroes()[fixtures.LUCIO])
        second.team = 0
        second.mover.position[:] = (2, 1, 0)
        second.body_script.ult_seconds = 0
        second.body_script.set_ult_charge(2940)
        run.step({source: (0, 0, 32), second: (0, 0, 32)})
        for _ in range(41):
            run.step({})
        self.assertEqual(helper.health(run, ally), 950)
        self.assertEqual(len(self.vitals(run, ally).temporary_shields), 1)

    def test_caster_body_retirement_preserves_ally_buff_and_receiver_retirement_clears_it(self):
        helper, run, source, _, ally = self.scenario()
        self.cast(run, source)
        run.match.switch_hero(source, heroes()[SOLDIER])
        run.step({})
        self.assertEqual(helper.health(run, ally), 948)
        old_body = ally.body
        run.match.switch_hero(ally, heroes()[fixtures.LUCIO])
        run.step({})
        self.assertEqual(helper.health(run, ally), 200)
        self.assertNotIn(old_body, run.match.combat.lucio_barriers.leases)

    @unittest.skipUnless(pve.CATALOG and pve.BUNDLE, "requires real five-type NPC resources")
    def test_real_npc_allies_receive_shields_and_enemies_do_not(self):
        fixture = pve.LucioPveTests()
        fixture.setUpClass()
        try:
            for definition in fixture.definitions.values():
                for team in (0, 1):
                    with self.subTest(npc=definition.name, team=team):
                        helper, run, source, bot = fixture.scenario(definition, team=team)
                        source.body_script.ult_seconds = 0
                        source.body_script.set_ult_charge(2940)
                        before = helper.health(run, bot)
                        self.cast(run, source)
                        self.assertEqual(helper.health(run, bot) - before, 750 if team == 0 else 0)
        finally:
            fixture.tearDownClass()
