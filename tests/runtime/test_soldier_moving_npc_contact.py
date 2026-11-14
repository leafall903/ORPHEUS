"""Accepted Soldier commands use the routed peer's physical-time contact pose."""

import json
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, contacts, npc_console, npc_contacts, soldier_peer_history, sombra_replay
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from tests.runtime.test_game_combat import Run


class SoldierMovingNpcContactTests(unittest.TestCase):
    def test_unqualified_contact_context_keeps_current_feet_without_extrapolation(self):
        run = Run()
        player = run.players[0]
        npc_console.Console({}).press('hero', run.match, player, hero='Baptiste')
        peer = run.match.bots[-1]
        script, fight = player.body_script, run.match.combat
        values = peer.movement_data or mover_data(peer.body)
        run.match.tick = 100
        script.world_frame_anchor = (100, 100)
        peer.position = (4, 0, 2.5)
        peer.route = [peer.position, (8, 0, 2.5)]
        history = deque([(89, (0, 0, 2.5), 2, 1.5), (91, (2, 0, 2.5), 2, 1.5)])
        fight.history[peer.entity] = history
        fight.replacement_ticks.pop(peer.entity, None)
        command = Command(90, forward=127)
        self.assertEqual(soldier_peer_history.position(player, command, peer, None, values), (1, 0, 2.5))
        script.world_frame_anchor = (100, 9100)
        self.assertEqual(
            soldier_peer_history.position(player, Command(9090), peer, None, values), (1, 0, 2.5))
        script.world_frame_anchor = (100, 100)
        # Missing/out-of-range samples must not use Combat.seen's nearest-pose fallback.
        for frame in (88, 92, 100, 101):
            self.assertEqual(
                soldier_peer_history.position(player, Command(frame), peer, None, values), peer.position)
        cases = ((script, 'hero', 0x02E0000000000008), (script, 'retired', True),
                 (script, 'world_frame_anchor', None), (peer, 'route', []),
                 (script.component, 'entity', player.body + 1),
                 (script, 'frame_ms', 8), (player, 'quanta', 8000))
        for obj, name, changed in cases:
            with self.subTest(unqualified=name):
                original = getattr(obj, name)
                try:
                    setattr(obj, name, changed)
                    self.assertEqual(
                        soldier_peer_history.position(player, command, peer, None, values), peer.position)
                finally:
                    setattr(obj, name, original)
        self.assertEqual(
            soldier_peer_history.position(player, command, peer, object(), values), peer.position)
        fight.replacement_ticks[peer.entity] = 91
        self.assertEqual(soldier_peer_history.position(player, command, peer, None, values), peer.position)
        fight.history.pop(peer.entity)
        self.assertEqual(soldier_peer_history.position(player, command, peer, None, values), peer.position)
        self.assertEqual(list(history), [(89, (0, 0, 2.5), 2, 1.5), (91, (2, 0, 2.5), 2, 1.5)])
        self.assertEqual(peer.position, (4, 0, 2.5))

    def test_delayed_commands_sample_the_same_retained_peer_capsules(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/soldier-moving-npc-contact-v1.json'
        contract = json.loads(path.read_text())
        inputs, expected = contract['input'], contract['expected']
        runs = []
        for packet in inputs['packet_sizes']:
            run = Run()
            run.match.bots.clear()
            run.match._pending_bot_scripts.clear()
            run.match.collision_world = flat_world(0)
            player = run.players[0]
            player.mover.position[:] = inputs['owner_position']
            player.mover.body = None
            player.body_script.create_arrived = True
            for _ in range(inputs['prewarm_frames']):
                run.step({})
            npc_console.Console({}).press('hero', run.match, player, hero=inputs['target_hero'])
            peer = run.match.bots[-1]
            peer.position = tuple(inputs['peer_route'][0])
            peer.spawner['position'] = peer.position
            peer.route = [tuple(point) for point in inputs['peer_route']]
            peer.index, peer.wait = 0, 0
            pending, peer_poses, steps = [], [], []
            start = run.frame
            original_step, original_contact = npc_contacts.step_pose, contacts.make_contact
            geometry = []

            def contact(shape, category, geometry=geometry, original_contact=original_contact):
                if isinstance(shape, contacts.PeerShape):
                    capsule = shape.geometry
                    geometry.append((capsule.a, capsule.b, capsule.radius, shape.peer_category, category))
                return original_contact(shape, category)

            def step(owner, command, seconds, tick, geometry=geometry, original_step=original_step,
                     steps=steps, **kwargs):
                geometry.clear()
                world_tick = sombra_replay.world_frame(owner.body_script, command.frame)
                self.assertEqual(world_tick, command.frame)
                self.assertEqual(seconds, expected['step_seconds'])
                result = original_step(owner, command, seconds, tick, **kwargs)
                steps.append((command.frame, tuple(geometry), owner.mover.snapshot(command.frame)))
                return result

            with (patch.object(contacts, 'make_contact', contact),
                  patch.object(npc_contacts, 'step_pose', step)):
                for offset in range(1, inputs['sample_frames'] + 1):
                    run.frame = start + offset
                    run.match.update(run.frame * combat.TICK, run.frame)
                    peer_poses.append(tuple(peer.position))
                    pending.append(Command(run.frame, forward=inputs['forward']))
                    if offset % packet == 0:
                        player.take_commands(pending)
                        pending = []
            self.assertFalse(pending)
            self.assertEqual(len(steps), inputs['sample_frames'])
            self.assertEqual(len(player.mover._engine_body().contacts), expected['remaining_contacts'])
            runs.append((peer_poses, steps))
        self.assertEqual(runs[0][0], runs[1][0])
        for paced, delayed in zip(runs[0][1], runs[1][1], strict=True):
            with self.subTest(command_frame=paced[0]):
                self.assertEqual(paced, delayed)
        final = runs[0][1][-1][2]
        self.assertEqual(list(final.position), expected['final_position'])
        # The first contact produces a lateral clip; the preceding step remains straight.
        at = expected['first_contact_offset'] - 1
        self.assertEqual(runs[0][1][at - 1][2].velocity[0], 0)
        self.assertLess(runs[0][1][at][2].velocity[0], 0)
