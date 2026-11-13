"""One identity swap through actual hammer input; retain all original NPCs."""
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes


def identity_swap():
    run = Run(KANEZAKA_DM, ('Reinhardt', 'A', 'B'))
    player, target_a, target_b = run.players
    npc_count = len(run.match.bots)
    player.commands.last_frame = 9000
    run.match.switch_hero(player, heroes()[combat.REINHARDT])
    fight = run.match.combat
    epoch = fight.knockbacks.body_epochs[player.body][1]
    assert epoch == (1000, 9000)
    origin = tuple(player.mover.position)
    near = (origin[0], origin[1], origin[2] + 3.)
    far = (origin[0] + 40., origin[1], origin[2])
    queries = []
    seen = fight.seen
    def observed(target, tick):
        shown = seen(target, tick)
        if target.owner in (target_a, target_b):
            queries.append(dict(entity=target.entity, view_world_tick=tick,
                                current=list(target.feet), shown=list(shown.feet) if shown else None))
        return shown
    with patch.object(fight, 'seen', observed):
        for offset in range(1, 59):
            swapped = offset > 54
            target_a.mover.position[:] = far if swapped else near
            target_b.mover.position[:] = near if swapped else far
            tick = 1000 + offset
            run.match.update(tick * .016, tick)
            player.take_commands([Command(9000 + offset, buttons=1 if 31 <= offset <= 70 else 0,
                                          extra=112)])
    hp = {p: fight.vitals[p.body].total for p in (target_a, target_b)}
    hits = player.client.messages(combat.HIT)
    return dict(owner_birth=9000, world_birth=1000, A=target_a.body, B=target_b.body,
                health_a=hp[target_a], health_b=hp[target_b], queries=queries,
                hits=[dict(entity=h['+0x78']['+0x0']['+0x0'], owner_frame=h['+0x84']) for h in hits],
                npc_count_before=npc_count, npc_count_after=len(run.match.bots))


class HammerViewClockTests(unittest.TestCase):
    def test_history_uses_world_view_but_hit_keeps_owner_frame(self):
        result = identity_swap()
        self.assertEqual(result['npc_count_before'], 15)
        self.assertEqual(result['npc_count_after'], 15)
        self.assertEqual((result['health_a'], result['health_b']), (115., 200.))
        self.assertEqual(result['hits'], [dict(entity=result['A'], owner_frame=9057)])
        self.assertEqual({row['view_world_tick'] for row in result['queries']}, {1050., 1051.})


if __name__ == '__main__':
    unittest.main()
