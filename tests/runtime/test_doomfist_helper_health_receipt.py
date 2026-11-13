"""Actual helper parameters feed one backend receipt; original completion remains open."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat, doomfist, doomfist_helper_health
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, play, play_queue


class HelperHealthReceiptTests(DoomfistFixture, unittest.TestCase):
    def stage(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                               / 'doomfist-helper-body-health-receipt-v1.json').read_text(encoding='utf-8'))
        self.b.commands.last_frame = contract['input']['recipient_frame']
        self.match.switch_hero(self.b, heroes()[SOLDIER])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        source = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0D06)
        node = source.graph.node(16)
        captured = []
        real_damage = self.fight._damage

        def stage_contact(*args, **kwargs):
            if len(args) > 4 and args[4] == doomfist.PUNCH:
                resource = play.resolve_resource(source, node)
                evaluate = source.evaluate

                def contact_target(config, *values, **options):
                    # The known legacy contact is an explicit fixture adapter.
                    # Neither source375 nor the unknown paired23530 is written.
                    if config is node.field('m_targets'):
                        return (expr.Entity(self.b.body),)
                    return evaluate(config, *values, **options)

                with patch.object(source, 'evaluate', side_effect=contact_target):
                    request = play.resolve_request(source, node, resource, context_provider=lambda:
                        play.InvocationContext(self.a.body, self.a.body, source.source_entity()))
                captured.append((request, args[5], args[2]))
                # Legacy damage/passive/force are not executed in this service fixture.
                return False
            return real_damage(*args, **kwargs)

        before, serial = self.vitals().total, self.fight.knockbacks.serial
        with patch.object(self.fight, '_damage', side_effect=stage_contact):
            for _ in range(18):
                self.run.step({self.a: (0, 0, 2)})
            self.run.step({})
            for _ in range(60):
                self.run.step({})
                if captured:
                    break
        self.assertEqual(len(captured), 1)
        self.assertEqual(self.vitals().total, before)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        self.assertFalse(self.a.client.messages(combat.HIT))
        request, source_frame, legacy_amount = captured[0]
        component = self.b.body_script.component
        ids = set(component.instances)
        queue = play_queue.Queue(component, lambda guid: play_queue.Load('ready', graph.graph(0x0D07))
                                 if guid == request.resource.guid else None)
        queue.submit(request)
        helper, = [i for key, i in component.instances.items() if key not in ids]
        component.run_now()
        state = helper.node_state(helper.graph.node(14))
        self.assertTrue(state.active)
        self.assertNotEqual(source_frame, component.frame)
        inputs = doomfist_helper_health.resolve(state, self.a, source_frame=source_frame)
        self.assertIsNotNone(inputs)
        self.assertAlmostEqual(inputs.amount, contract['expected']['amount'], places=5)
        self.assertAlmostEqual(inputs.amount, legacy_amount, places=5)
        self.assertEqual(inputs.reference_entity, component.entity)
        self.assertEqual(inputs.paired, state.evaluate(state.node.field('m_B12BFE8B'), watch=False))
        return inputs, helper, queue, before, serial

    def test_actual_contact_parameters_settle_one_body_damage_receipt_without_finishing_helper(self):
        inputs, helper, queue, before, serial = self.stage()
        component, state, source_frame = helper.component, inputs.state, inputs.source_frame
        receipt = doomfist_helper_health.consume_body_damage(inputs)
        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.phase, 'settled')
        self.assertAlmostEqual(receipt.health_before - receipt.health_after, inputs.amount, places=5)
        self.assertAlmostEqual(before - self.vitals().total, inputs.amount, places=5)
        notices = self.a.client.messages(combat.HIT)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['+0x84'], source_frame)
        self.assertEqual(self.fight.vitals_of(self.fight.player_target(self.a)).temporary_shields, 30)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        self.assertTrue(state.active)
        self.assertFalse(receipt.original_completion_qualified)
        again = doomfist_helper_health.resolve(state, self.a, source_frame=source_frame)
        self.assertIs(doomfist_helper_health.consume_body_damage(again), receipt)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertEqual(self.vitals().total, receipt.health_after)
        self.match.switch_hero(self.a, heroes()[SOLDIER])
        self.assertIsNone(doomfist_helper_health.consume_body_damage(inputs))
        self.assertTrue(state.active)  # No fake completion or authored OnEnd dispatch.
        component.destroy(helper)
        queue.retire()

    def test_captured_caller_body_and_hit_clock_survive_actual_source_death(self):
        inputs, helper, queue, before, serial = self.stage()
        body, script = self.a.body, self.a.body_script
        self.assertTrue(self.fight._damage(self.b, self.fight.player_target(self.a), 1000., False,
                                          0x0B90000000000001, self.b.body_script.frame))
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(self.a.body, body)
        self.assertIs(self.a.body_script, script)
        receipt = doomfist_helper_health.consume_body_damage(inputs)
        self.assertIsNotNone(receipt)
        self.assertTrue(receipt.accepted)
        self.assertAlmostEqual(before - self.vitals().total, inputs.amount, places=5)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertEqual(self.a.client.messages(combat.HIT)[0]['+0x84'], inputs.source_frame)
        self.assertEqual(receipt.request.source_body, body)
        self.assertFalse(receipt.original_completion_qualified)
        self.assertTrue(inputs.state.active)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        helper.component.destroy(helper)
        queue.retire()

    def test_backend_failure_after_mutation_never_replays_damage_or_finishes(self):
        inputs, helper, queue, before, serial = self.stage()
        original = self.fight._damage

        def fail_after_damage(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('fixture failure after actual backend damage')

        with (patch.object(self.fight, '_damage', side_effect=fail_after_damage),
              self.assertRaisesRegex(RuntimeError, 'after actual backend damage')):
            doomfist_helper_health.consume_body_damage(inputs)
        receipt = doomfist_helper_health.consume_body_damage(inputs)
        self.assertEqual(receipt.phase, 'failed')
        self.assertIsNone(receipt.accepted)
        self.assertAlmostEqual(before - self.vitals().total, inputs.amount, places=5)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        self.assertEqual(self.fight.vitals_of(self.fight.player_target(self.a)).temporary_shields, 0)
        self.assertTrue(inputs.state.active)
        self.assertFalse(receipt.original_completion_qualified)
        helper.component.destroy(helper)
        queue.retire()
