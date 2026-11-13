"""Synthetic contract on actual Player pose dispatch, without visual claims."""
import json
import unittest
from pathlib import Path

from ow174.game.content import SOLDIER, heroes
from ow174.game import combat, npc
from ow174.game.mover import mover_data
from tests.runtime import test_mccree_roll as roll_cases
from tests.runtime.test_game_combat import aim


class RollDispatchTests(unittest.TestCase):
    def test_practice_roll_stops_before_manual_enemy_and_primary_registers(self):
        helper = roll_cases.McCreeRollTests()
        run, player = helper.arena()
        definition = npc.Definition(npc.TROOPER_HERO, 0x0400000000001D40, 'Contact target', 200)
        bot = npc.spawn(run.match, definition, (0, 0, 4), team=1 - player.team)
        radii = player.mover.data.stand_radius + mover_data(bot.body).stand_radius
        helper.step(run, player, 8)
        for _ in range(40):
            helper.step(run, player)
        self.assertAlmostEqual(player.mover.position[2], 4 - radii, delta=0.02)
        self.assertIsNotNone(player.impulse_correction)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        before = fight.vitals_of(target).total
        yaw, pitch = aim(fight._eye(player), (0, 1, 4))
        run.step({player: (yaw, pitch, 1)})
        for _ in range(35):
            run.step({player: (yaw, pitch, 0)})
        self.assertEqual(before - fight.vitals_of(target).total, 70)
        self.assertEqual([hit['+0x78']['+0x4'] for hit in player.client.messages(combat.HIT)], [70])

    def test_next_pose_consumes_only_the_cassidy_queue_once(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/mccree-roll-next-frame-v1.json'
        contract = json.loads(path.read_text())
        expected = contract['expected']
        helper = roll_cases.McCreeRollTests()
        run, player = helper.arena()
        helper.step(run, player, contract['input']['buttons'])
        self.assertEqual(player.mover.position[2], expected['cast_frame_displacement'])
        self.assertEqual(len(player.body_script.world.velocity_requests), 1)
        helper.step(run, player, contract['input']['next_buttons'])
        self.assertAlmostEqual(player.mover.velocity[2], expected['next_frame_forward_velocity'],
                               delta=expected['velocity_tolerance'])
        self.assertFalse(player.body_script.world.velocity_requests)
        helper.step(run, player)
        self.assertAlmostEqual(player.mover.velocity[2], expected['next_frame_forward_velocity'],
                               delta=expected['velocity_tolerance'])
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        other_world = player.body_script.world
        request = (run.frame + 1, 'delta', (0.0, 0.0, 15.0))
        other_world.velocity_requests.append(request)
        helper.step(run, player)
        self.assertEqual(other_world.velocity_requests, [request])
        self.assertEqual(player.mover.velocity[2], 0)
