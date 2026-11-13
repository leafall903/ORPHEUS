"""Actual Q mounts1537; only the original retirement animation publishes6601."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_bob_charge as bob_fixture

from ow174.game import npc

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-bob-original-lifecycle-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


class AsheBobOriginalLifecycleTests(unittest.TestCase):
    def test_actual_q_original_phase_retirement_stack_and_cleanup(self):
        fixture = bob_fixture.BobChargeTests()
        run, owner, enemy = fixture.setup(distance=12)
        with (patch.object(npc, 'spawn', wraps=npc.spawn) as spawned,
              patch.object(npc, 'remove_body', wraps=npc.remove_body) as removed):
            actor = fixture.begin(run, owner)
            component = actor.bot.body_script.component
            phase, = (i for i in component.instances.values() if i.graph.index == 0x1537)
            self.assertEqual(phase.source_entity(), actor.charge_instigator)
            slot = component.entity_var(6601, create=False)
            self.assertTrue(slot is None or slot.value() is None)
            fixture.stop(run, actor)
            self.assertEqual(actor.uppercut_target, enemy.body)
            self.assertEqual(run.match.combat.vitals[enemy.body].total, 9880)
            self.assertEqual(actor.bot.position[2], CONTRACT['expected']['contact_z'])
            while run.frame < actor.ready:
                run.step({})
            self.assertEqual(component.entity_var(1735).value(), 2)
            wait = phase.node_state(phase.graph.node(35))
            self.assertTrue(wait.active)
            self.assertGreaterEqual(wait.start, actor.ready * 16)
            slot = component.entity_var(6601)
            self.assertIsNone(slot.value())
            while run.frame < actor.until:
                run.step({})
            self.assertEqual(actor.phase, 'retire')
            self.assertIn(actor.bot, run.match.bots)
            self.assertTrue(phase.node_state(phase.graph.node(39)).active)
            self.assertTrue(phase.node_state(phase.graph.node(150)).active)
            self.assertIs(slot.value(), True)
            self.assertIsNone(slot.base)
            self.assertEqual([(link.state.instance.graph.index, link.state.node.pos) for link in slot.links],
                             [(0x1537, 148)])
            health = run.match.combat.vitals[enemy.body].total
            shots = actor.shots
            for _ in range(CONTRACT['expected']['retire_steps']):
                run.step({})
            self.assertNotIn(actor.bot, run.match.bots)
            self.assertNotIn(phase.id, component.instances)
            self.assertIsNone(slot.value())
            self.assertFalse(slot.links)
            self.assertEqual(run.match.combat.vitals[enemy.body].total, health)
            self.assertEqual(actor.shots, shots)
            self.assertIsNone(component.fault)
            spawned.assert_called_once()
            removed.assert_called_once()
