"""Normal body SubScripts retain the original parent instigator before init."""

import unittest

from ow174.game import combat, reaper
from ow174.game.script import expr, graph
from ow174.game.script.bot import BotScript
from tests.runtime import test_game_reaper as scenarios
from tests.runtime.test_game_combat import KANEZAKA_DM, Run


class SubScriptParentSourceTests(unittest.TestCase):
    def assert_children(self, script, expected):
        children = [i for i in script.component.instances.values() if i.parent is not None]
        self.assertTrue(children)
        for child in children:
            with self.subTest(graph=f"{child.graph.index:04X}", parent=f"{child.parent.graph.index:04X}"):
                self.assertEqual(child.cached_source_entity, expected)
                self.assertEqual(child.context().var(expr.INSTANCE, 27), expr.Entity(expected))
                self.assertEqual(child.source_entity(), child.parent.source_entity())
                self.assertIsNone(child.descriptor_source_entity)

    def test_real_reaper_nested_children_and_actual_fire_keep_body_source(self):
        fixture = scenarios.ReaperTests()
        run, owner, victim, target = fixture.scenario()
        self.assert_children(owner.body_script, owner.body)
        self.assertTrue(any(i.parent is not None and i.parent.parent is not None
                            for i in owner.body_script.component.instances.values()))
        fixture.fire(run, owner, victim)
        self.assertEqual(run.match.combat.vitals_of(target).total, 80)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 20)
        self.assert_children(owner.body_script, owner.body)

    def test_other_actual_soldier_body_parent_child_source(self):
        run = Run(KANEZAKA_DM)
        owner = run.players[0]
        self.assert_children(owner.body_script, owner.body)
        self.assertTrue(any(i.parent is not None and i.parent.parent is not None
                            for i in owner.body_script.component.instances.values()))

    def test_explicit_reaper_npc_instigator_reaches_nested_children(self):
        body, entity, instigator = 0x0400000000000022, 0xA0007101, 0xA0007102
        script = BotScript(body, reaper.HERO, entity, 1000, KANEZAKA_DM.mode_guid, 250,
                           record=graph.bodies()[body], source_entity=instigator)
        self.assertNotEqual(script.component.entity, instigator)
        self.assert_children(script, instigator)
