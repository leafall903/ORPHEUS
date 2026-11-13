"""Real Bastion commands distinguish target identities across clock epochs."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import BASTION


class BastionViewClockTests(unittest.TestCase):
    def test_actual_weapons_keep_viewed_identity_and_owner_hit_frame(self):
        data = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                           / 'bastion-view-clock-v1.json').read_text(encoding='utf-8'))
        for weapon in data['weapons']:
            with self.subTest(mode=weapon['mode']):
                run = Run(names=('Bastion', 'TargetA', 'TargetB'))
                player, a, b = run.players
                match = run.match
                original_npcs = tuple(bot.entity for bot in match.bots)
                self.assertEqual(len(original_npcs), 15)
                owner_birth = data['input']['owner_birth']
                player.commands.last_frame = owner_birth
                match.switch_hero(player, heroes()[BASTION])
                self.assertEqual(player.body_script.world_frame_anchor, (1000, owner_birth))
                match.collision_world = flat_world(0)
                pos_a = weapon.get('target_a_position', data['input']['target_a_position'])
                pos_b = weapon.get('target_b_position', data['input']['target_b_position'])
                for actor, position in ((player, (1000, 0, 1000)), (a, pos_a), (b, pos_b)):
                    actor.mover.position = list(position)
                    actor.mover.body = None
                    actor.body_script.create_arrived = True

                def advance(buttons=0, *, run=run, match=match, player=player, owner_birth=owner_birth):
                    run.frame += 1
                    match.update(run.frame * .016, run.frame)
                    player.take_commands([Command(owner_birth + run.frame - 1000, buttons=buttons)])

                if weapon['mode'] == 'Sentry':
                    for _ in range(40):
                        advance()
                    advance(8)
                    for _ in range(100):
                        advance()
                start = run.frame
                for offset in range(1, 42):
                    swapped = offset > 33
                    a.mover.position[:] = pos_b if swapped else pos_a
                    b.mover.position[:] = pos_a if swapped else pos_b
                    run.frame = start + offset
                    match.update(run.frame * .016, run.frame)
                    if offset < 41:
                        player.take_commands([Command(owner_birth + run.frame - 1000)])
                fight = match.combat
                targets = {target.owner: target for target in fight._targets()}
                pools = [fight.vitals_of(targets[actor]) for actor in (a, b)]
                yaw, pitch = aim(fight._eye(player), (pos_a[0], 1.1, pos_a[2]))
                original = fight._submit_attack
                submitted = []

                def submit(shooter, frame, payload, *, source=player, rows=submitted, forward=original):
                    if shooter is source:
                        rows.append((frame, payload.view_tick))
                    return forward(shooter, frame, payload)

                frame = owner_birth + run.frame - 1000
                view_delay = weapon.get('view_delay_ms', 128)
                with patch.object(fight, '_submit_attack', submit):
                    player.take_commands([
                        Command(frame, yaw=yaw, pitch=pitch, buttons=weapon.get('buttons', 1),
                                extra=view_delay),
                        Command(frame + 1, yaw=yaw, pitch=pitch, extra=view_delay),
                    ])
                    for offset in range(42, 51):
                        run.frame = start + offset
                        match.update(run.frame * .016, run.frame)
                        if weapon['mode'] == 'Melee' and offset > 42:
                            player.take_commands([Command(owner_birth + run.frame - 1000,
                                                          yaw=yaw, pitch=pitch, extra=view_delay)])
                hits = player.client.messages(combat.HIT)
                diagnostic = {'submitted': submitted, 'health': [pool.total for pool in pools],
                              'targets': [a.body, b.body],
                              'hits': [(hit['+0x78']['+0x0']['+0x0'], hit['+0x84']) for hit in hits]}
                self.assertEqual([pool.total for pool in pools], [200 - weapon['damage'], 200],
                                 json.dumps(diagnostic))
                self.assertEqual(submitted, [(frame, weapon['view_world_tick'])])
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], a.body)
                self.assertEqual(hits[0]['+0x84'], weapon['owner_hit_frame'])
                self.assertEqual(tuple(bot.entity for bot in match.bots), original_npcs)
