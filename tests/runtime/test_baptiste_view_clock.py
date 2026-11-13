"""Actual Baptiste commands discriminate target identity across owner/world epochs."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim

BAPTISTE = 0x02E0000000000221


class BaptisteViewClockTests(unittest.TestCase):
    def exercise(self, *, melee):
        expected = json.loads((Path(__file__).resolve().parents[2]
                               / 'fixtures/behavior/baptiste-view-clock-v1.json').read_text())['expected']
        run = Run(names=('Baptiste', 'TargetA', 'TargetB'))
        source, a, b = run.players
        stock_npcs = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(stock_npcs), 15)
        source.commands.last_frame = 9000
        run.match.switch_hero(source, heroes()[BAPTISTE])
        self.assertEqual(source.body_script.world_frame_anchor, (1000, 9000))
        run.match.collision_world = flat_world(0)
        near = (1000, 0, 1001.5) if melee else (997, 0, 1008)
        far = (1006, 0, 1001.5) if melee else (1003, 0, 1008)
        for player, point in ((source, (1000, 0, 1000)), (a, near), (b, far)):
            player.mover.position = list(point)
            player.mover.body = None
            player.body_script.create_arrived = True
        for offset in range(1, 42):
            swapped = offset > 33
            a.mover.position[:] = far if swapped else near
            b.mover.position[:] = near if swapped else far
            run.match.update((1000 + offset) * combat.TICK, 1000 + offset)
            if offset < 41:
                source.take_commands([Command(9000 + offset)])
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        pools = [fight.vitals_of(targets[player]) for player in (a, b)]
        yaw, pitch = aim(fight._eye(source), (near[0], 1.1, near[2]))
        submitted = []
        original = fight._submit_attack

        def submit(shooter, frame, payload):
            if shooter is source:
                submitted.append((frame, payload.view_tick))
            return original(shooter, frame, payload)

        with patch.object(fight, '_submit_attack', side_effect=submit):
            source.take_commands([Command(9041, yaw=yaw, pitch=pitch,
                                          buttons=0x800 if melee else 1, extra=128)])
            for tick in range(1042, 1045):
                run.match.update(tick * combat.TICK, tick)
        hits = source.client.messages(combat.HIT)
        self.observation = {
            'action': 'quick_melee' if melee else 'primary',
            'birth': [1000, 9000], 'submitted': submitted,
            'health_A_B': [pool.total for pool in pools], 'A': a.body, 'B': b.body,
            'hits': [{'entity': hit['+0x78']['+0x0']['+0x0'], 'owner_frame': hit['+0x84']}
                     for hit in hits],
            'stock_npcs_before': list(stock_npcs),
            'stock_npcs_after': [bot.entity for bot in run.match.bots],
        }
        self.assertEqual(submitted, [(9041, expected['view_world_tick'])])
        self.assertEqual([pool.total for pool in pools],
                         expected['melee_health_A_B' if melee else 'primary_health_A_B'])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], a.body)
        self.assertEqual(hits[0]['+0x84'], expected['owner_hit_frame'])
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), stock_npcs)

    def test_primary_targets_view_world_identity_and_preserves_owner_hit_frame(self):
        self.exercise(melee=False)

    def test_quick_melee_targets_view_world_identity_and_preserves_owner_hit_frame(self):
        self.exercise(melee=True)
