"""Opt-in real NPC definitions/scripts and authored capsules; transport is an adapter.

Set ORPHEUS_PVE_NPC_DATA_DIR and ORPHEUS_NPC_XML_DIR to external read-only inputs.
These fixtures do not claim enemy navigation, special attacks or native transport.
"""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_tracer as tracer_tests
from test_game_combat import FakeClient, aim

from ow174.game import combat, mover, npc, npc_control, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.script import graph
from tools.extract_game_movers import mover_of


class NpcClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def bot_stream(self, entity):
        return None  # authoritative combat fixture omits NPC projectile observer transport

    def queue_bot_death(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.deaths.append((entity, snapshot))


class TracerNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = os.environ.get("ORPHEUS_PVE_NPC_DATA_DIR")
        xml = os.environ.get("ORPHEUS_NPC_XML_DIR")
        if not data or not xml:
            raise unittest.SkipTest("external NPC catalog/graph bundle and body XML required")
        cls.definitions = npc.load_catalog(Path(data) / "pve_npcs_174.json")
        if {d.name for d in cls.definitions.values()} != {
                "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise ValueError("acceptance requires all five actual NPC classes")
        cls.original_graph_path = graph.GRAPHS_PATH
        cls.addClassCleanup(cls.restore_graphs)
        graph.GRAPHS_PATH = Path(data) / "game_graphs_174.json.gz"
        cls.clear_graphs()
        values = {}
        for definition in cls.definitions.values():
            path = Path(xml) / f"{definition.body & 0xFFFFFFFFFFFF:012X}.003.xml"
            fields = mover_of(path.read_text(encoding="utf-8"))
            if fields is None:
                raise ValueError(f"missing authored NPC capsule: {definition.name}")
            values[definition.body] = mover.MoverData(**fields)
        cls.movers_patch = patch.dict(mover._movers(), values)
        cls.movers_patch.start()
        cls.addClassCleanup(cls.movers_patch.stop)

    @classmethod
    def clear_graphs(cls):
        for function in (graph.graph, graph._records, graph._data):
            function.cache_clear()

    @classmethod
    def restore_graphs(cls):
        graph.GRAPHS_PATH = cls.original_graph_path
        cls.clear_graphs()

    def fight(self, definition, *, allied=False, distance=5):
        run, player, enemy, _ = tracer_tests.TracerPistolTests().fight()
        enemy.mover.position[0] += 100
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.client = NpcClient(player)
        enemy.client = NpcClient(enemy)
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + distance),
                        team=player.team if allied else 1 - player.team)
        self.assertIsNotNone(bot.body_script)
        self.assertIsNone(bot.body_script.component.fault)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        self.assertEqual(target.height, mover.mover_data(definition.body).stand_height)
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + distance))
        return run, player, bot, target, yaw, pitch

    def test_primary_damages_authored_base_pools_of_all_five_classes(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                run.step({player: (yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, before - 12)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 12)

    def test_melee_damages_all_five_classes_once(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition, distance=1.5)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                run.step({player: (yaw, pitch, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, before - 30)

    def test_same_team_npcs_are_not_damaged(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition, allied=True)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                run.step({player: (yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, before)

    def test_each_actual_body_can_die_and_keeps_its_corpse_past_respawn(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, yaw, pitch = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                # Finish a wounded NPC through the actual input damage path.
                vitals.now[combat.HEALTH_POOL] = 11
                vitals.now[combat.SHIELDS_POOL] = 0
                run.step({player: (yaw, pitch, FIRE)})
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertTrue(player.client.deaths)
                self.assertIsNotNone(bot.body_script)
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))

    def engage(self, definition):
        run, player, bot, _, yaw, pitch = self.fight(definition)
        player.mover.yaw, player.mover.pitch = yaw, pitch
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        victim = next(t for t in run.match.combat._targets() if t.owner is player)
        return run, player, bot, run.match.combat.vitals_of(victim)

    def test_three_actual_primary_attacks_damage_tracer_and_stop_without_late_damage(self):
        # Controller eye aim hits the existing approximate head region: Heavy's5 becomes10.
        expected = {"Trooper": (20, 29), "Sniper": (100, 128), "Heavy Assault": (10, 32)}
        for definition in self.definitions.values():
            if definition.name not in expected:
                continue  # Enforcer/Assassin have explicit failed probe rows, not accepted passes.
            with self.subTest(npc=definition.name):
                run, _, bot, vitals = self.engage(definition)
                start_frame = run.frame
                for _ in range(200):
                    run.step({})
                    if vitals.total < 150:
                        break
                damage, first_frame = expected[definition.name]
                self.assertEqual((150 - vitals.total, run.frame - start_frame), (damage, first_frame))
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(80):
                    run.step({})
                self.assertEqual(vitals.total, 150 - damage)
                self.assertFalse(bot.attack_input)
                self.assertFalse(bot.body_script.component.shots)

    def test_recall_protects_from_three_actual_npc_attacks_then_damage_resumes(self):
        first_frames = {"Trooper": 29, "Sniper": 128, "Heavy Assault": 32}
        for definition in self.definitions.values():
            if definition.name not in first_frames:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.engage(definition)
                for _ in range(first_frames[definition.name] - 10):
                    run.step({})
                run.step({player: (0, 0, 16)})
                self.assertTrue(tracer.recalling(player))
                protected_frames = 0
                for _ in range(100):
                    if not tracer.recalling(player):
                        break
                    self.assertEqual(vitals.total, 150)
                    run.step({})
                    protected_frames += 1
                self.assertGreater(protected_frames, 60)
                self.assertFalse(tracer.recalling(player))
                for _ in range(500):
                    run.step({})
                    if vitals.total < 150:
                        break
                self.assertLess(vitals.total, 150)
                self.assertIsNotNone(bot.body_script)
                npc_control.stop(run.match)

    def test_wall_blocks_melee_to_all_five_actual_classes_without_damage_or_charge(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, yaw, pitch = self.fight(definition, distance=1.5)
                x, floor, z = player.mover.position
                face = z + 0.7
                triangles = [
                    (-1000, floor, -1000, -1000, floor, 1000, 1000, floor, 1000),
                    (-1000, floor, -1000, 1000, floor, 1000, 1000, floor, -1000),
                    (x-5, floor, face, x+5, floor, face, x+5, floor+4, face),
                    (x-5, floor, face, x+5, floor+4, face, x-5, floor+4, face),
                    (x-5, floor, face, x+5, floor+4, face, x+5, floor, face),
                    (x-5, floor, face, x-5, floor+4, face, x+5, floor+4, face),
                ]
                run.match.collision_world = World(triangles, name="Tracer melee NPC wall")
                for other in run.players:
                    other.mover.use(world=run.match.collision_world)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                run.step({player: (yaw, pitch, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertEqual(player.client.messages(combat.HIT), [])
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_pulse_bomb_deals_contact_and_blast_to_all_five_base_pools(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _, _ = self.fight(definition)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                for _ in range(120):
                    run.step({})
                self.assertEqual(vitals.total, max(0, before - 355))
                self.assertEqual(bot.dead, before <= 355)


if __name__ == "__main__":
    unittest.main()
