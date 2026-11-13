"""Actual Q keeps the landed entity's declared component and its lifecycle."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_zarya_reported_presentation as scenes

from ow174.game import graviton
from ow174.game.script import expr, graph
from ow174.game.server import GameServer
from ow174.game.server_snapshot import export_server_state


class ZaryaFieldComponentStartupTests(unittest.TestCase):
    def flight(self):
        run, caster, _, _ = scenes.ZaryaPresentationTests().scene()
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        rocket, = run.match.combat.rockets
        return run, caster, rocket

    def test_declared_empty_component_is_registered_on_actual_field_and_retired(self):
        spec = json.loads((Path(__file__).resolve().parents[2]
                           / 'fixtures/behavior/zarya-field-component-startup-v1.json').read_text())
        self.assertIsInstance(graph.entity_script_component(graviton.FIELD_ENTITY), dict)
        run, caster, rocket = self.flight()
        for _ in range(36):
            run.step({})
        field, = run.match.combat.zarya_gravitons.fields
        actor = run.match.combat.deployables[field.entity]
        self.assertIsNotNone(actor.body_script)
        script = actor.body_script
        component = script.component
        self.assertEqual(component.entity, actor.entity)
        self.assertIs(run.match.body_components[actor.entity], component)
        self.assertIs(caster.body_script.world._body_component(actor.entity), component)
        self.assertEqual(len(component.instances), spec['expected_initial_graph_count'])
        self.assertEqual(rocket.source_state.instance.component.entity_var(1752).value(),
                         expr.Entity(rocket.entity))
        self.assertNotEqual(actor.entity, rocket.entity)
        self.assertNotIn(rocket.entity, run.match.body_components)
        self.assertEqual(actor.source_body, rocket.source_body)
        server = GameServer()
        server.matches.append(run.match)
        for name in ('environment', 'skin_of', 'emote_aliases_of',
                     'emote_blend_trees_of', 'wheel_cosmetics_of'):
            setattr(server, name, getattr(run.match, name))
        for player in run.players:
            player.client = None
        stored = export_server_state(server, collision_provider=run.match.collision_provider)
        self.assertIsInstance(stored, dict)
        run.match.combat.destroy_deployable(actor.entity)
        self.assertNotIn(actor.entity, run.match.body_components)
        self.assertTrue(script.retired)
        self.assertIsNone(component.world.match)

    def test_unknown_legacy_catalog_does_not_invent_a_component(self):
        run, _, _ = self.flight()
        with patch.object(graph, 'entity_script_component', return_value=NotImplemented):
            for _ in range(36):
                run.step({})
        field, = run.match.combat.zarya_gravitons.fields
        actor = run.match.combat.deployables[field.entity]
        self.assertIsNone(actor.body_script)
        self.assertNotIn(actor.entity, run.match.body_components)
