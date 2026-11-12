"""Actual empty000E controller VM hosting, separate from player body state."""

import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures
from test_game_graph_analysis import make_graph, node, output

from ow174.game import heroselect
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph, runtime
from ow174.game.script.controller_filter import ControllerFilterInputs
from ow174.game.script.player_controller import DEFINITION, ControllerVM


@unittest.skipUnless(graph.entity_body(DEFINITION) is not None, "requires actual000E controller metadata")
class PlayerControllerTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight

    def test_actual_controller_modes_reserve_their_wire_ids_for_vm_allocation(self):
        for practice, expected in ((False, (1, 2, 3, 4)), (True, (1, 2, 3, 4, 5))):
            with self.subTest(practice=practice):
                run, player, _enemy = self.fight(team_game=practice)
                host = player.controller_vm
                self.assertEqual(host.reserved_wire_ids, expected)
                for opened in (False, True):
                    instances, _ = heroselect.controller_instances(run.match.controller, opened,
                                                                   hero=player.hero.guid, body=player.body)
                    self.assertEqual(tuple(sorted(item.index for item in instances)), expected)
                first = host.component.create(make_graph(1))
                self.assertEqual(first.id, expected[-1] + 1)
                host.component.destroy(first)
                second = host.component.create(make_graph(2))
                self.assertGreater(second.id, first.id)
                self.assertNotIn(second.id, expected)
                run.match.switch_hero(player, heroes()[SOLDIER])
                run.step({})
                self.assertIs(player.controller_vm, host)
                self.assertEqual(host.reserved_wire_ids, expected)

    def test_explicit_wire_collision_or_allocator_rewind_rejects_before_advancing(self):
        for collision in (True, False):
            with self.subTest(collision=collision):
                run, player, _enemy = self.fight()
                host = player.controller_vm
                if collision:
                    host.component.create(make_graph(1), instance_id=host.reserved_wire_ids[0], init=False)
                else:
                    host.component.next_id = host.reserved_wire_ids[0]
                before = host.component.frame
                with self.assertRaisesRegex(ValueError, "reserved controller wire namespace"):
                    host.advance(run.frame + 1, player.hero.guid)
                self.assertEqual(host.component.frame, before)

    def test_explicit_filter_binding_rejects_other_namespace_and_retires_on_replacement_or_leave(self):
        run, player, _enemy = self.fight()
        host = player.controller_vm
        self.assertIsNone(host.filter_inputs)
        masks = ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64))
        profile = ControllerFilterInputs(player.entity, masks, 0, 0x40001, provenance="explicit manual input")
        host.bind_filter_inputs(profile)
        self.assertIs(profile.bound_host, host)
        with self.assertRaisesRegex(ValueError, "contradict"):
            profile.update(component_masks=(masks[0] & ~(1 << 47), masks[1]))
        self.assertEqual(profile.component_masks, masks)
        contradictory = ControllerFilterInputs(
            player.entity, (masks[0] & ~(1 << 47), masks[1]), 0, 0x40001,
            provenance="explicit contradictory component47 absence")
        with self.assertRaisesRegex(ValueError, "component47"):
            host.bind_filter_inputs(contradictory)
        self.assertIs(host.filter_inputs, profile)
        self.assertFalse(profile.retired)
        self.assertIsNone(contradictory.bound_host)
        other = ControllerVM(player.entity, player.hero.guid, run.match.game_map.mode_guid, run.frame)
        with self.assertRaisesRegex(ValueError, "this live owner"):
            other.bind_filter_inputs(profile)
        self.assertIsNone(other.filter_inputs)
        self.assertFalse(profile.retired)
        replacement = ControllerFilterInputs(player.entity, masks, 0, 0x40021,
                                              provenance="explicit replacement")
        host.bind_filter_inputs(replacement)
        self.assertTrue(profile.retired)
        self.assertIsNone(profile.bound_host)
        self.assertIs(replacement.bound_host, host)
        run.match.leave(player)
        self.assertTrue(replacement.retired)
        self.assertIsNone(replacement.bound_host)
        with self.assertRaises(ValueError):
            host.bind_filter_inputs(None)

    def test_actual_empty_controller_is_independent_and_survives_body_death_and_switch(self):
        run, player, enemy = self.fight()
        host = player.controller_vm
        self.assertIsNotNone(host)
        profile = ControllerFilterInputs(player.entity, ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64)),
                                         0, 0x40001, provenance="declared lifetime test input")
        host.bind_filter_inputs(profile)
        self.assertEqual(host.definition, DEFINITION)
        self.assertEqual(host.component.entity, player.entity)
        self.assertTrue(host.component.strict)
        self.assertEqual(host.component.instances, {})
        self.assertIsNot(host.component, player.body_script.component)
        host.component.set(14695, True)
        self.assertIsNone(player.body_script.component.entity_var(14695, create=False))
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        for _ in range(3):
            run.step({})
        self.assertTrue(run.match.combat.is_dead(player))
        self.assertIs(player.controller_vm, host)
        self.assertFalse(host.retired)
        self.assertIs(host.filter_inputs, profile)
        self.assertFalse(profile.retired)
        run.match.switch_hero(player, heroes()[SOLDIER])
        run.step({})
        self.assertIs(player.controller_vm, host)
        self.assertTrue(host.component.entity_var(14695).value())
        self.assertEqual(host.component.world.hero, SOLDIER)
        self.assertIs(host.filter_inputs, profile)
        self.assertIs(profile.bound_host, host)

    def test_same_or_old_frame_does_not_advance_and_storage_is_snapshot_visible(self):
        _run, player, _enemy = self.fight()
        host = player.controller_vm
        frame, now = host.component.frame, host.component.now
        self.assertFalse(host.advance(frame, player.hero.guid))
        self.assertFalse(host.advance(frame - 1, player.hero.guid))
        self.assertEqual(host.component.now, now)
        before = export_component_vm(host.component)
        host.component.set(14695, True)
        self.assertNotEqual(export_component_vm(host.component), before)

    def test_removal_retires_the_controller_namespace_and_cannot_restart_it(self):
        run, player, _enemy = self.fight()
        host = player.controller_vm
        host.component.create(make_graph(1))
        run.match.leave(player)
        self.assertTrue(host.retired)
        self.assertTrue(host.component.owner_dead)
        self.assertEqual(host.component.instances, {})
        host.retire()
        self.assertFalse(host.advance(run.frame + 1, player.hero.guid))

    def test_unknown_engine_query_poison_retires_only_controller_host(self):
        run, player, _enemy = self.fight()
        host = player.controller_vm
        profile = ControllerFilterInputs(player.entity, ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64)),
                                         0, 0x40001, provenance="declared fault lifetime test")
        host.bind_filter_inputs(profile)
        host.component.create(make_graph(1, [
            node(0, "STUStatescriptEntry", fields={"m_outPlug": output([1, "m_beginPlug"])}),
            node(1, "STUStatescriptStateBooleanSwitch", state=0,
                 fields={"m_condition": {"$": "unknown_controller_engine_query"}}),
        ], states=[1], entries=[0]))
        with self.assertLogs("ow174.game", level="ERROR"):
            run.step({})
        self.assertTrue(host.retired)
        self.assertTrue(profile.retired)
        self.assertIsNone(profile.bound_host)
        self.assertIsInstance(host.component.fault, runtime.UnsupportedScriptError)
        self.assertIsNotNone(player.body_script)
        self.assertFalse(player.gone)

    def test_nonempty_or_unknown_definition_and_invalid_identity_are_rejected(self):
        record = graph.entity_body(DEFINITION)
        for change in ({"graphs": [{"graph": 1}]}, {"script_component": {}}, {"weapons": [{}]}):
            with (
                self.subTest(change=change), patch.object(graph, "entity_body", return_value=record | change),
                self.assertRaises(ValueError),
            ):
                ControllerVM(100, SOLDIER, 1, 0)
        for entity in (0, -1, True, 0x1FFFFFFF, 1 << 32):
            with self.subTest(entity=entity), self.assertRaises(ValueError):
                ControllerVM(entity, SOLDIER, 1, 0)

    def test_rejected_controller_definition_does_not_publish_a_partial_player(self):
        run, player, _enemy = self.fight()
        before = list(run.match.players)
        original = graph.entity_body
        invalid = original(DEFINITION) | {"graphs": [{"graph": 1}]}

        def definition(identifier):
            return invalid if identifier == DEFINITION else original(identifier)

        with patch.object(graph, "entity_body", side_effect=definition), self.assertRaises(ValueError):
            run.match.add_player(999, "Rejected", player.hero.guid, 0, False)
        self.assertEqual(run.match.players, before)


if __name__ == "__main__":
    unittest.main()
