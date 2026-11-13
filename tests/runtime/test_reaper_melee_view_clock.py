"""Actual Reaper melee damages and pushes the viewed target identity once."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, reaper
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim


class ReaperMeleeViewClockTests(unittest.TestCase):
    def test_viewed_identity_damage_force_and_owner_hit(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/reaper-melee-view-clock-v1.json'
        case = json.loads(path.read_text(encoding='utf-8'))
        inputs, expected = case['input'], case['expected']
        for birth in inputs['owner_births']:
            with self.subTest(owner_birth=birth):
                run = Run(names=('Reaper', 'TargetA', 'TargetB'))
                source, a, b = run.players
                source.commands.last_frame = birth
                run.match.switch_hero(source, heroes()[reaper.HERO])
                run.match.collision_world = flat_world(0)
                near, far = inputs['near'], inputs['far']
                for owner, position in ((source, inputs['source']), (a, near), (b, far)):
                    owner.mover.position = list(position)
                    owner.mover.body = None
                    owner.body_script.create_arrived = True
                fight = run.match.combat
                npc_ids = tuple(bot.entity for bot in run.match.bots)
                self.assertEqual(len(npc_ids), 15)
                heading = aim(fight._eye(source), (near[0], near[1] + 1, near[2]))
                for offset in range(1, inputs['action_offset'] + 1):
                    a.mover.position[:] = far if offset > inputs['swap_after'] else near
                    b.mover.position[:] = near if offset > inputs['swap_after'] else far
                    tick = inputs['world_birth'] + offset
                    run.match.update(tick * combat.TICK, tick)
                    if offset < inputs['action_offset']:
                        source.take_commands([Command(birth + offset)])
                targets = {target.owner: target for target in fight._targets()}
                pools = [fight.vitals_of(targets[owner]) for owner in (a, b)]
                initial = [pool.total for pool in pools]
                frame = birth + inputs['action_offset']
                with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as applied:
                    source.take_commands([
                        Command(frame, yaw=heading[0], pitch=heading[1], buttons=0x800,
                                extra=inputs['view_delay_ms']),
                        Command(frame + 1, yaw=heading[0], pitch=heading[1], extra=inputs['view_delay_ms']),
                    ])
                    for offset in range(inputs['action_offset'] + 1, inputs['action_offset'] + 10):
                        tick = inputs['world_birth'] + offset
                        run.match.update(tick * combat.TICK, tick)
                self.assertEqual([old - pool.total for old, pool in zip(initial, pools, strict=True)],
                                 expected['damage_by_identity'])
                self.assertEqual(applied.call_count, expected['force_calls'])
                call = applied.call_args
                self.assertEqual(call.args[0].entity, a.body)
                self.assertEqual(list(call.args[1]), expected['impulse'])
                self.assertEqual(call.kwargs, {'impact_tick': expected['impact_world_tick'],
                                               'source_entity': source.body})
                hits = source.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], a.body)
                self.assertEqual(hits[0]['+0x84'], frame)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), npc_ids)
