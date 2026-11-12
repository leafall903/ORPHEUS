"""Actual Assassin selection, incoming damage and effect delivery.

Fixtures exercise the reconstructed manual profile, explicit filter inputs
and fail-closed cases. Native automatic targeting remains open.
"""

import json
import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import test_ashe_pve_npcs as fixtures

from ow174.game import assassin, combat, heroselect, immunity, npc_console, npc_control, statescript, world
from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm
from ow174.game.script import (
    decode,
    expr,
    filter_bits,
    filter_condition,
    filter_game,
    graph,
    play,
    runtime,
)
from ow174.game.script.controller_filter import ControllerFilterInputs

CONTROLLERS = Path(os.environ.get("ORPHEUS_PVE_CONTROLLER_EXTENSION", "."))
PRESENCE = Path(os.environ.get("ORPHEUS_CAPTURED_CONTROLLER_PRESENCE", "."))


@unittest.skipUnless(fixtures.CATALOG.is_file() and fixtures.BUNDLE.is_file() and CONTROLLERS.is_file(),
                     "requires actual NPC AI-root metadata")
class AssassinBoundaryTests(unittest.TestCase):
    fight = fixtures.AshePveNpcTests.fight
    press = fixtures.AshePveNpcTests.press
    spawn = fixtures.AshePveNpcTests.spawn

    @classmethod
    def setUpClass(cls):
        cls.controller_patch = patch.dict(os.environ, {"ORPHEUS_PVE_GRAPH_EXTENSION": str(CONTROLLERS)})
        cls.controller_patch.start()
        fixtures.AshePveNpcTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass.__func__(cls)
        cls.controller_patch.stop()

    def setup(self, distance=2):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot = self.spawn(definition, distance)
        player.mover.yaw, player.mover.pitch = fixtures.fixtures.aim(
            run.match.combat._eye(player), (0, bot.position[1] + 0.75, distance))
        context = bot.body_script.control_context
        root = context.controller_component
        instance = next(item for item in root.instances.values() if item.graph.index == 0x13C2)
        return run, player, bot, context, instance

    def test_actual_writer_runs_but_unsupported_filter_clears_target_without_damage(self):
        run, player, bot, context, instance = self.setup()
        host = player.controller_vm
        # A declared primary-status component with no payload must fail closed.
        profile = ControllerFilterInputs(
            player.entity, ((1 << 25) | (1 << 26) | (1 << 47) | (1 << 57), 1 << (97 - 64)),
            0, player.controller_filter_bits, provenance="test profile with unavailable primary status")
        host.bind_filter_inputs(profile)
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        vitals = run.match.combat.vitals_of(target)
        before = vitals.total
        root = context.controller_component
        with patch.object(instance, "write", wraps=instance.write) as writes:
            self.assertEqual(npc_control.start(run.match, player), bot.entity)
            for _ in range(100):
                run.step({})
        target_writes = [call.args[1] for call in writes.call_args_list
                         if call.args[0] == (expr.ENTITY, 1650)]
        self.assertIs(host.filter_inputs, profile)
        self.assertIn(expr.Entity(player.entity), target_writes)
        self.assertEqual(root.entity_var(1650).value(), 0)
        self.assertEqual(root.entity_var(14365).value(), expr.Entity(player.entity))
        self.assertEqual(vitals.total, before)
        behavior = instance.node_state(instance.graph.node(1))
        self.assertTrue(behavior.selected)
        self.assertIsNone(root.fault)
        self.assertIsNone(bot.body_script.component.fault)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        self.assertFalse(behavior.selected)
        self.assertEqual(context.target_entity, 0)
        self.assertEqual(root.entity_var(14365).value(), expr.Entity(0))
        for pos in (4, 6, 8, 10, 15):
            state = instance.node_state(instance.graph.node(pos))
            self.assertFalse(state is not None and state.active, pos)

    def test_manual_command_hosts_live_pin_without_fixture_profile_or_effect_services(self):
        run, player, bot, context, _instance = self.setup()
        host = player.controller_vm
        self.assertIsNone(host.filter_inputs)
        self.assertIsNone(host.component.world.pin_services)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        self.assertEqual(host.filter_inputs.replicated_bits, player.controller_filter_bits)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        for _ in range(95):
            run.step({})
            if vitals.total < 175:
                break
        self.assertLess(vitals.total, 175)
        self.assertIs(context.target_host, host)
        effect, = host.component.instances.values()
        self.assertEqual(effect.graph.index, 0x1177)
        self.assertEqual(effect.source_entity(), bot.entity)
        self.assertTrue(host.component.entity_var(14695).value())
        update = next(item for item in reversed(player.client.entities)
                      if item.stream is player.script and item.resend)
        active = decode.read_chunk(update.chunk.getvalue(), update.chunk.count)
        self.assertIn(effect.id, active.instances)
        npc_control.stop(run.match)
        self.assertFalse(host.component.instances)
        self.assertIsNone(host.component.entity_var(14695).value())
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, before)
        update = next(item for item in reversed(player.client.entities)
                      if item.stream is player.script and item.resend)
        released = decode.read_chunk(update.chunk.getvalue(), update.chunk.count)
        self.assertGreater(released.cmfd, active.cmfd)
        self.assertNotIn(effect.id, released.instances)

    def test_authored_body_criterion_controls_manual_selection(self):
        run, player, bot, context, instance = self.setup()
        npc_control.start(run.match, player)
        run.step({})
        behavior = instance.node_state(instance.graph.node(1))
        self.assertTrue(behavior.selected)
        # The actual criterion reads possessed-body13401. A false value must
        # deselect the behavior even while a manual command still has a target.
        bot.body_script.component.set(13401, 0)
        context.advance(context.controller_component.frame + 1)
        self.assertFalse(behavior.selected)
        self.assertNotEqual(context.target_entity, 0)
        self.assertFalse(instance.node_state(instance.graph.node(15)).active)

    def test_manual_override_uses_controller_category_from_actual_record_builders(self):
        run, player, _bot, context, _instance = self.setup()
        with patch.object(world, "create") as create:
            run.match._player_create(0, player, own=False)
            controller_bits = create.call_args.args[2][26][0]
            run.match._body_builder(player, None, own=False)(0)
            body_bits = create.call_args.args[2][26][0]
        controller = filter_bits.project(0, controller_bits, status10=False)
        body = filter_bits.project(0, body_bits, status10=False)
        self.assertTrue(filter_bits.matches(controller, 0x40000, 0, 0x130))
        self.assertFalse(filter_bits.matches(body, 0x40000, 0, 0x130))
        self.assertFalse(filter_bits.matches(
            filter_bits.project(0, controller_bits, status10=True), 0x40000, 0, 0x130))
        npc_control.start(run.match, player)
        run.step({})
        self.assertEqual(context.target_entity, player.entity)
        self.assertNotEqual(context.target_entity, player.body)

    def test_actual_condition_observes_actual_target_stack_and_its_retirement(self):
        _run, player, _bot, context, instance = self.setup()
        target = player.body_script.component
        condition = instance.graph.node(10).field("m_condition")["m_filter"]["m_condition"]
        original = context.controller_component.world.entity_var_slot

        def lookup(entity, identifier, *, create=False):
            # Explicit fixture binding to the real target VM, not a constant
            # eligibility answer or an inferred general engine registry.
            if entity == player.body:
                return target.entity_var(identifier, create=create)
            return original(entity, identifier, create=create)

        with patch.object(context.controller_component.world, "entity_var_slot", side_effect=lookup):
            self.assertTrue(filter_condition.evaluate(instance, condition, player.body))
            effect = target.create(graph.graph(0x1177), init=False)
            # Isolate the actual flag writer; this is not acceptance of effect
            # acquisition, the rest of1177 or Assassin's targeted health change.
            writer = effect.node_state(effect.graph.node(46))
            writer.begin()
            self.assertTrue(target.entity_var(14695).value())
            self.assertFalse(filter_condition.evaluate(instance, condition, player.body))
            target.destroy(effect)
            self.assertFalse(expr.truthy(target.entity_var(14695).value()))
            self.assertTrue(filter_condition.evaluate(instance, condition, player.body))
        self.assertEqual(context.controller_component.parameter_scopes, [])

    def test_paired_actual_vms_do_not_alias_controller_and_body_condition_flags(self):
        _run, _player, _bot, context, instance = self.setup()
        condition = instance.graph.node(10).field("m_condition")["m_filter"]["m_condition"]
        root, body = context.controller_component, context.body_component
        root_effect = root.create(graph.graph(0x1177), init=False)
        body_effect = body.create(graph.graph(0x1177), init=False)
        root_effect.node_state(root_effect.graph.node(46)).begin()
        self.assertFalse(filter_condition.evaluate(instance, condition, context.controller))
        self.assertTrue(filter_condition.evaluate(instance, condition, context.body))
        body_effect.node_state(body_effect.graph.node(46)).begin()
        root.destroy(root_effect)
        self.assertTrue(filter_condition.evaluate(instance, condition, context.controller))
        self.assertFalse(filter_condition.evaluate(instance, condition, context.body))
        body.destroy(body_effect)
        self.assertTrue(filter_condition.evaluate(instance, condition, context.body))
        self.assertEqual(root.parameter_scopes, [])

    def test_live_target_controller_flag_notifies_foreign_watcher_and_stop_unbinds(self):
        run, player, _bot, context, instance = self.setup()
        host = player.controller_vm
        # Isolate watcher ownership without mounting the pin service domain.
        host.bind_filter_inputs(ControllerFilterInputs(
            player.entity, (1 << 47, 0), 0, None, provenance="standalone watcher fixture"))
        npc_control.start(run.match, player)
        run.step({})
        root = context.controller_component
        root.run_now()
        self.assertIs(context.target_host, host)
        self.assertEqual(host.component.world.readers, [context])
        condition = instance.graph.node(10).field("m_condition")["m_filter"]["m_condition"]
        watcher = instance.node_state(instance.graph.node(10))
        self.assertTrue(watcher.active)
        slot = host.component.entity_var(14695)
        self.assertTrue(filter_condition.evaluate(instance, condition, player.entity, watcher=watcher))
        self.assertIn(watcher, slot.watchers)
        self.assertIn(slot, watcher.watched)
        with self.assertRaisesRegex(BodyVMSnapshotError, "enclosing server-domain"):
            export_component_vm(host.component)
        effect = host.component.create(graph.graph(0x1177), init=False)
        effect.node_state(effect.graph.node(46)).begin()
        self.assertTrue(watcher.flags_changed)
        self.assertFalse(filter_condition.evaluate(instance, condition, player.entity))
        self.assertIsNone(player.body_script.component.entity_var(14695, create=False))
        npc_control.stop(run.match)
        self.assertEqual(host.component.world.readers, [])
        self.assertNotIn(watcher, slot.watchers)
        self.assertNotIn(slot, watcher.watched)
        self.assertIsNone(context.target_host)
        host.component.destroy(effect)
        export_component_vm(host.component)

    def test_target_host_retirement_clears_binding_without_reusing_its_variables(self):
        run, player, _bot, context, instance = self.setup()
        npc_control.start(run.match, player)
        run.step({})
        host = player.controller_vm
        condition = instance.graph.node(10).field("m_condition")["m_filter"]["m_condition"]
        watcher = instance.node_state(instance.graph.node(10))
        slot = host.component.entity_var(14695)
        filter_condition.evaluate(instance, condition, player.entity, watcher=watcher)
        self.assertIn(watcher, slot.watchers)
        run.match.leave(player)
        self.assertTrue(host.retired)
        self.assertEqual(host.component.world.readers, [])
        self.assertEqual(context.target_entity, 0)
        self.assertIsNone(context.target_host)
        self.assertNotIn(watcher, slot.watchers)
        self.assertNotIn(slot, watcher.watched)
        self.assertIs(context.variable(player.entity, 14695), NotImplemented)

    def test_invalid_target_host_rebinding_preserves_current_binding_atomically(self):
        run, player, _bot, context, _instance = self.setup()
        npc_control.start(run.match, player)
        run.step({})
        host = player.controller_vm
        for entity, candidate in ((player.body, host), (None, host), (player.entity, object())):
            with self.subTest(entity=entity, host=type(candidate).__name__):
                with self.assertRaisesRegex(ValueError, "live independent controller host"):
                    context.set_target(entity, host=candidate)
                self.assertEqual(context.target_entity, player.entity)
                self.assertIs(context.target_host, host)
                self.assertEqual(host.component.world.readers, [context])

    def test_actual_filter_empty_callbacks_and_live_flag_with_declared_component_inputs(self):
        run, player, _bot, context, instance = self.setup()
        npc_control.start(run.match, player)
        run.step({})
        context.controller_component.run_now()
        host = player.controller_vm
        config = instance.graph.node(10).field("m_condition")["m_filter"]
        self.assertIsNone(config["m_93FA1642"])
        self.assertIsNone(config["m_C1903771"])
        with patch.object(world, "create") as create:
            run.match._player_create(0, player, own=False)
            replicated_bits = create.call_args.args[2][26][0]
        # Explicit isolated engine-input fixture. These presence/status/source
        # answers do not enable production AC829876 or prove live native input.
        services = Mock(spec=("lookup", "has_component", "bits", "source", "observe", "engine_filter"))
        services.lookup.return_value = host
        services.has_component.return_value = True
        services.bits.return_value = filter_bits.project(0, replicated_bits, status10=False)
        services.source.return_value = None
        services.engine_filter.side_effect = AssertionError("empty rule path must not query registry")
        watcher = instance.node_state(instance.graph.node(10))
        self.assertTrue(watcher.active)
        self.assertTrue(filter_game.evaluate(instance, config, player.entity,
                                             services=services, watcher=watcher))
        effect = host.component.create(graph.graph(0x1177), init=False)
        effect.node_state(effect.graph.node(46)).begin()
        self.assertFalse(filter_game.evaluate(instance, config, player.entity,
                                              services=services, watcher=watcher))
        host.component.destroy(effect)
        self.assertTrue(filter_game.evaluate(instance, config, player.entity,
                                             services=services, watcher=watcher))
        services.engine_filter.assert_not_called()
        self.assertIsNone(player.body_script.component.entity_var(14695, create=False))
        self.assertEqual(context.controller_component.parameter_scopes, [])

    def bind_profile(self, run, player):
        captured = json.loads(PRESENCE.read_text())
        sample = next(row for row in captured["observations"] if row["entity_id"] == "A0000100")
        self.assertFalse(sample["derived_status10"])
        masks = tuple(int(word, 16) for word in sample["component_masks"])
        host = player.controller_vm
        with patch.object(world, "create") as create:
            run.match._player_create(0, player, own=False)
            replicated_bits = create.call_args.args[2][26][0]
        profile = ControllerFilterInputs(player.entity, masks, 0, replicated_bits,
                                         provenance="manual reconstruction from verified retained sample")
        host.bind_filter_inputs(profile)
        return profile, replicated_bits

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_actual_linked_target_state_requires_declared_mover_inputs(self):
        for presence in ("unknown", "present", "absent", "sample"):
            with self.subTest(presence=presence):
                run, player, bot, _, _ = self.setup()
                host = player.controller_vm.component
                if presence != "unknown":
                    profile, _ = self.bind_profile(run, player)
                    if presence in ("present", "sample"):
                        first, *rest = profile.component_masks
                        profile.update(component_masks=(first | (1 << 31), *rest))
                    if presence == "sample":
                        player.controller_vm.set_linked_target_sample(bot.entity, 0.25, None, None)
                effect = host.create(graph.graph(0x1177), init=False)
                effect.set_source_entity(bot.entity)
                effect.init()
                host.run_now()
                state = effect.node_state(effect.graph.node(9))
                state.request(runtime.BEGIN)
                if presence in ("absent", "sample"):
                    host.run_now()
                    self.assertTrue(state.active)
                    if presence == "sample":
                        self.assertEqual(state.mover_angles, (0.0,) * 6)
                        self.assertEqual(state.payload()["entity"], bot.entity)
                    active = export_component_vm(host)
                    state.request(runtime.FINISH)
                    host.run_now()
                    self.assertFalse(state.active)
                    self.assertNotEqual(export_component_vm(host), active)
                else:
                    with self.assertRaises(runtime.UnsupportedScriptError) as error:
                        host.run_now()
                    self.assertEqual(error.exception.diagnostic["feature"], "linked target")

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_concurrent_linked_target_states_use_registration_order_and_cleanup(self):
        run, player, bot, _, _ = self.setup()
        profile, _ = self.bind_profile(run, player)
        first, *rest = profile.component_masks
        profile.update(component_masks=(first | (1 << 31), *rest))
        vm = player.controller_vm
        host = vm.component
        effects = [host.create(graph.graph(0x1177), init=False) for _ in range(2)]
        states = []
        for effect in effects:
            effect.set_source_entity(bot.entity)
            effect.init()
            host.run_now()
            states.append(effect.node_state(effect.graph.node(9)))

        # Begin in reverse allocation order. The native list follows activation.
        for state, angle, heading in ((states[1], 0.25, 0.0), (states[0], 0.5, 90.0)):
            vm.set_linked_target_sample(bot.entity, angle, heading, 0.0)
            state.request(runtime.BEGIN)
            host.run_now()
            state.instance.auto_retire = True
        last, earlier = states
        self.assertEqual(host.state_registries[last.node.cls], [earlier, last])
        self.assertEqual(earlier.mover_angles[0], 0.25)
        yaw = expr.f32(90 * 0.01745329238474369)
        self.assertEqual(vm.linked_target_angles(), (yaw, 0.0, yaw, 0.0, yaw, 0.0))

        def owner_states():
            dynamic, bound_vars, _ = run.match._controller_effect_data(player)
            full = statescript.owner_full_frame(host.frame, [], {}, dynamic=dynamic, bound_vars=bound_vars)
            chunk = statescript.chunk(full, 0, 1)
            return decode.read_chunk(chunk.getvalue(), chunk.count).instances

        wire = owner_states()
        self.assertEqual(wire[earlier.instance.id].states[earlier.index][1]["link"],
                         (last.instance.id, last.index))
        self.assertNotIn("link", wire[last.instance.id].states[last.index][1])
        last.instance.auto_retire = False
        wire = owner_states()
        self.assertNotIn(last.instance.id, wire)
        self.assertNotIn("link", wire[earlier.instance.id].states[earlier.index][1])
        last.instance.auto_retire = True

        vm.set_linked_target_sample(player.entity, 0.75, 0.0, 0.0)
        host.run_frame(host.frame + 1)
        self.assertEqual([s.linked_entity for s in states], [player.entity, player.entity])
        self.assertEqual(vm.linked_target_angles(), (yaw, 0.0, yaw, 0.0, yaw, 0.0))
        host.destroy(last.instance)
        self.assertEqual(vm.linked_target_angles(), (0.25, 0.0, 0.25, 0.0, 0.25, 0.0))
        wire = owner_states()
        self.assertNotIn(last.instance.id, wire)
        self.assertNotIn("link", wire[earlier.instance.id].states[earlier.index][1])
        host.destroy(earlier.instance)
        self.assertIsNone(vm.linked_target_angles())
        self.assertNotIn(last.node.cls, host.state_registries)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_actual_effect_start_reaches_scope_registration_with_declared_mover_absence(self):
        run, player, bot, _, _ = self.setup()
        self.bind_profile(run, player)
        npc_control.start(run.match, player)
        run.step({})
        host = player.controller_vm.component
        effect = host.create(graph.graph(0x1177), init=False)
        effect.set_source_entity(bot.entity)
        effect.init()
        effect.play_schema((), reorder_entries=True)
        self.assertEqual(effect.start_entries((), bot.entity), 1)
        with self.assertRaises(runtime.UnsupportedScriptError) as error:
            host.run_now()
        self.assertEqual(error.exception.diagnostic["feature"], "subgraph scope registration")
        self.assertEqual(error.exception.diagnostic["node"], 55)
        self.assertEqual(effect.node_state(effect.graph.node(1)).counter, 1)
        self.assertEqual(effect.node_state(effect.graph.node(9)).counter, 0)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_effect_owner_health_query_requires_declared_health_absence(self):
        for presence in ("unknown", "present", "absent"):
            with self.subTest(presence=presence):
                run, player, _, _, _ = self.setup()
                host = player.controller_vm.component
                if presence != "unknown":
                    profile, _ = self.bind_profile(run, player)
                    if presence == "present":
                        first, *rest = profile.component_masks
                        profile.update(component_masks=(first | (1 << 51), *rest))
                effect = host.create(graph.graph(0x1177), init=False)
                queries = [effect.graph.node(pos).field("m_condition")["m_configVars"][0] for pos in (21, 39)]
                if presence == "absent":
                    for query in queries:
                        self.assertEqual(effect.evaluate(query), 0.0)
                else:
                    with self.assertRaises(runtime.UnsupportedScriptError) as error:
                        effect.evaluate(queries[0])
                    self.assertEqual(error.exception.diagnostic["class"], "STU_9B7648C3")

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_effect_start_reaches_voice_after_owner_health_query(self):
        run, player, bot, _, _ = self.setup()
        self.bind_profile(run, player)
        npc_control.start(run.match, player)
        run.step({})
        host = player.controller_vm.component
        effect = host.create(graph.graph(0x1177), init=False)
        effect.set_source_entity(bot.entity)
        effect.init()
        effect.play_schema((), reorder_entries=True)
        self.assertEqual(effect.start_entries((), bot.entity), 1)
        with (patch.object(host.world, "subgraph_scopes", return_value=None),
              self.assertRaises(runtime.UnsupportedScriptError) as error):
            host.run_now()
        self.assertEqual(error.exception.diagnostic["feature"], "voice stimulus context")
        self.assertEqual(error.exception.diagnostic["node"], 33)
        self.assertEqual(effect.node_state(effect.graph.node(21)).counter, 1)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_opt_in_controller_inputs_drive_actual_native_gate_and_live_flag(self):
        run, player, bot, context, instance = self.setup()
        profile, replicated_bits = self.bind_profile(run, player)
        host = player.controller_vm
        npc_control.start(run.match, player)
        run.step({})
        root = context.controller_component
        root.run_now()
        config = instance.graph.node(10).field("m_condition")
        watcher = instance.node_state(instance.graph.node(10))
        self.assertEqual(root.entity_var(1650).value(), expr.Entity(player.entity))
        self.assertTrue(instance.evaluate(config, watcher))
        # Input changes are observed by existing native-expression polling, not
        # an invented component25 observer service or an explicit test read.
        profile.update(replicated_bits=replicated_bits | 0x20)
        run.step({})
        root.run_now()
        self.assertEqual(root.entity_var(1650).value(), 0)
        profile.update(replicated_bits=replicated_bits)
        npc_control.stop(run.match)
        run.step({})
        player.mover.yaw, player.mover.pitch = fixtures.fixtures.aim(
            run.match.combat._eye(player), (bot.position[0], bot.position[1] + 0.75, bot.position[2]))
        npc_control.start(run.match, player)
        run.step({})
        root.run_now()
        instance = next(item for item in root.instances.values() if item.graph.index == 0x13C2)
        config = instance.graph.node(10).field("m_condition")
        watcher = instance.node_state(instance.graph.node(10))
        self.assertTrue(watcher.current)
        self.assertTrue(instance.evaluate(config, watcher))
        slot = host.component.entity_var(14695)
        self.assertIn(watcher, slot.watchers)
        effect = host.component.create(graph.graph(0x1177), init=False)
        effect.node_state(effect.graph.node(46)).begin()
        self.assertFalse(instance.evaluate(config, watcher))
        self.assertTrue(watcher.flags_changed)
        root.run_now()
        self.assertEqual(root.entity_var(1650).value(), 0,
                         (root.now, host.component.now, watcher.pending, watcher.current,
                          [(event.time, event.kind) for event in instance.queue]))
        host.component.destroy(effect)
        host.bind_filter_inputs(None)
        self.assertTrue(profile.retired)
        self.assertIsNone(host.filter_inputs)
        self.assertIs(context.native_filter(config, instance, watcher=watcher), NotImplemented)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_actual_health_state_rejects_missing_owning_world_service(self):
        run, player, bot, context, _instance = self.setup()
        self.bind_profile(run, player)
        npc_control.start(run.match, player)
        run.step({})
        context.controller_component.run_now()
        self.assertEqual(context.controller_component.entity_var(1650).value(), expr.Entity(player.entity))
        body = bot.body_script.component
        attack = body.create(graph.graph(0x1157), init=False)
        writer = attack.graph.node(51)
        self.assertEqual(writer.cls, "STUStatescriptActionSetVar")
        self.assertTrue(attack.run_node(writer, None))
        self.assertEqual(attack.vars[536].value(), expr.Entity(player.entity))
        health_node = attack.graph.node(15)
        self.assertEqual(health_node.cls, "STUStatescriptStateModifyHealth")
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        vitals = run.match.combat.vitals_of(target)
        before = vitals.total
        body.strict = True
        with (patch.object(body.world, "modify_health", return_value=NotImplemented),
              self.assertRaises(runtime.UnsupportedScriptError) as rejected):
            attack.node_state(health_node).begin()
        self.assertIn("STUStatescriptStateModifyHealth", str(rejected.exception))
        self.assertEqual(vitals.total, before)
        self.assertIsNone(player.controller_vm.component.fault)
        self.assertIsNone(player.body_script.component.fault)

    def engage(self, *, distance=2):
        run, player, bot, context, _instance = self.setup(distance=distance)
        self.bind_profile(run, player)
        npc_control.start(run.match, player)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        return run, player, bot, context, vitals

    def test_assassin_pin_targets_ashe_controller_with_bob_in_between(self):
        from test_ashe_impulses import wall_scene

        run, player, bot, context, _instance = self.setup(distance=2.9)
        match = run.match
        fight = match.combat
        console = npc_console.Console(self.catalog)
        self.assertIsNone(player.controller_vm.filter_inputs)
        self.assertEqual(console.press("0", match, player)["entity"], bot.entity)
        self.assertIsNotNone(player.controller_vm.filter_inputs)
        open_world = match.collision_world
        match.collision_world = wall_scene(2.25)
        player.body_script.set_ult_charge(2240)
        run.step({player: (0, 0, 32)})
        actor, = fight.companions.active.values()
        for _ in range(24):
            if actor.phase != "charge":
                break
            run.step({})
        self.assertEqual(actor.stopped_reason, "wall")
        self.assertIsNone(actor.uppercut_target)
        self.assertFalse(bot.attack_input)
        self.assertEqual(fight.vitals[bot.entity].total, 500)

        with patch.object(world, "create") as create:
            match._bot_create_update(actor.bot).build(0)
            bob_bits = create.call_args.args[2][26][0]
        category = filter_bits.project(0, bob_bits, status10=False)
        self.assertFalse(filter_bits.matches(category, 0x40000, 0, 0x130))
        targets = {target.owner: target for target in fight._targets()}
        player_target, bob_target = targets[player], targets[actor.bot]
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        eye = fight._eye(player)
        distance = math.dist(origin, eye)
        direction = tuple((end - start) / distance for start, end in zip(origin, eye, strict=True))
        bob_hit = combat.ray_capsule(origin, direction, *bob_target.segment(), bob_target.radius)
        player_hit = combat.ray_capsule(origin, direction, *player_target.segment(), player_target.radius)
        self.assertIsNotNone(bob_hit)
        self.assertIsNotNone(player_hit)
        self.assertLess(bob_hit, player_hit)

        match.collision_world = open_world
        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
            console.press("0", match, player)
        self.assertEqual(bot.attack_target, player.entity)
        player_vitals, bob_vitals = fight.vitals_of(player_target), fight.vitals_of(bob_target)
        self.assertEqual(player_vitals.total, 200)
        attack = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(8):
                run.step({})
            self.assertEqual(player_vitals.total, 175)
            self.assertEqual(attack.vars[536].value(), expr.Entity(player.entity))
            self.assertEqual(context.target_entity, player.entity)
            for _ in range(90):
                if player_vitals.total < 175:
                    break
                run.step({})
            self.assertLess(player_vitals.total, 175)
            rate = attack.node_state(attack.graph.node(15))
            self.assertTrue(rate.active)
            self.assertEqual(rate.target_body, player.body)
            hits = [call.args for call in damage.call_args_list if call.args[0] is bot]
            self.assertGreaterEqual(len(hits), 2)
            self.assertTrue(all(args[1].owner is player for args in hits))
        self.assertEqual(bob_vitals.total, 1200)
        self.assertIsNone(bot.body_script.component.fault)
        self.assertEqual(console.press("{", match, player)["count"], 1)
        self.assertIsNone(bot.attack_target)
        health_after_stop = player_vitals.total
        for _ in range(45):
            run.step({})
        self.assertEqual(player_vitals.total, health_after_stop)
        self.assertEqual(bob_vitals.total, 1200)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_actual_manual_pin_applies_contact_then_delayed_rate_and_lethal_damage(self):
        run, player, bot, context, vitals = self.engage()
        self.assertEqual(vitals.total, 200)
        run.step({})
        self.assertEqual(vitals.total, 200)
        run.step({})
        self.assertEqual(vitals.total, 175)
        body = bot.body_script.component
        attack = next(i for i in body.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(attack.vars[536].value(), expr.Entity(player.entity))
        self.assertEqual(context.controller_component.entity_var(1650).value(), expr.Entity(player.entity))
        self.assertEqual(attack.node_state(attack.graph.node(25)).counter, 1)
        for _ in range(90):
            if vitals.total < 175:
                break
            run.step({})
        rate = attack.node_state(attack.graph.node(15))
        self.assertTrue(rate.active)
        self.assertEqual(rate.target_body, player.body)
        before, clock = vitals.total, rate.last_applied
        for _ in range(5):
            run.step({})
        self.assertAlmostEqual(before - vitals.total, (rate.last_applied - clock) * 0.1, places=4)
        for _ in range(130):
            if vitals.dead:
                break
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertEqual(body.shots, [])

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_effect_admission_flag_keeps_existing_pin_bound_until_release(self):
        run, player, bot, context, vitals = self.engage()
        for _ in range(2):
            run.step({})
        self.assertEqual(vitals.total, 175)
        host = player.controller_vm.component
        effect = host.create(graph.graph(0x1177), init=False)
        effect.init()
        host.run_now()
        effect.node_state(effect.graph.node(46)).begin()
        for _ in range(80):
            run.step({})
        self.assertTrue(host.entity_var(14695).value())
        self.assertEqual(context.controller_component.entity_var(1650).value(), 0)
        self.assertLess(vitals.total, 175)
        attack = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(attack.vars[536].value(), expr.Entity(player.entity))
        rate = attack.node_state(attack.graph.node(15))
        self.assertTrue(rate.active)
        self.assertEqual(rate.target_body, player.body)

        before = vitals.total
        npc_control.stop(run.match)
        for _ in range(12):
            run.step({})
        self.assertFalse(rate.active)
        self.assertEqual(vitals.total, before)
        npc_control.start(run.match, player)
        for _ in range(155):
            run.step({})
        self.assertEqual(attack.node_state(attack.graph.node(25)).counter, 1)
        self.assertEqual(vitals.total, before)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_manual_pin_reengages_with_fresh_input_after_release_and_wait(self):
        run, player, bot, _context, vitals = self.engage()
        for _ in range(12):
            run.step({})
        self.assertEqual(vitals.total, 175)
        npc_control.stop(run.match)
        run.step({})
        attack = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        release, wait, pin_input = (attack.node_state(attack.graph.node(i)) for i in (31, 37, 12))
        self.assertTrue(release.active)
        self.assertTrue(wait.active)
        npc_control.start(run.match, player)
        for _ in range(155):
            run.step({})
        self.assertFalse(release.active)
        self.assertFalse(wait.active)
        self.assertIn(release.ended_at - release.start, range(1466, 1484))
        self.assertIn(wait.ended_at - wait.start, range(2000, 2017))
        self.assertLess(release.ended_at, wait.ended_at)
        self.assertEqual(pin_input.counter, 2)
        self.assertEqual(vitals.total, 175)
        npc_control.stop(run.match)
        run.step({})
        npc_control.start(run.match, player)
        for _ in range(12):
            run.step({})
        self.assertEqual(vitals.total, 150)
        self.assertEqual(pin_input.counter, 3)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_manual_pin_damage_stops_on_release_wall_or_source_death(self):
        from test_ashe_impulses import wall_scene

        for stop in ("release", "wall", "source death"):
            with self.subTest(stop=stop):
                run, player, bot, _context, vitals = self.engage()
                for _ in range(95):
                    run.step({})
                self.assertLess(vitals.total, 175)
                if stop == "release":
                    npc_control.stop(run.match)
                elif stop == "wall":
                    run.match.collision_world = wall_scene(1)
                else:
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    fight._damage(player, target, 1000, False, None, run.frame)
                before = vitals.total
                for _ in range(12):
                    run.step({})
                self.assertEqual(vitals.total, before)

    def wait_for_live_pin_effect(self, run, player, bot, vitals):
        player.controller_vm.enable_pin_effects()
        host = player.controller_vm.component
        attack = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        for _ in range(95):
            run.step({})
            if vitals.total < 175:
                break
        rate = attack.node_state(attack.graph.node(15))
        self.assertTrue(rate.active)
        self.assertLess(vitals.total, 175)
        effect, = host.instances.values()
        self.assertEqual(effect.graph.index, 0x1177)
        self.assertTrue(host.entity_var(14695).value())
        return attack, rate, host, effect

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_coach_gun_breaks_live_assassin_pin_by_separating_the_bodies(self):
        run, player, bot, _context, vitals = self.engage()
        fight = run.match.combat
        attack, rate, host, effect = self.wait_for_live_pin_effect(run, player, bot, vitals)
        pitch = fixtures.fixtures.aim(fight._eye(player), (0, bot.position[1] + 1, bot.position[2]))[1]
        for _ in range(20):
            run.step({player: (0, pitch, 8)})
            if bot.entity in fight.impulses.pending:
                break
        self.assertIn(bot.entity, fight.impulses.pending)
        self.assertLess(fight.vitals[bot.entity].total, 500)
        self.assertFalse(bot.dead)
        for _ in range(20):
            run.step({})
            if effect.id not in host.instances:
                break
        self.assertGreater(math.dist(fight._eye(player),
                                     (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])),
                           assassin.PIN_RANGE)
        self.assertNotIn(effect.id, host.instances)
        self.assertIsNone(host.entity_var(14695).value())
        self.assertFalse(rate.active)
        self.assertTrue(attack.node_state(attack.graph.node(31)).active)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, before)

    def add_teammate(self, run, player, hero, position):
        from test_game_combat import in_world

        from ow174.game.content import heroes

        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hero = heroes()[hero]
        teammate = match.add_player(3, hero.name, hero.guid, 2, False)
        in_world(match, teammate)
        run.players.append(teammate)
        self.assertTrue(match.switch_hero(teammate, hero))
        teammate.team = player.team
        teammate.mover.position[:] = position
        return teammate

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_flashbang_interrupts_live_assassin_pin_without_separation(self):
        run, player, bot, _context, vitals = self.engage()
        match = run.match
        fight = match.combat
        caster = self.add_teammate(run, player, 0x02E0000000000042, (0, bot.position[1], 5))
        attack, rate, host, effect = self.wait_for_live_pin_effect(run, player, bot, vitals)
        origin = tuple(bot.position)
        yaw, pitch = fixtures.fixtures.aim(fight._eye(caster), (0, bot.position[1] + 1, bot.position[2]))
        for tick in range(40):
            run.step({caster: (yaw, pitch, 16 if tick == 0 else 0)})
            if fight.is_stunned(bot):
                break
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(fight.vitals[bot.entity].total, 475)
        self.assertEqual(tuple(bot.position), origin)
        self.assertLess(math.dist(fight._eye(player), (origin[0], origin[1] + combat.EYE, origin[2])),
                        assassin.PIN_RANGE)
        before = vitals.total
        run.step({})
        self.assertNotIn(effect.id, host.instances)
        self.assertIsNone(host.entity_var(14695).value())
        self.assertFalse(rate.active)
        self.assertTrue(attack.node_state(attack.graph.node(31)).active)
        while fight.is_stunned(bot):
            run.step({})
            self.assertEqual(vitals.total, before)
        self.assertEqual(vitals.total, before)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_live_ice_wall_blocks_manual_selection_then_cancel_restores_pin(self):
        from test_ashe_ice_wall import AsheIceWallTests

        run, player, bot, _context, _instance = self.setup(distance=2.9)
        self.bind_profile(run, player)
        match = run.match
        fight = match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        mei = self.add_teammate(run, player, immunity.MEI, (0, bot.position[1], 8))
        _, pieces = AsheIceWallTests.cast_wall(self, run, mei, z=1.45)
        self.assertEqual(len(pieces), 5)
        yaw, pitch = fixtures.fixtures.aim(fight._eye(player), (0, bot.position[1] + 1, bot.position[2]))
        run.step({player: (yaw, pitch, 0)})
        self.assertIsNone(npc_control.selected(match, player))
        with self.assertRaises(ValueError):
            npc_control.start(match, player)
        self.assertEqual(vitals.total, 200)
        run.step({player: (yaw, pitch, 0), mei: (0, 0, 16)})
        self.assertFalse(fight.ice_walls.pieces)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        self.wait_for_live_pin_effect(run, player, bot, vitals)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_live_ice_wall_breaks_active_assassin_pin_without_separating_the_bodies(self):
        from test_ashe_ice_wall import AsheIceWallTests

        run, player, bot, context, vitals = self.engage(distance=2.9)
        mei = self.add_teammate(run, player, immunity.MEI, (0, bot.position[1], 8))
        attack, rate, host, effect = self.wait_for_live_pin_effect(run, player, bot, vitals)
        before_pose = tuple(player.mover.position), bot.position
        _, pieces = AsheIceWallTests.cast_wall(self, run, mei, z=1.45)
        self.assertEqual(len(pieces), 5)
        for _ in range(5):
            run.step({})
            if effect.id not in host.instances:
                break
        self.assertEqual((tuple(player.mover.position), bot.position), before_pose)
        self.assertIsNone(context.target_host)
        self.assertNotIn(effect.id, host.instances)
        self.assertIsNone(host.entity_var(14695).value())
        self.assertFalse(rate.active)
        self.assertTrue(attack.node_state(attack.graph.node(31)).active)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, before)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_viper_kill_clears_live_assassin_pin_and_retires_controller_binding(self):
        run, player, bot, context, _instance = self.setup()
        self.bind_profile(run, player)
        fight = run.match.combat
        yaw, pitch = fixtures.fixtures.aim(fight._eye(player), (0, bot.position[1] + 1, bot.position[2]))
        victim = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(victim)
        bot_vitals = fight.vitals[bot.entity]
        for _ in range(40):
            run.step({player: (yaw, pitch, 2)})
        for tick in range(400):
            run.step({player: (yaw, pitch, 2 if tick % 8 == 7 else 3)})
            if bot_vitals.total <= 50:
                break
        self.assertEqual(bot_vitals.total, 50)
        self.assertFalse(bot.dead)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        _attack, _rate, host, effect = self.wait_for_live_pin_effect(run, player, bot, vitals)
        for _ in range(40):
            run.step({player: (yaw, pitch, 2)})
        for _ in range(20):
            run.step({player: (yaw, pitch, 3)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(bot_vitals.killer, player.body)
        self.assertTrue(context.retired)
        self.assertNotIn(effect.id, host.instances)
        self.assertIsNone(host.entity_var(14695).value())
        self.assertFalse(vitals.dead)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, before)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_bob_uppercut_breaks_live_assassin_pin(self):
        run, player, bot, _context, vitals = self.engage(distance=2.9)
        fight = run.match.combat
        attack, rate, host, effect = self.wait_for_live_pin_effect(run, player, bot, vitals)
        height = bot.position[1]
        player.body_script.set_ult_charge(2240)
        run.step({player: (0, 0, 32)})
        actor, = fight.companions.active.values()
        for _ in range(20):
            run.step({})
            if effect.id not in host.instances:
                break
        self.assertEqual(actor.uppercut_target, bot.entity)
        self.assertEqual(fight.vitals[bot.entity].total, 380)
        self.assertGreater(bot.position[1], height)
        self.assertNotIn(effect.id, host.instances)
        self.assertIsNone(host.entity_var(14695).value())
        self.assertFalse(rate.active)
        self.assertTrue(attack.node_state(attack.graph.node(31)).active)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, before)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_admitted_controller_outside_reconstructed_contact_range_takes_no_damage(self):
        run, player, bot, context, _instance = self.setup(distance=8)
        self.bind_profile(run, player)
        npc_control.start(run.match, player)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        for _ in range(100):
            run.step({})
        self.assertEqual(context.controller_component.entity_var(1650).value(), expr.Entity(player.entity))
        self.assertEqual(vitals.total, 200)
        states = (state for i in bot.body_script.component.instances.values() for state in i.states.values())
        self.assertFalse(any(s.active and s.node.cls == "STUStatescriptStateModifyHealth" for s in states))

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_delayed_rate_cannot_transfer_to_replacement_body_of_admitted_controller(self):
        run, player, bot, context, old_vitals = self.engage()
        for _ in range(2):
            run.step({})
        self.assertEqual(old_vitals.total, 175)
        old_body, old_position = player.body, tuple(player.mover.position)
        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old_body)
        player.mover.position[:] = old_position  # position the replacement before its first step
        fight = run.match.combat
        new_vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        for _ in range(95):
            run.step({})
        self.assertTrue(bot.attack_input)
        self.assertEqual(context.controller_component.entity_var(1650).value(), expr.Entity(player.entity))
        self.assertEqual(new_vitals.total, 200)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_authored_pin_submits_effect_and_retires_it_on_manual_release(self):
        for present in (False, True):
            with self.subTest(mover_present=present):
                self.check_authored_pin_submission(present)

    def check_authored_pin_submission(self, present):
        run, player, bot, context, vitals = self.engage()
        vm = player.controller_vm
        host = vm.component
        if present:
            first, *rest = vm.filter_inputs.component_masks
            vm.filter_inputs.update(component_masks=(first | (1 << 31), *rest))
            vm.set_linked_target_sample(bot.entity, 0.25, 0.0, 90.0)
        vm.enable_pin_effects()
        services = host.world.pin_services
        registry, messages = services.registry, services.messages
        for _ in range(80):
            run.step({})
        effect, = host.instances.values()
        self.assertEqual(effect.graph.index, 0x1177)
        self.assertEqual(effect.source_entity(), bot.entity)
        self.assertTrue(effect.auto_retire)
        health_gate = effect.node_state(effect.graph.node(21))
        self.assertIs(health_gate.current, False)
        receiver = effect.states.get(effect.graph.node(17).state)
        self.assertTrue(receiver is None or not receiver.active)
        self.assertTrue(host.entity_var(14695).value())
        self.assertEqual(context.controller_component.entity_var(1650).value(), 0)
        self.assertLess(vitals.total, 175)
        attack = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        attack.run_node(attack.graph.node(45), None)
        self.assertEqual(tuple(host.instances.values()), (effect,))
        stock, variables = heroselect.controller_instances(run.match.controller, False)
        owner_states = {
            node.state: statescript.StateWire(node.cls, True, state.payload())
            for node in effect.graph.owner_states()
            if (state := effect.states.get(node.state)) is not None and state.active
        }
        dynamic = statescript.InstanceWire(effect.id, effect.graph, states=owner_states)
        flag = host.entity_var(14695)
        link, = flag.links
        binding = statescript.Binding(effect.id, link.state.index, link.slot, link.priority, link.above)
        full = statescript.owner_full_frame(
            host.frame, stock, variables,
            dynamic=(dynamic,), bound_vars={14695: (flag.value(), (binding,))},
        )
        chunk = statescript.chunk(full, 0, 1)
        decoded = decode.read_chunk(chunk.getvalue(), chunk.count)
        linked = effect.graph.node(9).state
        self.assertEqual(set(decoded.instances), {item.index for item in stock} | {effect.id})
        expected_payload = {"has_entity": 0, "values": [{"set": 0}] * 7}
        linked_state = effect.node_state(effect.graph.node(9))
        if present:
            pitch = expr.f32(90 * 0.01745329238474369)
            expected_payload["has_entity"] = 1
            expected_payload["entity"] = bot.entity
            values = (-0.25, pitch, 0.0, 0.0, 0.0, 0.0, 0.25)
            expected_payload["values"] = [{"set": 1, "value": item} for item in values]
            clamp = 1.5533430576324463
            self.assertEqual(linked_state.mover_angles, (0.0, pitch, 0.0, clamp, 0.0, clamp))
        self.assertEqual(decoded.instances[effect.id].states[linked], (True, expected_payload))
        value, bindings = decoded.entity_vars[14695]
        self.assertIs(value, True)
        self.assertEqual([(item.instance, item.state) for item in bindings],
                         [(effect.id, effect.graph.node(46).state)])
        sent = [item for item in player.client.entities if item.stream is player.script and item.resend]
        active_update = sent[-1]
        active_frame = decode.read_chunk(active_update.chunk.getvalue(), active_update.chunk.count)
        self.assertIn(effect.id, active_frame.instances)
        self.assertTrue(active_frame.entity_vars[14695][0])
        self.assertGreaterEqual(player.script.delivered, active_update.chunk_last)
        self.assertFalse(player.script.data_in_flight)
        if present:
            vm.set_linked_target_sample(bot.entity, 0.5, 180.0, -90.0)
            run.step({})
            self.assertEqual(linked_state.mover_angles, (0.25, pitch, 0.25, clamp, 0.25, clamp))
            vm.set_linked_target_sample(player.entity, 0.75, 180.0, -90.0)
            run.step({})
            self.assertEqual(linked_state.mover_angles, (0.25, pitch, 0.25, clamp, 0.25, clamp))
            self.assertEqual(linked_state.payload()["entity"], player.entity)
            run.step({player: (10000, -8000, 0)})
            self.assertEqual((player.mover.yaw, player.mover.pitch), (2608, 16202))
            self.assertEqual((player.mover.last_command.yaw, player.mover.last_command.pitch), (2608, 16202))
        npc_control.stop(run.match)
        self.assertIsNone(linked_state.mover_angles)
        self.assertEqual(host.instances, {})
        self.assertIsNone(host.entity_var(14695).value())
        self.assertEqual(registry.states, [])
        self.assertFalse(messages.states)
        with patch.object(player.script, "arrived"):
            for _ in range(4):
                run.step({})
            sent = [item for item in player.client.entities
                    if item.stream is player.script and item.resend]
            removal = sent[-1]
            removed = decode.read_chunk(removal.chunk.getvalue(), removal.chunk.count)
            self.assertGreater(removed.cmfd, active_frame.cmfd)
            self.assertNotIn(effect.id, removed.instances)
            self.assertNotIn(14695, removed.entity_vars)
            self.assertTrue(player.script.data_in_flight)
            last = player.script.last
            run.match._owner_ack(player, player.script, run.frame * 0.016)
            self.assertEqual(player.script.last, last)
        player.script.arrived(removal.chunk_last)
        self.assertFalse(player.script.data_in_flight)
        if present:
            run.step({player: (10000, -8000, 0)})
            self.assertEqual((player.mover.yaw, player.mover.pitch), (10000, -8000))

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_actual_controller_effect_request_derives_context_then_rejects_missing_submission(self):
        run, player, bot, context, _instance = self.setup()
        self.bind_profile(run, player)
        npc_control.start(run.match, player)
        run.step({})
        context.controller_component.run_now()
        attack = bot.body_script.component.create(graph.graph(0x1157), init=False)
        self.assertTrue(attack.run_node(attack.graph.node(51), None))
        self.assertEqual(attack.vars[536].value(), expr.Entity(player.entity))
        host = player.controller_vm.component
        before = tuple(host.instances)
        effect_request = attack.graph.node(45)
        self.assertEqual(effect_request.cls, "STUStatescriptActionPlayScript")
        bot.body_script.component.strict = True
        with (patch.object(bot.body_script.component.world, "submit_script_request",
                           wraps=bot.body_script.component.world.submit_script_request) as submit,
              self.assertRaises(runtime.UnsupportedScriptError) as rejected):
            attack.run_node(effect_request, None)
        request = submit.call_args.args[2]
        self.assertEqual(request.resource.guid, 0x0580000000001177)
        self.assertEqual(request.targets, (player.entity,))
        self.assertEqual(request.context, play.InvocationContext(bot.entity, bot.entity, bot.entity))
        self.assertEqual((request.params, request.policy, request.wire_flag), ((), 0, 0))
        self.assertEqual(rejected.exception.diagnostic["feature"], "native PlayScript request")
        self.assertEqual(tuple(host.instances), before)
        self.assertIsNone(host.fault)
        self.assertIsNone(player.body_script.component.fault)

    @unittest.skipUnless(PRESENCE.is_file(), "requires verified retained controller presence sample")
    def test_installed_profile_with_unknown_present_payload_does_not_admit_in_strict_mode(self):
        run, player, _bot, context, instance = self.setup()
        profile, _bits = self.bind_profile(run, player)
        npc_control.start(run.match, player)
        run.step({})
        root = context.controller_component
        root.run_now()
        self.assertEqual(root.entity_var(1650).value(), expr.Entity(player.entity))
        profile.update(component_masks=(profile.component_masks[0] | (1 << 57),
                                         *profile.component_masks[1:]))
        root.strict = True
        config = instance.graph.node(10).field("m_condition")
        with self.assertRaises(runtime.UnsupportedScriptError) as caught:
            instance.evaluate(config, instance.node_state(instance.graph.node(10)))
        self.assertEqual(caught.exception.diagnostic["class"], "STU_AC829876")
        self.assertIs(root.fault, caught.exception)
        self.assertEqual(root.parameter_scopes, [])
        self.assertIsNone(player.controller_vm.component.fault)
        self.assertIsNone(player.body_script.component.fault)


if __name__ == "__main__":
    unittest.main()
