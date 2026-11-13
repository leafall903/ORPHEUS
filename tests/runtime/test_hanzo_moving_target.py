"""An identical physical timeline must not lose direct hits under source batching."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hanzo_fixture
import test_game_hanzo_sonic as sonic_fixture

from ow174.game import bodytrace, combat, movelog
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2] /
                   'fixtures/behavior/hanzo-moving-target-v1.json').read_text(encoding='utf-8'))


class HanzoMovingTargetTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))

    def replay(self, size, separate, kind):
        run, owner, victim = hanzo_fixture.HanzoCombatTests().arena(CASE['input']['distance'])
        if separate:
            origin = CASE['input']['separate_owner_epoch']
            run.match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=origin)
            owner.commands.reset(last_frame=origin)
            for offset in range(1, 21):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(origin + offset)])
                victim.take_commands([Command(run.frame)])
        fight = run.match.combat
        if kind == 'sonic':
            fight.configure_sonic(sonic_fixture.profile())
        start, source, pending, launches = run.frame, owner.commands.last_frame, [], []
        submit = fight._submit_attack

        def capture(shooter, frame, payload):
            if isinstance(payload, combat.Rocket):
                launches.append((payload.source_graph, payload.definition, frame))
            return submit(shooter, frame, payload)

        with patch.object(fight, '_submit_attack', side_effect=capture):
            for offset in range(1, CASE['input']['frames'] + 1):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick)
                victim.take_commands([Command(tick, right=CASE['input']['target_right']
                    if offset >= CASE['input']['target_right_from'] else 0)])
                buttons = (8 if kind == 'sonic' and offset == 1 else
                           1 if CASE['input']['draw_first'] <= offset <= CASE['input']['draw_last'] else 0)
                pending.append(Command(source + offset, buttons=buttons))
                if offset % size == 0:
                    owner.take_commands(pending)
                    pending = []
        target = next(t for t in fight._targets() if t.owner is victim)
        return {'launches': launches, 'health': fight.vitals_of(target).total,
                'hits': owner.client.messages(combat.HIT), 'live': len(fight.rockets),
                'target_position': tuple(victim.mover.position),
                'sonars': [(b.attached, sorted(b.statuses)) for b in fight.sonars]}, victim.body

    def test_packet_delay_preserves_direct_hit_and_sonic_target(self):
        for kind in ('bow', 'sonic'):
            for separate in (False, True):
                with self.subTest(kind=kind, separate_epoch=separate):
                    (direct, target), (delayed, _) = [self.replay(size, separate, kind)
                                                    for size in CASE['input']['packets']]
                    self.assertEqual(direct, delayed)
                    self.assertEqual(direct['health'], 200 - CASE['expected']['damage'])
                    self.assertEqual(len(direct['hits']), CASE['expected']['hit_notices'])
                    self.assertEqual(direct['live'], CASE['expected']['live'])
                    self.assertEqual(direct['launches'][0][1], int(CASE['expected']['definitions'][kind], 16))
                    self.assertEqual(direct['sonars'], [(target, [target])] if kind == 'sonic' else [])
