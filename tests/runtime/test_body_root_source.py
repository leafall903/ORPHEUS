"""Native body-root owner fallback, retaining explicit NPC instigators."""

import json
import unittest
from pathlib import Path

from ow174.game.content import find_hero
from ow174.game.script import expr, graph
from ow174.game.script.bot import BotScript
from tests.runtime import test_dva_delivery_created_body as dva
from tests.runtime.test_game_combat import KANEZAKA_DM, Run


class BodyRootSourceTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/body-root-source-v1.json"
        self.fixture = json.loads(path.read_text(encoding="utf-8"))

    def assert_roots(self, script, source, record=None):
        record = script.record if record is None else record
        count = sum(i.get("graph") is not None for i in record["graphs"])
        count += bool(record.get("manager")) + sum(i is not None for i in record.get("weapons", ()))
        roots = [script.component.instances[i] for i in range(1, count + 1)]
        self.assertTrue(roots)
        for instance in roots:
            with self.subTest(graph=f"{instance.graph.index:04X}", entity=script.component.entity):
                self.assertEqual(instance.cached_source_entity, source)
                self.assertEqual(instance.context().var(expr.INSTANCE, 0x1B), expr.Entity(source))
                self.assertEqual(instance.descriptor_source_entity, self.fixture["descriptorSource"])

    def test_two_ordinary_player_bodies_seed_actual_body_not_controller(self):
        run = Run(KANEZAKA_DM, ("Soldier", "Mei"))
        soldier, mei = run.players
        self.assertTrue(run.match.switch_hero(mei, find_hero(self.fixture["playerHeroes"][1])))
        for player in (soldier, mei):
            self.assertNotEqual(player.body, player.entity)
            self.assert_roots(player.body_script, player.body)

    def test_actual_dva_created_controlled_mech_keeps_its_own_vm_source(self):
        dva.CreatedBodyTests.setUpClass()
        _run, pilot, _, _, mech = dva.CreatedBodyTests().scenario()
        self.assertIs(pilot.controlled_mech, mech)
        self.assertNotEqual(mech.entity, pilot.body)
        self.assertNotEqual(mech.entity, pilot.entity)
        self.assert_roots(pilot.body_script, pilot.body)
        self.assert_roots(mech.body_script, mech.entity, graph.entity_body(mech.body))
        self.assertEqual(mech.owner, pilot.entity)
        self.assertEqual(mech.source_body, pilot.body)

    def test_ordinary_npc_and_explicit_npc_source_override(self):
        cfg = self.fixture["npc"]
        args = (int(cfg["body"], 16), int(cfg["hero"], 16), int(cfg["entity"], 16), cfg["frame"],
                KANEZAKA_DM.mode_guid, cfg["health"])
        ordinary = BotScript(*args)
        self.assert_roots(ordinary, ordinary.component.entity, graph.entity_body(args[0]))
        source = int(cfg["explicitSource"], 16)
        explicit = BotScript(*args, source_entity=source)
        self.assert_roots(explicit, source, graph.entity_body(args[0]))
