"""Actual controller effect frames use the existing Match transport."""
import unittest

from test_game_combat import Run
from ow174.game.script import decode, graph


class ControllerEffectMatchTests(unittest.TestCase):
    def setup_effect(self, *, mover=False):
        if not graph.GRAPHS_PATH.is_file() or graph.graph(0x1177) is None:
            self.skipTest('requires original1177 graph')
        run = Run()
        player, = run.players
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
        return run, player, vm, effect

    @staticmethod
    def frames(player):
        return [item for item in player.client.entities if item.stream is player.script and item.resend]

    def test_actual_effect_flag_and_removal_refresh_only_changed_full_frame(self):
        run, player, vm, effect = self.setup_effect()
        run.match.send_controller(player)
        first = self.frames(player)[-1]
        active = decode.read_chunk(first.chunk.getvalue(), first.chunk.count)
        self.assertIn(effect.id, active.instances)
        self.assertIs(active.entity_vars[14695][0], True)
        before = len(self.frames(player))
        for _ in range(4):
            run.step({})
        self.assertEqual(len(self.frames(player)), before)
        vm.component.destroy(effect)
        for _ in range(4):
            run.step({})
        self.assertIsNone(player.controller_effect_key)
        sent = self.frames(player)
        self.assertEqual(len(sent), before + 1)
        removed = decode.read_chunk(sent[-1].chunk.getvalue(), sent[-1].chunk.count)
        self.assertNotIn(effect.id, removed.instances)
        self.assertNotIn(14695, removed.entity_vars)
        self.assertGreater(removed.cmfd, active.cmfd)

    def test_actual_controller_angles_clamp_body_commands_and_release(self):
        run, player, vm, effect = self.setup_effect(mover=True)
        vm.set_linked_target_sample(player.entity, .5, None, None)
        run.step({player: (10000, -8000, 0)})
        self.assertEqual((player.mover.yaw, player.mover.pitch), (2608, 16202))
        self.assertEqual((player.mover.last_command.yaw, player.mover.last_command.pitch), (2608, 16202))
        vm.component.destroy(effect)
        run.step({player: (10000, -8000, 0)})
        self.assertEqual((player.mover.yaw, player.mover.pitch), (10000, -8000))
