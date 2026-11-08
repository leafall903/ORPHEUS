"""Opt-in actual five-body fixtures, using an external read-only NPC catalog/bundle.

These test combat targets and pool changes, not autonomous AI or retail difficulty.
Set ORPHEUS_ZARYA_NPC_DATA to the prepared NPC data directory to execute them.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import FakeClient, Run

from ow174.game import combat, npc
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import graph

NPC_DATA = os.environ.get("ORPHEUS_ZARYA_NPC_DATA")
ZARYA = 0x02E0000000000068


class NpcClient(FakeClient):
    """Delivery-model callbacks; native wire/presentation is a separate acceptance gate."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []
        self.death_updates = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.death_updates.append((entity, event))

    def bot_stream(self, entity):
        return None  # No observer projectile transport in this damage fixture.


@unittest.skipUnless(NPC_DATA, "requires external five-NPC data; not acceptance when skipped")
class ZaryaFiveNpcTests(unittest.TestCase):
    @classmethod
    def clear_graph_cache(cls):
        for function in (graph._data, graph._records, graph.graph):
            function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(NPC_DATA)
        cls.catalog = npc.load_catalog(cls.directory / "pve_npcs_174.json")
        if {d.name for d in cls.catalog.values()} != {
                "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise AssertionError("requires the five actual requested NPC definitions")
        cls.bundle = patch.object(graph, "GRAPHS_PATH", cls.directory / "game_graphs_174.json.gz")
        cls.bundle.start()
        cls.clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        cls.bundle.stop()
        cls.clear_graph_cache()

    def encounter(self, definition, *, team=1, z=5):
        run = Run(names=("Zarya",))
        player, = run.players
        player.client = NpcClient(player)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        player.mover.position[:] = [0, 1, 0]
        bot = npc.spawn(run.match, definition, (0, 1, z), team=team)
        self.assertEqual(bot.hero, definition.hero)
        self.assertEqual(bot.body, definition.body)
        self.assertIsNotNone(bot.body_script)
        return run, player, bot

    @staticmethod
    def fire(run, player, frames=51, buttons=1):
        for _ in range(frames):
            run.step({player: (0, 0, buttons)})

    def test_beam_deals_60_to_each_actual_enemy_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.fire(run, player)
                self.assertAlmostEqual(before - vitals.total, 60)
                self.assertTrue(player.client.messages(combat.HIT))

    def test_beam_does_not_damage_allied_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0)
                before = run.match.combat.vitals[bot.entity].total
                self.fire(run, player)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)

    def test_secondary_deals_authored_47_to_each_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.fire(run, player, 1, 2)
                self.fire(run, player, 90, 0)
                self.assertAlmostEqual(before - vitals.total, 47)

    def test_allied_bodies_reject_secondary_and_melee_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0, z=1.5)
                before = run.match.combat.vitals[bot.entity].total
                self.fire(run, player, 1, 2)
                self.fire(run, player, 90, 0)
                self.fire(run, player, 1, 0x800)
                self.fire(run, player, 40, 0)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)

    def test_melee_deals_30_to_each_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, z=1.5)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.fire(run, player, 1, 0x800)
                self.fire(run, player, 40, 0)
                self.assertAlmostEqual(before - vitals.total, 30)

    def test_projected_barrier_protects_each_real_allied_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0)
                self.fire(run, player, 1, 16)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                # Explicit hostile actor fixture; this is not evidence of autonomous AI.
                enemy = npc.spawn(run.match, definition, (0, 1, 10), team=1)
                run.match.combat._damage(enemy, target, 100, False, None, run.frame)
                self.assertEqual(vitals.total, before)
                self.fire(run, player, 1, 0)
                self.assertEqual(player.body_script.component.entity_var(87).value(), 20)

    def test_static_wall_blocks_melee_on_each_actual_body(self):
        wall = World([(-10, 0, 0.75, 10, 0, 0.75, 10, 10, 0.75),
                      (-10, 0, 0.75, 10, 10, 0.75, -10, 10, 0.75)])
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, z=1.5)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                with patch.object(run.match.combat, "_collision", return_value=wall):
                    self.fire(run, player, 1, 0x800)
                    self.fire(run, player, 40, 0)
                self.assertEqual(vitals.total, before)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_static_wall_blocks_secondary_blast_on_each_actual_body(self):
        wall = World([(-10, 0, 5, 10, 0, 5, 10, 10, 5),
                      (-10, 0, 5, 10, 10, 5, -10, 10, 5)])
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, z=6)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                with patch.object(run.match.combat, "_collision", return_value=wall):
                    self.fire(run, player, 1, 2)
                    self.fire(run, player, 60, 0)
                self.assertEqual(vitals.total, before)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_mounted_sniper_first_authored_shot_deals_100_to_zarya(self):
        definition = next(d for d in self.catalog.values() if d.name == "Sniper")
        run, player, bot = self.encounter(definition)
        bot.yaw = -32768
        bot.firing = True
        bot.body_script.component.run_frame(run.frame, [(20, True)])
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(200):
            self.fire(run, player, 1, 0)
            if vitals.total < 400:
                break
        self.assertEqual(vitals.total, 300)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_personal_barrier_blocks_mounted_sniper_shot_and_gains_energy(self):
        definition = next(d for d in self.catalog.values() if d.name == "Sniper")
        run, player, bot = self.encounter(definition)
        bot.yaw = -32768
        bot.firing = True
        bot.body_script.component.run_frame(run.frame, [(20, True)])
        # Explicit reaction timing fixture; no automatic barrier/AI policy.
        self.fire(run, player, 20, 0)
        self.fire(run, player, 1, 8)
        self.fire(run, player, 120, 0)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertEqual(player.body_script.component.entity_var(87).value(), 20)

    def test_lethal_beam_leaves_each_manual_corpse_without_respawn(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                # Shorten setup, keeping the actual body, graphs and final authored shot.
                vitals.now = dict.fromkeys(vitals.now, 0.0)
                vitals.now[combat.HEALTH_POOL] = 1.0
                self.fire(run, player, 5)
                self.assertTrue(bot.dead)
                self.assertTrue(bot.retain_corpse)
                self.assertEqual([entity for entity, _ in player.client.deaths], [bot.entity])
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                self.fire(run, player, 200, 0)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(vitals.total, 0)

    def test_authored_trooper_and_heavy_volleys_damage_zarya(self):
        expected = {"Trooper": 120, "Heavy Assault": 110}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                bot.yaw = -32768
                bot.firing = True
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                vitals = run.match.combat.vitals_of(target)
                # Explicit AI input fixture: actual body graph button20, not autonomous AI.
                bot.body_script.component.run_frame(run.frame, [(20, True)])
                self.fire(run, player, 120, 0)
                self.assertEqual(400 - vitals.total, expected[definition.name])
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_personal_barrier_blocks_actual_trooper_and_heavy_volleys(self):
        for definition in self.catalog.values():
            if definition.name not in {"Trooper", "Heavy Assault"}:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                self.fire(run, player, 1, 8)
                bot.yaw = -32768
                bot.firing = True
                bot.body_script.component.run_frame(run.frame, [(20, True)])
                self.fire(run, player, 120, 0)
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                self.assertEqual(run.match.combat.vitals_of(target).total, 400)
                self.assertGreater(player.body_script.component.entity_var(87).value(), 0)

    def test_projected_barrier_blocks_trooper_sniper_heavy_attacks_on_ally(self):
        for definition in self.catalog.values():
            if definition.name not in {"Trooper", "Sniper", "Heavy Assault"}:
                continue
            with self.subTest(npc=definition.name):
                run = Run(names=("Caster", "Ally"))
                caster, ally = run.players
                for player in run.players:
                    player.client = NpcClient(player)
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                ally.team = caster.team
                caster.mover.position[:] = [0, 1, 0]
                ally.mover.position[:] = [0, 1, 5]
                bot = npc.spawn(match, definition, (0, 1, 10), team=1)
                bot.yaw, bot.firing = -32768, True
                bot.body_script.component.run_frame(run.frame, [(20, True)])
                # Explicit reactive timing puts Sniper's first shot within the
                # actual two-second projection; no AI/barrier automation implied.
                self.fire(run, caster, 20, 0)
                self.fire(run, caster, 1, 16)
                target = next(t for t in match.combat._targets() if t.owner is ally)
                vitals = match.combat.vitals_of(target)
                before = vitals.total
                self.fire(run, caster, 120, 0)
                self.assertEqual(vitals.total, before)
                self.assertGreater(caster.body_script.component.entity_var(87).value(), 0)
                energy = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0242)
                self.assertEqual(energy.vars[392].value().id, bot.entity)

    def test_ultimate_deals_25_and_pulls_each_actual_enemy_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                before, position = vitals.total, bot.position
                player.body_script.set_ult_charge(10000)
                self.fire(run, player, 1, 0x20)
                self.fire(run, player, 280, 0)
                self.assertAlmostEqual(before - vitals.total, 25)
                self.assertLess(bot.position[2], position[2])
                self.assertFalse(run.match.combat.zarya_gravitons.fields)
                self.assertFalse(run.match.combat.zarya_gravitons.npc_movers)

    def test_ultimate_never_damages_or_pulls_each_allied_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0)
                before, position = run.match.combat.vitals[bot.entity].total, bot.position
                player.body_script.set_ult_charge(10000)
                self.fire(run, player, 1, 0x20)
                self.fire(run, player, 280, 0)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(bot.position, position)


if __name__ == "__main__":
    unittest.main()
