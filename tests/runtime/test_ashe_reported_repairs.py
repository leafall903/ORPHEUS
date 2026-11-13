"""Focused server contracts for reported deployment/fire omissions; client acceptance pending."""

import json
import unittest
from pathlib import Path

import test_ashe_combat as fixtures

from ow174.game.script import expr, graph

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-deployment-burn-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


@unittest.skipUnless(graph.GRAPHS_PATH.is_file(), 'requires external 104319 graphs')
class ReportedRepairsTests(unittest.TestCase):
    def test_original_factory_position_and_charge_direction_survive_owner_turn(self):
        run, owner, _ = fixtures.AsheCombatTests().fight(distance=100)
        inputs, expected = CONTRACT['input'], CONTRACT['expected']
        owner.mover.position[:] = inputs['owner_position']
        owner.body_script.set_ult_charge(2240)
        run.step({owner: (inputs['cast_yaw'], 0, 32)})
        actor, = run.match.combat.companions.active.values()
        self.assertEqual(tuple(actor.bot.position), tuple(expected['factory_position']))
        self.assertEqual(actor.charge_yaw, expected['charge_yaw'])
        factory, = (item for item in owner.body_script.component.instances.values()
                    if item.graph.index == 0x1536)
        self.assertEqual(factory.source_entity(), owner.body)
        self.assertEqual(actor.charge_instigator, owner.body)
        self.assertEqual(actor.gun.source_entity(), actor.bot.entity)
        start = tuple(actor.bot.position)
        for _ in range(10):
            run.step({owner: (inputs['post_cast_yaw'], 0, 0)})
        self.assertGreater(actor.bot.position[0], start[0] + 2)
        self.assertAlmostEqual(actor.bot.position[2], start[2], places=4)
        self.assertEqual(actor.charge_yaw, expected['charge_yaw'])

    def test_burn_mounts_original_graph_scope_and_retires_with_target(self):
        run, owner, target = fixtures.AsheCombatTests().fight(distance=15.5)
        fixtures.AsheCombatTests().press(run, owner, 16)
        engine = run.match.combat.dynamite
        grenade, = engine.grenades
        while run.frame < grenade.fuse:
            run.step({})
        burn = engine.burns[owner.body, target.body]
        component = target.body_script.component
        instance = component.instances[burn.instance]
        expected = CONTRACT['expected']
        self.assertEqual(instance.graph.index, expected['burn_graph'])
        self.assertEqual(instance.source_entity(), owner.body)
        self.assertEqual(instance.find_var(expr.INSTANCE, 0x1B).value(), expr.Entity(owner.body))
        self.assertEqual(instance.find_var(expr.INSTANCE, 476).value(), expected['burn_damage_rate'])
        self.assertEqual(instance.find_var(expr.INSTANCE, 215).value(), CONTRACT['input']['burn_seconds'])
        self.assertTrue(any(state.active and state.node.pos == expected['burn_scope_node']
                            for state in instance.states.values()))
        snapshot = target.body_script.snapshot()
        self.assertEqual(snapshot.instances[instance.id][0], expected['burn_graph'])
        self.assertEqual(snapshot.vars[instance.id, 0x1B][0], expr.Entity(owner.body))
        self.assertIsNone(component.fault)
        self.assertEqual(run.match.combat.vitals[target.body].total, 150)
        engine.retire(target.body)
        self.assertNotIn(instance.id, component.instances)
        self.assertFalse(engine.burns)

    def test_normal_throw_recovery_then_viper_shot_detonates_unmodified_flight(self):
        run, owner, _ = fixtures.AsheCombatTests().fight(distance=100)
        fixtures.AsheCombatTests().press(run, owner, 16)
        engine = run.match.combat.dynamite
        grenade, = engine.grenades
        for _ in range(25):
            run.step({})
        yaw, pitch = fixtures.aim(run.match.combat._eye(owner), grenade.position)
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(run.frame, grenade.fuse)
        self.assertNotIn(grenade, engine.grenades)
