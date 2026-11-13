"""Actual Genji melee view-history identity; no native screen-side claim."""
from unittest.mock import patch
import unittest

from test_game_combat import Run, aim
from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.collision import flat_world
from ow174.game.content import heroes

GENJI = 0x02E0000000000029


def swapped_encounter(birth):
    run = Run(names=('Genji', 'TargetA', 'TargetB'))
    player, a, b = run.players
    npcs = tuple(bot.entity for bot in run.match.bots)
    assert len(npcs) == 15
    player.commands.last_frame = birth
    run.match.switch_hero(player, heroes()[GENJI])
    assert player.body_script.genji_birth == (1000, birth)
    run.match.collision_world = flat_world(0)
    near, far = (1000., 0., 1001.8), (1003., 0., 1001.8)
    for actor, position in ((player, (1000., 0., 1000.)), (a, near), (b, far)):
        actor.mover.position = list(position)
        actor.mover.body = None
        actor.body_script.create_arrived = True
    start = run.match.tick
    for offset in range(1, 42):
        swapped = offset > 33
        a.mover.position[:] = far if swapped else near
        b.mover.position[:] = near if swapped else far
        tick = start + offset
        run.match.update(tick * .016, tick)
        if offset < 41:
            player.take_commands([Command(birth + offset)])
    fight = run.match.combat
    targets = {target.owner: target for target in fight._targets()}
    pools = [fight.vitals_of(targets[actor]) for actor in (a, b)]
    yaw, pitch = aim(fight._eye(player), (near[0], 1.1, near[2]))
    submitted = []
    submit = fight._submit_attack

    def observe(shooter, frame, payload):
        if shooter is player:
            assert isinstance(payload, combat.MeleeContact)
            submitted.append((frame, payload.view_tick))
        return submit(shooter, frame, payload)

    frame = birth + 41
    with patch.object(fight, '_submit_attack', observe):
        player.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=0x800, extra=128),
                              Command(frame + 1, yaw=yaw, pitch=pitch, extra=128)])
        for offset in range(42, 51):
            tick = start + offset
            run.match.update(tick * .016, tick)
    hits = player.client.messages(combat.HIT)
    assert tuple(bot.entity for bot in run.match.bots) == npcs
    return {'owner_birth': birth, 'world_birth': 1000, 'submitted': submitted,
            'A': a.body, 'B': b.body, 'health': [pool.total for pool in pools],
            'hits': [{'target': h['+0x78']['+0x0']['+0x0'], 'owner_frame': h['+0x84'],
                      'damage': h['+0x78']['+0x4']} for h in hits], 'NPCs_retained': len(npcs)}


class GenjiMeleeViewClockTests(unittest.TestCase):
    def test_swapped_identity_uses_world_view_and_preserves_owner_hit_frame(self):
        for birth in (1000, 9000):
            with self.subTest(owner_birth=birth):
                result = swapped_encounter(birth)
                self.assertEqual(result['submitted'], [(birth + 41, 1033.0)])
                self.assertEqual(result['health'], [170, 200])
                self.assertEqual(result['hits'], [{'target': result['A'], 'owner_frame': birth + 41, 'damage': 30}])
                self.assertEqual(result['NPCs_retained'], 15)

    def test_fractional_view_mapping_retains_subtick_time(self):
        from types import SimpleNamespace
        from ow174.game import genji

        script = SimpleNamespace(genji_birth=(1000, 9000), frame_ms=15)
        command = Command(9041, extra=8)
        self.assertEqual(genji.view_world_tick(script, command, 15000), 1037.9375)
