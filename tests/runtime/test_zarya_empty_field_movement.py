"""Empty field queries avoid roster construction; actual fields keep their path."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bastion_combat as bastion
import test_zarya_graviton_fortify as fortify
import test_zarya_reported_presentation as presentation

from ow174.game import combat, defenses, graviton
from ow174.game.content import heroes
from ow174.game.script import graph, nodes, runtime


class ZaryaEmptyFieldMovementTests(unittest.TestCase):
    def test_linked_posture_field_creation_retains_full_query_control_flow(self):
        case = bastion.BastionCombatTests()
        case.fixture()
        case.run.match.switch_hero(case.bystander, heroes()[combat.BASTION])
        case.step(0, 40)
        component = case.bystander.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x013A)
        state = instance.states[30]
        self.assertIsInstance(state, nodes.Stack)
        self.assertTrue(state.active)
        state.link((graph.ENTITY, 1216))
        manager = case.fight.zarya_gravitons
        self.assertFalse(manager.fields)
        events = []
        real_output, real_targets = state.output, case.fight._targets
        real_var = runtime.Component.entity_var

        def output(slot):
            # Adversarial provider boundary: retain the original state's output,
            # but create a field during its real linked evaluation. This is not
            # a claim that the authored Bastion Stack normally creates fields.
            events.append('output')
            manager.fields.append(graviton.Field(
                case.enemy, case.enemy.body, case.enemy.team, case.fight._eye(case.player),
                case.fight.tick, case.fight.tick + 10, 6.0))
            return real_output(slot)

        def targets():
            events.append('targets.enter')
            result = real_targets()
            events.append('targets.exit')
            return result

        def entity_var(owner, key, create=True):
            if key == 1216:
                events.append(('posture', owner.entity, create))
            return real_var(owner, key, create=create)

        with (patch.object(state, 'output', output), patch.object(case.fight, '_targets', targets),
              patch.object(runtime.Component, 'entity_var', entity_var)):
            result = manager.movement_for(case.player)
        self.assertIsNotNone(result)
        destination, speed = result
        self.assertEqual(speed, graviton.PULL_SPEED)
        for actual, expected in zip(destination, case.player.mover.position, strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(events, ['targets.enter', ('posture', case.player.body, True),
                                  ('posture', case.bystander.body, True), 'output', 'targets.exit'])
        self.assertEqual(len(manager.fields), 1)

    def test_empty_field_preserves_real_bastion_linked_posture_read(self):
        case = bastion.BastionCombatTests()
        case.fixture()
        component = case.player.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x013A)
        state = instance.states[30]
        self.assertIsInstance(state, nodes.Stack)
        self.assertTrue(state.active)
        # An explicit runtime-link boundary, using the original active state and
        # output implementation. This is not an authored1216 writer claim.
        selected = state.link((graph.ENTITY, 1216))
        reads = []
        real_value = runtime.Var.value

        def trace(var, *args, **kwargs):
            if var is selected:
                reads.append(var.key)
            return real_value(var, *args, **kwargs)

        manager = case.fight.zarya_gravitons
        self.assertFalse(manager.fields)
        with patch.object(runtime.Var, 'value', trace):
            self.assertIsNone(manager.movement_for(case.player))
        self.assertEqual(reads, [(0, 1216)])
        self.assertTrue(state.active)

    def test_no_actual_field_does_not_construct_any_targets(self):
        spec = json.loads((Path(__file__).resolve().parents[2]
                           / 'fixtures/behavior/zarya-empty-field-movement-v1.json').read_text())
        run, caster, _, _ = presentation.ZaryaPresentationTests().scene()
        manager = run.match.combat.zarya_gravitons
        self.assertFalse(manager.fields)
        with patch.object(manager.fight, '_targets', wraps=manager.fight._targets) as targets:
            for _ in range(spec['query_count']):
                self.assertIsNone(manager.movement_for(caster))
        self.assertEqual(targets.call_count, spec['empty_field_target_queries'])

    def test_actual_q_keeps_normal_and_fortified_field_movement(self):
        for protected in (False, True):
            with self.subTest(fortified=protected):
                scene = fortify.ZaryaGravitonFortifyTests()
                scene.scene(defenses.ORISA, activate_defense=protected)
                field = scene.field()
                manager = scene.fight.zarya_gravitons
                target = next(t for t in scene.fight._targets() if t.owner is scene.b)
                with patch.object(scene.fight, '_targets', wraps=scene.fight._targets) as targets:
                    actual = manager.movement_for(scene.b)
                self.assertGreaterEqual(targets.call_count, 1)
                if protected:
                    self.assertIsNone(actual)
                else:
                    self.assertEqual(actual, ((field.position[0], field.position[1] - target.eye,
                                               field.position[2]), graviton.PULL_SPEED))
                self.assertIsNone(manager.movement_for(scene.a))
