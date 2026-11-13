"""Fire Strike clock through actual encrypted input; native acceptance remains pending."""
import dataclasses
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server_journal as journal
from ow174.game import combat
from ow174.game.content import Spawn


def run(batch):
    fixture = journal.ServerJournalTests()
    session, sent = fixture.make()
    scene = dataclasses.replace(journal.MAP, spawns=(Spawn(0, (0., 0., 0.), 0.),
                                                    Spawn(1, (0., 0., 20.), 180.)))
    peers, players = fixture.connected(session, sent, first_hero=0x02E0000000000007, scene=scene)
    owner, enemy = players
    peer = peers[0]
    fight = owner.match.combat
    now = 100.944
    for _ in range(30):
        now += .016
        peer.commands(owner.body_script.frame + 1, 1, 0, now=now)
        session.tick(now)
        for link in peers:
            link.drain()
            link.send(now=now)
    first = owner.body_script.frame + 1
    base_tick = session.server.tick
    launches, hits, retained = [], [], []
    commit, damage = combat.Combat._commit_attack, combat.Combat._damage

    def observe_commit(self, shooter, frame, payload, boost):
        if shooter is owner and isinstance(payload, combat.Rocket):
            launches.append({"command_frame": frame, "world_tick": self.tick,
                             "last_tick": payload.last_tick, "launch_tick": payload.launch_tick,
                             "started_tick": payload.started_tick, "position": payload.position})
            retained.append(payload)
        return commit(self, shooter, frame, payload, boost)

    def observe_damage(self, shooter, target, amount, *args, **kwargs):
        before = self.vitals_of(target).total
        result = damage(self, shooter, target, amount, *args, **kwargs)
        if shooter is owner:
            hits.append({"world_tick": self.tick, "contact_world_tick": kwargs.get("world_tick"),
                         "target": target.entity, "before": before,
                         "after": self.vitals_of(target).total})
        return result

    result = {"batch": batch, "base_world_tick": base_tick, "first_command": first,
              "launches": launches, "hits": hits}
    with patch.object(combat.Combat, "_commit_attack", observe_commit), \
            patch.object(combat.Combat, "_damage", observe_damage):
        for offset in range(1, 401):
            now += .016
            if offset <= 96 and offset % batch == 0:
                start = first + offset - batch
                event = first + 3
                if start <= event < start + batch:
                    if start < event:
                        peer.commands(start, event - start, 0, now=now)
                    peer.commands(event, 1, 16, now=now)
                    if event + 1 < start + batch:
                        peer.commands(event + 1, start + batch - event - 1, 0, now=now)
                else:
                    peer.commands(start, batch, 0, now=now)
            session.tick(now)
            for link in peers:
                link.drain()
                link.send(now=now)
            if offset == 96:
                rocket, = retained
                result["query"] = {"world_tick": session.server.tick, "position": rocket.position,
                                   "flown": rocket.flown, "left": rocket.left,
                                   "last_tick": rocket.last_tick, "started_tick": rocket.started_tick,
                                   "target_hp": fight.vitals[enemy.body].total}
            if retained and retained[0] not in fight.rockets and "retire_world_tick" not in result:
                result["retire_world_tick"] = session.server.tick
    return result



class FireStrikeClockTests(unittest.TestCase):
    def test_packet_batching_preserves_flight_damage_and_retirement_clock(self):
        contract = json.loads((Path(__file__).resolve().parents[2]
                               / 'fixtures/behavior/reinhardt-fire-strike-clock-v1.json').read_text())
        expected = contract['expected']
        for batch in contract['input']['batch_sizes']:
            with self.subTest(batch=batch):
                result = run(batch)
                self.assertEqual(result['base_world_tick'], contract['input']['base_world_tick'])
                self.assertEqual(result['first_command'], contract['input']['first_command_frame'])
                launch, = result['launches']
                self.assertEqual(launch['command_frame'], expected['volley_frame'])
                self.assertEqual(launch['last_tick'], expected['flight_birth_tick'])
                query = result['query']
                self.assertEqual(query['world_tick'], contract['input']['query_world_tick'])
                self.assertEqual(query['started_tick'], expected['flight_birth_tick'])
                self.assertEqual(query['target_hp'], expected['enemy_health'])
                self.assertAlmostEqual(query['flown'], expected['flown_seconds'])
                for actual, wanted in zip(query['position'], expected['position'], strict=True):
                    self.assertAlmostEqual(actual, wanted)
                self.assertEqual(len(result['hits']), expected['damage_events'])
                self.assertEqual(result['hits'][0]['contact_world_tick'], expected['contact_world_tick'])
                self.assertEqual(result['retire_world_tick'], expected['retire_world_tick'])
