"""Only the actual player-owned Torbjorn turret opts into captured NPC filter bits."""
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_repeated_lifecycles as protocol
import test_game_server as server_tests
from test_torbjorn_descriptor_source import command

from ow174.game import hero_torbjorn
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_TORBJORN_GRAPH_BUNDLE'), 'needs prepared Torbjorn bundle')
class TorbjornOwnedFilterTests(unittest.TestCase):
    def test_team_turret_capture_and_body_replacement_reach_current_and_late_peers(self):
        self.scenario(False)

    def test_ffa_turret_capture_and_body_replacement_reach_current_and_late_peers(self):
        self.scenario(True)

    def scenario(self, free_for_all):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/torbjorn-owned-filter-v1.json').read_text())
        expected = next(c['filter_bits'] for c in fixture['input']['cases']
                        if c['free_for_all'] == free_for_all)
        run = protocol.ProtocolHarness()
        try:
            handoffs = run.server.create_match(replace(PRACTICE_RANGE, free_for_all=free_for_all), [
                (1, 'Torbjorn', 0, 0, False), (2, 'Ally', 0, 0, False), (3, 'Enemy', 0, 1, False),
            ])
            peers = [run.connect(h) for h in handoffs]
            owner_link, owner = peers[0]
            for link, player in peers:
                run.message(link, 21616, server_tests.pick(
                    player.entity, hero_torbjorn.HERO if player is owner else SOLDIER,
                ))
                command(run, link, player)
            run.until(lambda: all(owner.body in link.models for link, _ in peers))
            for n, (_, player) in enumerate(peers[1:], 1):
                player.mover.position[0] += 100 * n

            def advance(count):
                for _ in range(count):
                    command(run, owner_link, owner)
                    run.step()

            advance(20)
            original_body = owner.body
            command(run, owner_link, owner, buttons=8, pitch=4000)
            advance(100)
            turret = owner.match.combat.turrets[owner.entity]
            self.assertIs(turret.actor.combat_owner, owner)
            for link, player in peers:
                with self.subTest(mode=free_for_all, viewer=player.name, phase='initial'):
                    self.assertEqual(link.fields[owner.body][26][0], expected)
                    self.assertEqual(link.fields[turret.actor.entity][26][0], expected)
            self.assertEqual(turret.actor.spawner.get('source_filter_bits'), expected)
            self.assertIsNone(turret.actor.pet_owner)
            sources = {i.id: (i.cached_source_entity, i.descriptor_source_entity)
                       for i in turret.actor.body_script.component.instances.values()}

            late = run.server.join_match(owner.match, [
                (4, 'Late Ally', 0, 0, False), (5, 'Late Enemy', 0, 1, False),
            ])
            for handoff in late:
                link, player = run.connect(handoff)
                run.message(link, 21616, server_tests.pick(player.entity, SOLDIER))
                command(run, link, player)
                player.mover.position[0] += 100 * player.slot
                peers.append((link, player))
            run.until(lambda: all(turret.actor.entity in link.fields for link, _ in peers))
            for link, player in peers:
                with self.subTest(mode=free_for_all, viewer=player.name, phase='late'):
                    self.assertEqual(link.fields[turret.actor.entity][26][0], expected)
                    self.assertNotIn(72, link.fields[turret.actor.entity])
            self.assertEqual(sources, {i.id: (i.cached_source_entity, i.descriptor_source_entity)
                                     for i in turret.actor.body_script.component.instances.values()})

            self.assertTrue(owner.match.switch_hero(owner, heroes()[SOLDIER]))
            self.assertTrue(owner.match.switch_hero(owner, heroes()[hero_torbjorn.HERO]))
            self.assertNotEqual(owner.body, original_body)
            self.assertNotIn(turret.actor, owner.match.bots)
            replacement = hero_torbjorn.deploy_turret(
                owner.match.combat, owner, tuple(owner.mover.position),
            )
            run.until(lambda: all(replacement.actor.entity in link.fields for link, _ in peers))
            for link, player in peers:
                with self.subTest(mode=free_for_all, viewer=player.name, phase='replacement'):
                    self.assertEqual(link.fields[replacement.actor.entity][26][0], expected)
                    self.assertNotIn(72, link.fields[replacement.actor.entity])
            self.assertEqual(replacement.actor.spawner.get('source_filter_bits'), expected)
            self.assertIsNone(replacement.actor.pet_owner)
        finally:
            run.close()
