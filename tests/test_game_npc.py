"""Manual NPCs use the real Match combat and entity lifecycle."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_game_combat import Run

from ow174.game import npc, projectiles, world
from ow174.game.script import graph


class ManualNpcTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(graph, 'entity_body', return_value=None)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.run = Run()
        self.match = self.run.match
        self.player = self.run.players[0]
        sample = self.match.bots[0]
        self.definition = npc.Definition(sample.hero, sample.body, 'Manual enemy', 300, 100, 50)

    def create(self):
        return npc.spawn(self.match, self.definition, self.player.mover.position, team=1, yaw=123)

    def test_spawn_joins_combat_with_pools_create_boundary_and_corpse_policy(self):
        bot = self.create()
        target = next(t for t in self.match.combat._enemies(self.player) if t.owner is bot)
        self.assertEqual(self.match.combat.vitals_of(target).total, 450)
        self.assertTrue(bot.retain_corpse)
        self.assertEqual(bot.yaw, 123)
        self.assertGreater(bot.records_from, self.match.tick)
        self.assertEqual(self.match.combat.replacement_ticks[bot.entity], self.match.tick)
        self.assertTrue(any(u.entity == bot.entity and u.op == world.OP_CREATE
                            for u in self.player.client.entities))
        self.match.combat._damage(self.player, target, 1000, False, None, self.match.tick)
        self.assertTrue(bot.dead)
        self.assertIn(bot, self.match.bots)
        self.assertFalse(any(who is bot for _, who in self.match.combat.respawns))
        self.assertEqual(npc.remove_spawned(self.match), (bot.entity,))

    def test_remove_cancels_attacks_and_keeps_training_bots_and_player(self):
        original = list(self.match.bots)
        bot = self.create()
        self.match.combat.rockets.append(SimpleNamespace(shooter=bot))
        self.match.combat.respawns.append((9999, bot))
        self.match.combat.history[bot.entity] = []
        self.match.combat.melee_seen[bot.entity] = {}
        shot = projectiles.Shot(bot, bot.entity, 1, self.match.tick, 1, 0, (1, 0))
        delivery = Mock()
        shot.observers.append(delivery)
        self.match.combat.projectiles.shots.append(shot)
        self.assertEqual(npc.remove_spawned(self.match), (bot.entity,))
        self.assertEqual(self.match.bots, original)
        self.assertEqual(self.match.combat.rockets, [])
        self.assertEqual(self.match.combat.projectiles.shots, [])
        delivery.finish.assert_called_once_with(self.match.tick, False)
        self.assertFalse(any(who is bot for _, who in self.match.combat.respawns))
        for table in (self.match.combat.vitals, self.match.combat.history,
                      self.match.combat.melee_seen, self.match.combat.replacement_ticks):
            self.assertNotIn(bot.entity, table)
        self.assertTrue(any(u.entity == bot.entity and u.op == world.OP_DESTROY
                            for u in self.player.client.entities))
        self.assertEqual(npc.remove_spawned(self.match), ())
        self.assertGreater(self.create().entity, bot.entity)

    def test_not_yet_spawned_viewer_gets_no_early_create(self):
        self.player.spawned = False
        bot = self.create()
        self.assertFalse(any(u.entity == bot.entity for u in self.player.client.entities))
        self.assertIn(bot, self.match.bots)  # normal initial world catalog includes it on entry

    def test_rejects_invalid_input_before_allocating_or_mutating_match(self):
        before = (len(self.match.bots), self.match.combat.next_bot)
        for point in ((0, float('nan'), 0), (0, 0), (True, 0, 0)):
            with self.subTest(point=point), self.assertRaises(ValueError):
                npc.spawn(self.match, self.definition, point, team=1)
        with self.assertRaises(ValueError):
            npc.spawn(self.match, self.definition, (0, 0, 0), team=True)
        self.match.ended = True
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual((len(self.match.bots), self.match.combat.next_bot), before)

    def test_definition_requires_real_guid_types_and_explicit_valid_pools(self):
        for kwargs in ({'hero': 1}, {'body': 1}, {'health': 0}, {'armour': -1},
                       {'shields': float('inf')}, {'health': True}):
            values = {'hero': self.definition.hero, 'body': self.definition.body,
                      'name': 'Enemy', 'health': 300}
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                npc.Definition(**values)


class CatalogTests(unittest.TestCase):
    def test_catalog_round_trip_preserves_selection_and_shields_as_shields(self):
        row = {'key': '2', 'hero': '0x02E000000000016B', 'body': '0x0400000000001403',
               'name': 'Synthetic enemy', 'health': 250, 'armour': 0, 'shields': 100}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.json'
            path.write_text(json.dumps({'format_version': 1, 'definitions': [row]}), encoding='utf-8')
            definitions = npc.load_catalog(path)
            self.assertEqual(definitions['2'].health, 250)
            self.assertEqual(definitions['2'].armour, 0)
            self.assertEqual(definitions['2'].shields, 100)
            for key in ('2', '0', '12'):
                other = {**row, 'key': key}
                path.write_text(json.dumps({'format_version': 1, 'definitions': [row, other]}),
                                encoding='utf-8')
                with self.subTest(key=key), self.assertRaises(ValueError):
                    npc.load_catalog(path)
