"""Torbjorn entity team metadata through the existing encrypted join path; no renderer claims."""
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_repeated_lifecycles as protocol
import test_game_server as server_tests
import test_game_torbjorn_molten as area_tests
from test_torbjorn_descriptor_source import command

from ow174.game import hero_torbjorn, molten_core
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.script import decode, graph


@unittest.skipUnless(os.environ.get('ORPHEUS_TORBJORN_GRAPH_BUNDLE'), 'needs prepared Torbjorn bundle')
class TorbjornTeamMetadataTests(unittest.TestCase):
    def test_pool_team_and_caster_reach_self_allies_enemies_and_late_joins(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/torbjorn-pool-team-metadata-v1.json').read_text())
        for case in fixture['input']['cases'][:2]:
            with self.subTest(owner_team=case['team']):
                self.pool_join_scenario(case['team'], case['filter_bits'])

    def test_ffa_pool_keeps_birth_filter_when_owner_team_context_changes(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/torbjorn-pool-team-metadata-v1.json').read_text())
        case = fixture['input']['cases'][2]
        run, owner, _, pool, _ = area_tests.TorbjornMoltenTests().arena()
        self.assertTrue(run.match.game_map.free_for_all)
        self.assertEqual(pool.filter_bits, case['filter_bits'])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = 1
        self.assertNotEqual(owner.team_bit, pool.filter_bits)
        self.assertEqual(pool.filter_bits, case['filter_bits'])

    def pool_join_scenario(self, owner_team, expected_bits):
        enemy_team = 1 - owner_team
        run = protocol.ProtocolHarness()
        try:
            handoffs = run.server.create_match(replace(PRACTICE_RANGE, free_for_all=False), [
                (1, 'Torbjorn', 0, owner_team, False), (2, 'Ally', 0, owner_team, False),
                (3, 'Enemy', 0, enemy_team, False),
            ])
            peers = [run.connect(h) for h in handoffs]
            owner_link, owner = peers[0]
            for link, player in peers:
                run.message(link, 21616, server_tests.pick(
                    player.entity, hero_torbjorn.HERO if player is owner else SOLDIER,
                ))
                command(run, link, player)
            run.until(lambda: all(owner.body in link.models for link, _ in peers))

            def advance(count):
                for _ in range(count):
                    command(run, owner_link, owner)
                    run.step()

            for n, (_, player) in enumerate(peers[1:], 1):
                player.mover.position[0] += 100 * n
            advance(20)
            command(run, owner_link, owner, buttons=8, pitch=4000)
            advance(100)
            turret = owner.match.combat.turrets[owner.entity]
            owner.body_script.set_ult_charge(10000)
            command(run, owner_link, owner, buttons=32)
            advance(45)
            launcher, team = owner.body, owner.team_bit
            self.assertEqual(team, expected_bits)
            command(run, owner_link, owner, buttons=1, pitch=4000)
            advance(60)
            pool = next(iter(owner.match.combat.molten_pools.values()))
            for link, _player in peers:
                self.assertEqual(link.fields[owner.body][26], {0: team})
                self.assertEqual(link.fields.get(pool.entity, {}).get(26), {0: team})
            # Existing late-join delivery must carry the captured team and descriptor source.
            late = run.server.join_match(owner.match, [
                (4, 'Late Ally', 0, owner_team, False), (5, 'Late Enemy', 0, enemy_team, False),
            ])
            for handoff in late:
                link, player = run.connect(handoff)
                run.message(link, 21616, server_tests.pick(player.entity, SOLDIER))
                command(run, link, player)
                peers.append((link, player))
            run.until(lambda: all(any(u.entity == pool.entity and u.chunk is not None
                                      for _, u in link.records) for link, _ in peers))
            initial = [i['graph'] for i in graph.entity_body(molten_core.POOL)['graphs']]
            for link, player in peers:
                with self.subTest(viewer=player.name, team=player.team):
                    self.assertEqual(link.fields[owner.body][26], {0: team})
                    self.assertEqual(link.fields[turret.actor.entity][26], {0: team})
                    self.assertEqual(link.fields[pool.entity][26], {0: team})
                    model = decode.ClientBody(initial)
                    for _, update in link.records:
                        if update.entity != pool.entity or update.chunk is None:
                            continue
                        frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, model.graphs)
                        if frame.last > model.applied and frame.first <= model.applied + 1:
                            model.apply(update)
                    self.assertEqual(model.sources[pool.instance], launcher)
                    self.assertNotIn(72, link.fields[pool.entity])  # no speculative Pet binding
        finally:
            run.close()
