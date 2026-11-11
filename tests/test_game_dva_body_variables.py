"""Actual026A foreign member/stack consumers; control transfer remains open."""

import unittest
from unittest.mock import patch

import test_body_vm_aliases as alias_tests
import test_game_dva_created_body as created_tests
import test_server_snapshot as storage_tests
from test_script_expr import expression
from test_script_ruleset_values import ruleset

from ow174.game import body_entities, npc, observers, replay_values
from ow174.game.body_vm_snapshot import export_body_vm, export_component_vm, snapshot_digest
from ow174.game.content import find_hero
from ow174.game.script import expr, runtime
from ow174.game.script.driver import BodyScript


class BodyVariableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created_tests.CreatedBodyTests.setUpClass()

    def scenario(self):
        return created_tests.CreatedBodyTests().scenario()

    def stack(self, instance):
        state = instance.node_state(instance.graph.node(208))
        state.begin()
        instance.component.run_now()
        self.assertTrue(state.active)
        return state

    def test_actual_charge_and_cost_member_expressions_read_child_storage(self):
        _, pilot, _, instance, bot = self.scenario()
        child = bot.body_script.component
        child.set(342, 123.5)
        child.set(338, 999.0)
        self.assertEqual(instance.evaluate(instance.graph.node(147).field("m_value")), 123.5)
        self.assertEqual(instance.evaluate(instance.graph.node(148).field("m_value")), 999.0)
        self.assertEqual(instance.ruleset_target_entity, bot.entity)
        self.assertIs(pilot.body_script.world.entity_var_slot(bot.entity, 342), child.vars[342])
        child.set(342, 321.0)
        self.assertEqual(instance.evaluate(instance.graph.node(147).field("m_value")), 321.0)
        instance.run_node(instance.graph.node(148), None)  # actual148 ->147 save actions.
        self.assertEqual(instance.component.entity_var(2511).value(), 321.0)
        self.assertEqual(instance.component.entity_var(7351).value(), 999.0)
        child.set(342, 0.0)
        return_instance = next(i for i in child.instances.values() if i.graph.index == 0x276)
        return_instance.run_node(return_instance.graph.node(133), None)
        self.assertEqual(child.entity_var(342).value(), 321.0)

    def test_actual_pet_selector_and_charge_return_keep_pet_owner_separate_from_direct_owner(self):
        _, pilot, other, _, bot = self.scenario()
        child = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x276)
        pilot.body_script.component.set(2511, 123.5)
        cfg = child.graph.node(133).field("m_value")
        self.assertEqual(child.evaluate(cfg), 123.5)
        child.run_node(child.graph.node(133), None)
        self.assertEqual(child.component.entity_var(342).value(), 123.5)
        bot.owner = other.entity
        self.assertEqual(child.evaluate({"$": "STU_F7262B36", "m_801B298F": expr.Entity(bot.entity)}),
                         expr.Entity(other.entity))
        self.assertEqual(child.evaluate({"$": "STU_F76C486C", "m_D63CBEC2": expr.Entity(bot.entity)}),
                         expr.Entity(pilot.entity))
        self.assertEqual(child.evaluate(cfg), 123.5)
        other.body_script.component.set(2511, 55.0)
        bot.pet_owner = other.entity
        self.assertEqual(child.evaluate(cfg), 55.0)

    def test_initialization_registry_resolves_both_components_before_wrapper_publication(self):
        constructor = body_entities.BotScript
        observations = []

        def boot(*args, **kwargs):
            match = kwargs["match"]
            bot = next(b for b in match.bots if b.entity == args[2])
            pilot = next(p for p in match.players if p.entity == bot.owner)
            parent = match.body_components[pilot.body]
            parent.set(2511, 77.5)
            script = constructor(*args, **kwargs)
            child = next(i for i in script.component.instances.values() if i.graph.index == 0x276)
            observations.append((pilot.body_script is None, bot.body_script is None,
                match.body_components[bot.entity] is script.component,
                script.component.world.entity_var_slot(pilot.body, 2511) is parent.vars[2511],
                child.evaluate(child.graph.node(133).field("m_value"))))
            return script

        with patch.object(body_entities, "BotScript", side_effect=boot):
            self.scenario()
        self.assertEqual(observations, [(True, True, True, True, 77.5)])

    def test_child_initialization_failure_after_registration_rolls_back_its_namespace(self):
        run, _, _, instance, _ = self.scenario()
        before = dict(run.match.body_components), tuple(run.match.bots), run.match.combat.next_bot
        with (
            patch.object(observers, "snapshot", side_effect=RuntimeError("snapshot failed")),
            self.assertRaisesRegex(RuntimeError, "snapshot failed"),
        ):
            body_entities.create_mech(run.match, instance, instance.graph.node(7))
        after = run.match.body_components, tuple(run.match.bots), run.match.combat.next_bot
        self.assertEqual(after, before)

    def test_late_parent_initialization_failure_retires_created_children_and_registry_entries(self):
        run, pilot, _, _, _ = self.scenario()
        spawn = BodyScript._spawn

        def fail_after_spawn(script, *args):
            spawn(script, *args)
            raise RuntimeError("late parent initialization failure")

        with (
            patch.object(BodyScript, "_spawn", fail_after_spawn),
            self.assertLogs("ow174.game", level="ERROR"),
        ):
            run.match.switch_hero(pilot, find_hero("dva"))
        self.assertIsNone(pilot.body_script)
        self.assertNotIn(pilot.body, run.match.body_components)
        self.assertFalse(any(b.owner == pilot.entity for b in run.match.bots))

    def test_foreign_ruleset_uses_target_hero_default_then_variable_override_and_retires(self):
        run, pilot, other, instance, _ = self.scenario()
        key = 0x2982  # Soldier schema0013 has1.0; D.Va schema0015 does not supply this key.
        self.assertIsNone(pilot.body_script.world.ruleset(key))
        cfg = ruleset(expression([14, 0, 7, 0, 15, 0], [(key, 0)],
                                 configs=[expr.Entity(other.body)]))
        self.assertEqual(instance.evaluate(cfg), 1.0)
        self.assertEqual(instance.ruleset_target_entity, other.body)
        other.body_script.component.set(key, 0.5)
        self.assertEqual(instance.evaluate(cfg), 0.5)
        self.assertTrue(run.match.switch_hero(other, find_hero("dva")))
        self.assertEqual(instance.evaluate(cfg), 0)

    def test_actual_foreign_stack_tracks_child_flag_changes_and_exports_both_namespaces(self):
        _, pilot, _, instance, bot = self.scenario()
        child = bot.body_script.component
        child.set(842, False)
        state = self.stack(instance)
        slot = child.vars[842]
        self.assertIn(slot, state.watched)
        self.assertIn(state, slot.watchers)
        self.assertIs(state.value, False)
        parent_before = snapshot_digest(export_body_vm(pilot.body_script))
        child_before = snapshot_digest(export_component_vm(child))
        raw = replay_values.decode(export_body_vm(pilot.body_script)["value"])
        self.assertEqual([row["entity"] for row in raw["external_components"]], [bot.entity])
        raw = replay_values.decode(export_component_vm(child)["value"])
        self.assertEqual([row["entity"] for row in raw["external_components"]], [pilot.body])
        child.set(842, True)
        self.assertNotEqual(snapshot_digest(export_body_vm(pilot.body_script)), parent_before)
        self.assertNotEqual(snapshot_digest(export_component_vm(child)), child_before)
        instance.component.run_now()
        self.assertIs(state.value, True)
        self.assertIs(instance.component.entity_var(22319).value(), True)
        self.assertEqual(instance.component.frame, pilot.body_script.frame)

    def test_target_retirement_disconnects_subscription_and_schedules_receiver_recheck(self):
        run, pilot, _, instance, bot = self.scenario()
        bot.body_script.component.set(842, True)
        state = self.stack(instance)
        slot = bot.body_script.component.vars[842]
        npc.remove_body(run.match, bot)
        self.assertNotIn(state, slot.watchers)
        self.assertNotIn(slot, state.watched)
        events = [e for e in instance.queue if e.kind == runtime.CHANGED and e.state is state]
        self.assertTrue(events)
        self.assertTrue(all(e.time == instance.component.now for e in events))
        instance.component.run_now()
        self.assertIsNone(pilot.body_script.world.entity_var_slot(bot.entity, 842))
        self.assertNotIn(slot, state.watched)
        self.assertIsInstance(export_body_vm(pilot.body_script), dict)

    def test_receiver_retirement_releases_foreign_var_without_mutating_its_value(self):
        _, pilot, _, instance, bot = self.scenario()
        child = bot.body_script.component
        child.set(842, True)
        state = self.stack(instance)
        slot = child.vars[842]
        pilot.body_script.retire()
        self.assertNotIn(state, slot.watchers)
        self.assertNotIn(slot, state.watched)
        self.assertIs(slot.value(), True)
        child.set(842, False)
        self.assertFalse(any(e.kind == runtime.CHANGED and e.state is state for e in instance.queue))

    def test_live_death_and_death_timer_failure_disconnect_foreign_subscriptions(self):
        for method in ("advance_live", "die", "advance_death"):
            with self.subTest(method=method):
                run, _, enemy, instance, bot = self.scenario()
                script = bot.body_script
                script.component.set(842, True)
                state = self.stack(instance)
                slot = script.component.vars[842]
                with (
                    patch.object(script, method, side_effect=RuntimeError("VM failed")),
                    self.assertLogs("ow174.game", level="ERROR"),
                ):
                    if method == "advance_live":
                        run.step({})  # the controlled initial mech advances through owner commands
                    elif method == "die":
                        run.match.combat._bot_died(enemy, bot, False, None, run.match.tick)
                    else:
                        run.match.combat._advance_bot_death(bot, run.match.tick+1)
                self.assertIsNone(bot.body_script)
                self.assertIsNone(script.component.world.match)
                self.assertNotIn(slot, state.watched)
                self.assertNotIn(state, slot.watchers)
                instance.component.run_now()

    def test_cross_vm_notification_uses_receivers_clock_and_preserves_fifo_coalescing(self):
        receiver, instance, _, _, _ = alias_tests.fixture()
        for allocated in receiver.instances.values():
            allocated.init()
        receiver.run_frame(5000)
        sender = runtime.Component(123)
        sender.run_frame(100)
        state = instance.states[0]
        state.begin()
        slot = sender.entity_var(77)
        state.watch(slot)
        sender.set(77, 1)
        sender.set(77, 2)
        events = [e for e in instance.queue if e.kind == runtime.CHANGED and e.state is state]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].time, receiver.now)
        self.assertNotEqual(events[0].time, sender.now)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("export evaluated a getter")):
            self.assertIsInstance(export_component_vm(receiver), dict)
            self.assertIsInstance(export_component_vm(sender), dict)
        receiver.run_now()
        self.assertFalse(state.flags_changed)

    def test_server_snapshot_bridges_subscriptions_without_flattening_two_component_namespaces(self):
        case = storage_tests.ServerSnapshotTests()
        session, peers, players = case.scene()
        pilot = players[0]
        peers[0].message(21616, storage_tests.pick(pilot.entity, body_entities.DVA), now=101.0)
        instance = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x26A)
        bot = next(b for b in pilot.match.bots if b.owner == pilot.entity)
        bot.body_script.component.set(842, True)
        self.stack(instance)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("export evaluated a getter")):
            first = storage_tests.snapshot_digest(case.capture(session))
            self.assertEqual(first, storage_tests.snapshot_digest(case.capture(session)))
        bot.body_script.component.set(842, False)
        self.assertNotEqual(first, storage_tests.snapshot_digest(case.capture(session)))
        instance.component.run_now()
        npc.remove_body(pilot.match, bot)
        instance.component.run_now()
        self.assertIsInstance(case.capture(session), dict)


if __name__ == "__main__":
    unittest.main()
