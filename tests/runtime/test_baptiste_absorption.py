"""Actual Bap/Sigma input with explicit003 tags and resource-valued temporary pools."""

import os
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import test_baptiste_combat as fixture
from test_baptiste_matrix import deploy_matrix
from test_game_combat import aim

from ow174.game import absorption, combat, deflection, mover, npc, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


class BaptisteAbsorptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not deflection.catalog().admitted(0x04000000000029AD):
            raise RuntimeError('requires explicit verified003 projectile-tag input')

    def arena(self, rear=False):
        args = fixture.BaptisteCombatTests().setup_fight()
        self.run, self.bap, self.ally, self.sigma, self.fight, _, self.weapon = args
        self.run.match.switch_hero(self.sigma, heroes()[absorption.SIGMA])
        for actor, point in ((self.bap, (0, 1, 0)), (self.ally, (20, 1, 12)), (self.sigma, (0, 1, 8))):
            actor.mover.position[:] = point
            actor.mover.body = None  # explicit static pose fixture, not movement acceptance
            actor.body_script.ult_seconds = 0
        self.sigma_aim = (0 if rear else -32768, 0)
        self.instance = next(i for i in self.sigma.body_script.component.instances.values()
                             if i.graph.index == 0x1D02)
        target = next(t for t in self.fight._targets() if t.owner is self.sigma)
        self.vitals = self.fight.vitals_of(target)

    def step(self, bap=0, sigma=0, count=1, angles=(0, 0)):
        for _ in range(count):
            with patch.object(self.fight.projectiles, 'fired', return_value=None):
                self.run.step({self.bap: (*angles, bap), self.sigma: (*self.sigma_aim, sigma)})

    def start(self):
        self.step(sigma=8)
        self.step(count=4)
        self.assertTrue(self.instance.state(1).active)

    def extra(self):
        return sum(pool.now for pool in self.vitals.extra.values())

    def charge(self):
        return self.bap.body_script.component.vars[342].value()

    def grant(self):
        self.arena()
        self.start()
        self.step(bap=1)
        self.step(count=150)
        self.assertFalse(self.instance.state(1).active)
        self.assertAlmostEqual(self.extra(), 43.2, places=4)

    def test_actual_primary_burst_consumes_ammo_without_hit_or_charge_then_plays_shield_graph(self):
        self.arena()
        self.start()
        ammo = self.weapon.vars[53].value()
        self.step(bap=1)
        self.step(count=25)
        self.assertEqual(self.instance.vars[21312].value(), 72)
        self.assertEqual(self.vitals.total, 400)
        self.assertEqual(ammo - self.weapon.vars[53].value(), 3)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.bap.client.messages(combat.HIT), [])
        self.step(count=125)
        self.assertAlmostEqual(self.extra(), 43.2, places=4)
        child, = (i for i in self.sigma.body_script.component.instances.values() if i.graph.index == 0x1D03)
        state, = (s for s in child.states.values() if s.active
                  and s.node.cls == 'STUStatescriptStateHealthPool')
        self.assertAlmostEqual(expr.to_float(state.evaluate(state.node.field('m_amount'), watch=False)),
                               43.2, places=4)

    def test_healing_grenade_is_consumed_without_healing_splash_or_shield_conversion(self):
        self.arena()
        self.ally.mover.position[:] = (0, 1, 10)
        target = next(t for t in self.fight._targets() if t.owner is self.ally)
        ally_vitals = self.fight.vitals_of(target)
        ally_vitals.damage(180)
        self.start()
        self.step(bap=2)
        self.step(count=150)
        self.assertEqual(ally_vitals.total, 20)
        self.assertEqual(self.vitals.total, 400)
        self.assertEqual(self.instance.vars[21312].value(), 0)
        self.assertEqual(self.extra(), 0)
        self.assertEqual(self.bap.stats.values.get(stats.HEALING, 0), 0)
        self.assertEqual(self.charge(), 0)
        self.assertFalse(self.fight.rockets)

    def test_distinct_temporary_pool_is_damaged_before_base_and_cannot_be_healed(self):
        self.grant()
        before = self.extra()
        self.step(bap=1)
        self.assertAlmostEqual(self.extra(), before - 24, places=4)
        self.assertEqual(self.vitals.base_total, 400)
        self.assertEqual(self.vitals.ult_healing_deficit, 0)
        self.assertEqual(self.charge(), 24)
        self.sigma.team = self.bap.team
        angles = aim(self.fight._eye(self.bap), (0, 1.9, 8))
        self.step(bap=2, angles=angles)
        self.step(count=40)
        self.assertLess(self.extra(), before)
        self.assertEqual(self.vitals.base_total, 400)
        self.assertEqual(self.bap.stats.values.get(stats.HEALING, 0), 0)
        self.assertEqual(self.vitals.ult_healing_deficit, 0)

    def test_pool_component_keeps_base_and_extra_identifiers_separate(self):
        self.grant()
        parts = self.vitals.component(self.vitals.ids)[51]
        base = struct.unpack('<IffI4B', parts[combat.SHIELDS_POOL * combat.POOLS])
        extra = struct.unpack('<IffI4B', parts[combat.SHIELDS_POOL * combat.POOLS + 1])
        self.assertNotEqual(base[0], extra[0])
        self.assertEqual(base[1:4], (100, 100, combat.SHIELDS_POOL))
        self.assertAlmostEqual(extra[1], 43.2, places=4)
        self.assertEqual(extra[3], combat.SHIELDS_POOL)

    def test_grace_then_resource_decay_retires_only_temporary_pool(self):
        self.grant()
        for _ in range(250):
            decay = next((s for s in self.instance.states.values() if s.node.pos == 70 and s.active), None)
            if decay is not None:
                break
            self.assertAlmostEqual(self.extra(), 43.2, places=4)
            self.step()
        self.assertIsNotNone(decay)
        self.step()
        before = self.extra()
        self.step(count=125)
        self.assertAlmostEqual(before - self.extra(), 10, places=3)
        self.assertEqual(self.vitals.base_total, 400)
        self.step(count=500)
        self.assertEqual(self.extra(), 0)
        self.assertEqual(self.vitals.maximum, 400)
        self.assertFalse(any(i.graph.index == 0x1D03
                             for i in self.sigma.body_script.component.instances.values()))

    def test_rear_fire_and_melee_are_not_absorbed(self):
        for melee in (False, True):
            with self.subTest(melee=melee):
                self.arena(rear=not melee)
                if melee:
                    self.sigma.mover.position[2] = 1.5
                self.start()
                self.step(bap=0x800 if melee else 1)
                self.step(count=30 if melee else 1)
                self.assertEqual(self.vitals.total, 370 if melee else 376)
                self.assertEqual(self.instance.vars[21312].value(), 0)

    def test_partial_decay_marks_shield_pool_dirty_for_health_replication(self):
        self.grant()
        for _ in range(250):
            if any(s.node.pos == 70 and s.active for s in self.instance.states.values()):
                break
            self.step()
        else:
            self.fail('authored decay state did not become active')
        self.step()
        before = self.extra()
        self.vitals.changed.clear()
        self.fight.tick += 10
        self.fight.absorption.update()
        self.assertAlmostEqual(before - self.extra(), .8, places=4)
        self.assertIn(combat.SHIELDS_POOL, self.vitals.changed)

    def test_nearer_wall_prevents_absorption_and_health_damage(self):
        self.arena()
        self.start()
        self.run.match.collision_world = World([
            (-10, -10, 3, 10, -10, 3, 10, 10, 3),
            (-10, -10, 3, 10, 10, 3, -10, 10, 3),
        ])
        self.step(bap=1)
        self.step(count=25)
        self.assertEqual(self.vitals.total, 400)
        self.assertEqual(self.instance.vars[21312].value(), 0)

    def test_matrix_boosted_actual_primary_converts_and_caps_at_resource400(self):
        self.arena()
        matrix = deploy_matrix(self.run, self.bap)
        matrix.position = (0, 2, 4)
        matrix.rotation = (0, 0, 0, 1)
        self.run.match.switch_hero(self.ally, heroes()[fixture.BAPTISTE])
        self.ally.mover.position[:] = (0, 1, 0)
        self.ally.mover.body = None
        self.ally.body_script.ult_seconds = 0
        self.start()
        for _ in range(125):
            with patch.object(self.fight.projectiles, 'fired', return_value=None):
                self.run.step({self.bap: (0, 0, 1), self.ally: (0, 0, 1),
                               self.sigma: (*self.sigma_aim, 0)})
            if self.extra() > 0:
                break
        self.assertGreaterEqual(self.instance.vars[21312].value(), 400 / .6)
        self.assertAlmostEqual(self.extra(), 400)
        self.assertEqual(self.vitals.base_total, 400)
        self.assertEqual(self.charge(), 0)

    def test_field0145_canister_passes_absorption_and_deploys(self):
        self.arena()
        self.start()
        self.step(bap=16)
        self.step(count=80)
        ward, = self.fight.baptiste.fields.values()
        self.assertIs(ward.actor.owner, self.bap)
        self.assertGreater(ward.actor.position[2], 5)
        self.assertEqual(self.instance.vars[21312].value(), 0)

    def test_world_deadline_rejects_stale_owner_vm_without_synthesizing_command(self):
        self.arena()
        self.start()
        self.run.frame += 200
        self.run.match.update(self.run.frame * .016, self.run.frame)
        self.assertTrue(self.instance.state(1).active)
        self.bap.take_commands([Command(self.run.frame, buttons=1)])
        self.assertEqual(self.vitals.base_total, 376)
        self.assertEqual(self.instance.vars[21312].value(), 0)

    def test_same_team_grasp_does_not_consume_friendly_healing(self):
        self.arena()
        self.sigma.team = self.bap.team
        self.vitals.damage(180)
        self.start()
        angles = aim(self.fight._eye(self.bap), (0, 1.9, 8))
        self.step(bap=2, angles=angles)
        self.step(count=40)
        self.assertEqual(self.vitals.total, 290)
        self.assertEqual(self.instance.vars[21312].value(), 0)

    def test_death_during_grasp_prevents_delayed_shield_grant(self):
        self.arena()
        self.start()
        self.step(bap=1)
        target = next(t for t in self.fight._targets() if t.owner is self.sigma)
        self.fight._damage(self.bap, target, 10000, False, None, self.run.frame)
        self.step(count=150)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(self.extra(), 0)


class BaptisteAbsorptionNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BaptisteAbsorptionTests.setUpClass()
        data = os.environ.get('ORPHEUS_PVE_NPC_DATA')
        if not data:
            raise RuntimeError('requires explicit five-NPC catalog and mover inputs')
        cls.catalog = npc.load_catalog(Path(data) / 'pve_npcs_174.json')
        mover.mover_data(next(iter(cls.catalog.values())).body)
        if any(row.body not in mover.loaded_mover_table() for row in cls.catalog.values()):
            raise RuntimeError('requires all five explicit NPC mover rows')

    def test_absorbed_damage_and_healing_do_not_reach_five_actual_npc_bodies(self):
        for definition in self.catalog.values():
            for healing in (False, True):
                with self.subTest(npc=definition.name, healing=healing):
                    case = BaptisteAbsorptionTests()
                    case.arena()
                    bot = npc.spawn(case.run.match, definition, (0, 1, 11), team=0 if healing else 1)
                    target = next(t for t in case.fight._targets() if t.owner is bot)
                    vitals = case.fight.vitals_of(target)
                    if healing:
                        vitals.damage(180)
                    before = vitals.total
                    case.start()
                    case.step(bap=2 if healing else 1)
                    case.step(count=40)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(case.instance.vars[21312].value(), 0 if healing else 72)
                    self.assertEqual(case.charge(), 0)
                    self.assertEqual(case.bap.stats.values.get(stats.HEALING, 0), 0)


if __name__ == '__main__':
    unittest.main()
