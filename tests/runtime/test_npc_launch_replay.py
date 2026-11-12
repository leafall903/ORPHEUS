"""Accepted authored NPC launches keep their sole player pose consumer."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bastion_combat as bastion
from test_game_combat import aim
from ow174.game import combat, npc, npc_control
from ow174.game.commands import Command
from ow174.game.script import expr, runtime


class NpcLaunchReplayTests(unittest.TestCase):
    def arena(self, key='5', *, sentry=False):
        case = bastion.BastionCombatTests()
        case.fixture(enemy_position=(10, 1, 0))
        if sentry:
            case.configure()
        catalog = npc.load_catalog(Path(os.environ['ORPHEUS_NPC_DATA_DIR']) / 'pve_npcs_174.json')
        bot = npc.spawn(case.run.match, catalog[key], (0, 1, 1 if key == '1' else 2),
                        team=1, yaw=-32768)
        case.command_aim = aim(case.fight._eye(case.player), (0, bot.position[1] + 1, bot.position[2]))
        case.step(0, 8)
        return case, bot

    def advance(self, case, gap, frames=25):
        if gap:
            case.run.frame += frames
            case.run.match.update(case.run.frame * combat.TICK, case.run.frame, frames=False)
        else:
            case.step(0, frames)

    def accepted(self, case, bot):
        before = case.health(case.player).total
        self.assertEqual(npc_control.start(case.run.match, case.player, melee=True), bot.entity)
        for _ in range(65):
            case.step()
            if case.health(case.player).total < before:
                break
        self.assertLess(case.health(case.player).total, before)
        npc_control.stop(case.run.match)
        return before - case.health(case.player).total

    def test_actual_manual_heavy_launch_paced_and_gap_match(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                samples = []
                for gap in (False, True):
                    case, bot = self.arena(sentry=sentry)
                    self.assertGreater(self.accepted(case, bot), 0)
                    self.assertEqual(case.fight.knockbacks.launch_sources[case.player.body], bot.entity)
                    before = tuple(case.player.mover.position)
                    self.advance(case, gap)
                    self.assertLess(case.player.mover.position[2], before[2] - 1)
                    samples.append((tuple(case.player.mover.position), case.player.mover.velocity))
                    if gap:
                        pose = tuple(case.player.mover.position)
                        frame = case.player.body_script.frame
                        with patch.object(case.fight, '_shoot', wraps=case.fight._shoot) as shots:
                            case.player.take_commands([Command(frame, buttons=1)])
                        self.assertEqual(shots.call_count, 0)
                        self.assertEqual(tuple(case.player.mover.position), pose)
                self.assertEqual(samples[0], samples[1])

    def test_actual_trooper_without_upward_force_keeps_default(self):
        case, bot = self.arena('1')
        self.assertEqual(self.accepted(case, bot), 25)
        self.assertNotIn(case.player.body, case.fight.knockbacks.launches)
        before = tuple(case.player.mover.position)
        self.advance(case, True)
        self.assertEqual(tuple(case.player.mover.position), before)

    def test_actual_graph_generic_melee_consumer_replays_accepted_launch(self):
        samples = []
        for gap in (False, True):
            case, bot = self.arena()
            instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x140E)
            state = instance.node_state(instance.graph.node(5))
            tracker = instance.node_state(instance.graph.node(6))
            tracker.targets = {case.player.body}
            self.assertTrue(case.fight._npc_melee(state, health=True))
            self.assertEqual(case.fight.knockbacks.launch_sources[case.player.body], bot.entity)
            self.advance(case, gap)
            samples.append(tuple(case.player.mover.position))
        self.assertEqual(samples[0], samples[1])

    def test_original_heavy_node35_launch_runs_vm_and_replays_gap(self):
        samples = []
        for gap in (False, True):
            case, bot = self.arena()
            component = bot.body_script.component
            instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
            instance.write((expr.INSTANCE, 563), (expr.Entity(case.player.body),))
            state = instance.node_state(instance.graph.node(35))
            before = case.health(case.player).total
            state.request(runtime.BEGIN)
            component.run_now()
            self.assertLess(case.health(case.player).total, before)
            self.assertGreater(case.player.mover.velocity[1], 0)
            self.assertEqual(case.fight.knockbacks.launch_sources[case.player.body], bot.entity)
            self.advance(case, gap)
            samples.append((tuple(case.player.mover.position), case.player.mover.velocity))
        self.assertEqual(samples[0], samples[1])
