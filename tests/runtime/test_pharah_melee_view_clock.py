"""A/B identity exchange discriminates Pharah melee's owner/view/world clocks."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import combat, pharah
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim


class PharahMeleeViewClockTests(unittest.TestCase):
    def test_fractional_view_delay_uses_physical_world_tick_duration(self):
        script = SimpleNamespace(world_frame_anchor=(1000, 9000), frame_ms=15)
        command = Command(9041, extra=8)
        self.assertEqual(pharah.view_world_tick(script, command, 15000), 1037.9375)

    def test_swapped_target_identity_keeps_owner_hit_frame_and_original_npcs(self):
        self.observations = []
        for birth in (1000, 9000):
            with self.subTest(owner_birth=birth):
                run = Run(names=('Pharah', 'TargetA', 'TargetB'))
                player, a, b = run.players
                original_npcs = tuple(bot.entity for bot in run.match.bots)
                self.assertEqual(len(original_npcs), 15)
                player.commands.last_frame = birth
                self.assertTrue(run.match.switch_hero(player, heroes()[pharah.HERO]))
                self.assertEqual(player.body_script.world_frame_anchor, (1000, birth))
                run.match.collision_world = flat_world(0)
                pos_a, pos_b = (1000, 0, 1001.5), (1003, 0, 1001.5)
                for owner, position in ((player, (1000, 0, 1000)), (a, pos_a), (b, pos_b)):
                    owner.mover.position = list(position)
                    owner.mover.body = None
                    owner.body_script.create_arrived = True
                for offset in range(1, 42):
                    a.mover.position[:] = pos_b if offset > 33 else pos_a
                    b.mover.position[:] = pos_a if offset > 33 else pos_b
                    tick = 1000 + offset
                    run.match.update(tick * .016, tick)
                    if offset < 41:
                        player.take_commands([Command(birth + offset)])
                fight = run.match.combat
                targets = {target.owner: target for target in fight._targets()}
                pools = tuple(fight.vitals_of(targets[owner]) for owner in (a, b))
                yaw, pitch = aim(fight._eye(player), (pos_a[0], 1.1, pos_a[2]))
                submitted = []
                original = fight._submit_attack

                def submit(shooter, at, payload, player=player, submitted=submitted, original=original):
                    if shooter is player and isinstance(payload, combat.MeleeContact):
                        submitted.append((at, payload.view_tick))
                    return original(shooter, at, payload)

                frame = birth + 41
                with patch.object(fight, '_submit_attack', submit):
                    player.take_commands([
                        Command(frame, yaw=yaw, pitch=pitch, buttons=0x800, extra=128),
                        Command(frame + 1, yaw=yaw, pitch=pitch, extra=128),
                    ])
                hits = player.client.messages(combat.HIT)
                self.observations.append({
                    'ownerBirth': birth, 'worldBirth': 1000, 'submitted': submitted,
                    'A': a.body, 'B': b.body, 'health': [pool.total for pool in pools],
                    'hits': [{'target': h['+0x78']['+0x0']['+0x0'], 'ownerFrame': h['+0x84']}
                             for h in hits], 'originalNpcCount': len(original_npcs),
                    'retainedNpcCount': len(run.match.bots),
                })
                self.assertEqual(submitted, [(frame, 1033.0)])
                self.assertEqual(tuple(pool.total for pool in pools), (170, 200))
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], a.body)
                self.assertEqual(hits[0]['+0x84'], frame)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)


if __name__ == '__main__':
    unittest.main()
