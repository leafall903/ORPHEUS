"""A delayed32-input packet must keep its first fire/jump edge and pose labels."""

import json
import os
import unittest
from pathlib import Path

from ow174.game import correction
from ow174.game.collision import flat_world
from ow174.game.commands import FIRE, JUMP, Command
from tests.runtime.test_game_combat import Run, aim

FIXTURE_ROOT = Path(os.environ.get(
    'ORPHEUS_BEHAVIOR_FIXTURES', Path(__file__).resolve().parents[2] / 'fixtures/behavior',
))


class SoldierDelayedPacketPoseTests(unittest.TestCase):
    def episode(self, delay):
        run = Run(names=('Soldier', 'Enemy'))
        soldier, enemy = run.players
        run.match.collision_world = flat_world(0)
        npcs = tuple(bot.entity for bot in run.match.bots)
        for player, position in ((soldier, (1000, 0, 1000)), (enemy, (1000, 0, 1010))):
            player.mover.position[:] = position
            player.mover.body = None
            player.body_script.create_arrived = True
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        pool = fight.vitals_of(target)
        health = pool.total
        yaw, pitch = aim(fight._eye(soldier), (1000, 1.1, 1010))
        first = run.frame + 1
        commands = [Command(first + offset, yaw=yaw, pitch=pitch, forward=127,
                            buttons=FIRE | JUMP if offset == 0 else 0) for offset in range(32)]
        for _ in range(32 + delay):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
        script_before = soldier.body_script.frame
        soldier.take_commands(commands)
        last = commands[-1].frame
        jump_height = soldier.mover.position[1]
        pose = next(p for p in soldier.mover.history if p.frame == last)
        update = correction.own_record(soldier, run.frame)
        self.assertIsNotNone(update)
        self.assertEqual(update.entity, soldier.body)
        self.assertEqual(update.movement.frame, last)
        self.assertEqual(update.movement.position, pose.position)
        self.assertEqual(update.movement.velocity, pose.velocity)
        self.assertEqual(soldier.mover.last_command.frame, last)
        soldier.client.queue_entities([update])
        self.assertEqual(soldier.mover.corrected, last)
        accepted_position = tuple(soldier.mover.position)
        for offset in range(1, 41):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            soldier.take_commands([Command(last + offset)])
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)
        self.assertEqual(len(npcs), 15)
        return {
            'damage': health - pool.total,
            'jump_height_at_last_pose': jump_height,
            'accepted_position': accepted_position,
            'end_position': tuple(soldier.mover.position),
            'end_velocity': tuple(soldier.mover.velocity),
            'first_owner_frame': first,
            'last_owner_frame': last,
            'delivery_world_tick': last + delay,
            'script_frame_before_admission': script_before,
        }

    def test_delayed_packet_preserves_first_edge_and_same_correction_pose(self):
        contract = json.loads((FIXTURE_ROOT / 'soldier-delayed-packet-pose-v1.json').read_text('utf-8'))
        immediate = self.episode(contract['input']['delivery_delays'][0])
        delayed = self.episode(contract['input']['delivery_delays'][1])
        self.assertEqual(immediate['damage'], contract['expected']['damage'])
        self.assertEqual(delayed['damage'], immediate['damage'], (immediate, delayed))
        self.assertGreater(delayed['jump_height_at_last_pose'], .2, delayed)
        for name in ('accepted_position', 'end_position', 'end_velocity'):
            self.assertEqual(delayed[name], immediate[name], (name, immediate, delayed))
