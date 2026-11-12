"""Original controller Pin services remain causal in whole-server storage."""
import unittest

import test_server_snapshot as fixtures
from ow174.game.script import graph
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest


class ControllerPinSnapshotTests(unittest.TestCase):
    def scene(self, *, mover=False):
        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        vm = player.controller_vm
        vm.configure_manual_pin(0)
        if mover:
            first, *rest = vm.filter_inputs.component_masks
            vm.filter_inputs.update(component_masks=(first | (1 << 31), *rest))
            vm.set_linked_target_sample(player.entity, .25, 0., 90.)
        vm.enable_pin_effects()
        effect = vm.component.create(graph.graph(0x1177), init=False)
        effect.set_source_entity(0x80002222)
        effect.init()
        vm.component.run_now()
        effect.play_schema((), reorder_entries=True)
        self.assertEqual(effect.start_entries((), 0x80002222), 1)
        vm.component.run_now()
        effect.auto_retire = True
        return case, session, player, vm, effect

    def test_pin_message_sequence_and_registry_alias_are_causal(self):
        case, session, player, vm, effect = self.scene()
        self.assertTrue(vm.component.entity_var(14695).value())
        services = vm.component.world.pin_services
        manager = services.messages.host.manager
        before = snapshot_digest(case.capture(session))
        manager.next_sequence += 1
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        manager.next_sequence -= 1
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        self.assertIs(effect.node_state(effect.graph.node(55)).scope_registry, services.registry)
        saved = list(services.registry.states)
        self.assertTrue(saved)
        services.registry.states.clear()
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        services.registry.states.extend(saved)
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        services.unregistered = True
        with self.assertRaises(ServerSnapshotError):
            case.capture(session)
        del services.unregistered
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        owner = services.messages.component
        services.messages.component = player.body_script.component
        with self.assertRaises(ServerSnapshotError):
            case.capture(session)
        services.messages.component = owner

    def test_live_linked_cache_and_current_numeric_backend_are_stored(self):
        case, session, player, vm, effect = self.scene(mover=True)
        state = effect.node_state(effect.graph.node(9))
        backend = vm.component.world.numeric_modifiers
        self.assertIsNotNone(backend)
        before = snapshot_digest(case.capture(session))
        backend.source_frame += 1
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        backend.source_frame -= 1
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        cache = state.linked_cache
        state.linked_cache = (*cache[:-1], cache[-1] + .125)
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)
        state.linked_cache = cache
        self.assertEqual(snapshot_digest(case.capture(session)), before)
