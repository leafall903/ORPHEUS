"""Manual NPCs use the real Match combat and entity lifecycle."""

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_game_combat import Run

from ow174.game import npc, projectiles, world
from ow174.game.commands import FIRE
from ow174.game.mover import MoverData
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
        bot.firing = True
        bot.attack_target = self.player.entity
        self.match.combat._damage(self.player, target, 1000, False, None, self.match.tick)
        self.assertTrue(bot.dead)
        self.assertFalse(bot.firing)
        self.assertIsNone(bot.attack_target)
        self.assertIn(bot, self.match.bots)
        self.assertFalse(any(who is bot for _, who in self.match.combat.respawns))
        self.assertEqual(npc.remove_spawned(self.match), (bot.entity,))

    def test_spawn_syncs_script_health_after_all_authoritative_pools_exist(self):
        script = Mock()
        with patch.object(self.match, '_prepare_bot_script',
                          side_effect=lambda bot: setattr(bot, 'body_script', script)):
            bot = self.create()
        self.assertEqual(self.match.combat.vitals[bot.entity].total, 450)
        script.set_health.assert_called_once_with(450, 450)

    def test_remove_cancels_attacks_and_keeps_training_bots_and_player(self):
        original = list(self.match.bots)
        bot = self.create()
        self.match.combat.rockets.append(SimpleNamespace(shooter=bot))
        self.match.combat.respawns.append((9999, bot))
        self.match.combat.history[bot.entity] = []
        self.match.combat.melee_seen[bot.entity] = {}
        self.match.combat.genji_assists[bot.entity] = {self.player.body: (self.player, self.match.tick)}
        shot = projectiles.Shot(bot, bot.entity, 1, self.match.tick, 1, 0, (1, 0))
        delivery = Mock()
        shot.observers.append(delivery)
        self.match.combat.projectiles.shots.append(shot)
        self.assertEqual(npc.remove_spawned(self.match), (bot.entity,))
        self.assertEqual(self.match.bots, original)
        self.assertEqual(self.match.combat.rockets, [])
        self.assertEqual(self.match.combat.projectiles.shots, [])
        self.assertNotIn(bot.entity, self.match.combat.genji_assists)
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


    def test_authored_controller_reference_survives_spawn_without_claiming_control_mounting(self):
        self.definition = replace(self.definition, controller=0x040000000000227A)
        count = len(self.match.bots)
        bot = self.create()
        self.assertEqual(bot.spawner['controller_definition'], '0x040000000000227A')
        self.assertEqual(len(self.match.bots), count + 1)
        for invalid in (True, 0, 0x02E00000000001CE, '0x040000000000227A'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(self.definition, controller=invalid)

    def test_explicit_mover_controls_actual_capsule_hits_and_movement_values(self):
        results = []
        for movement in (None, MoverData(stand_height=2.3, stand_radius=.7, run_forward=2.2)):
            definition = replace(self.definition, mover=movement)
            x, y, z = self.player.mover.position
            bot = npc.spawn(self.match, definition, (x + .65, y, z + 6), team=1)
            target = next(t for t in self.match.combat._targets() if t.owner is bot)
            before = self.match.combat.vitals_of(target).total
            self.run.step({self.player: (0, 0, FIRE)})
            results.append(before - self.match.combat.vitals_of(target).total)
            if movement is not None:
                self.assertEqual((target.height, target.radius, bot.speed), (2.3, .7, 2.2))
            self.run.step({})
            for _ in range(10):
                self.run.step({})
            npc.remove_spawned(self.match)
        self.assertEqual(results[0], 0)
        self.assertGreater(results[1], 0)

    def test_explicit_mover_rejects_nonfinite_and_nonpositive_capsules(self):
        for movement in (MoverData(stand_radius=0), MoverData(stand_height=-1),
                         MoverData(run_forward=float('nan')), MoverData(stand_radius=True)):
            with self.subTest(movement=movement), self.assertRaises(ValueError):
                replace(self.definition, mover=movement)

class CatalogTests(unittest.TestCase):
    def test_catalog_mover_round_trip_requires_complete_finite_fields(self):
        row = {'key': '1', 'hero': '0x02E000000000016B', 'body': '0x0400000000001403',
               'name': 'Prepared enemy', 'health': 250, 'armour': 0, 'shields': 100,
               'mover': asdict(MoverData(stand_height=2.3, stand_radius=.7))}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.json'
            for movement, valid in ((row['mover'], True), ({'stand_height': 2.3}, False),
                                    ({**row['mover'], 'stand_height': float('inf')}, False)):
                data = {'format_version': 1, 'definitions': [{**row, 'mover': movement}]}
                path.write_text(json.dumps(data), encoding='utf-8')
                if valid:
                    self.assertEqual(npc.load_catalog(path)['1'].mover.stand_radius, .7)
                else:
                    with self.assertRaises(ValueError):
                        npc.load_catalog(path)

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
