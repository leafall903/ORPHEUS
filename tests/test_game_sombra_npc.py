"""Actual five-NPC body data in a Match; fixture collision/transport are not native acceptance."""

import os
import unittest
from pathlib import Path

import test_game_sombra_combat as sombra_fixture
from test_game_combat import aim

from ow174.game import combat, npc, npc_control
from ow174.game.commands import FIRE
from ow174.game.script import graph
from ow174.game.script.driver import OWNER_HEALTH


def clear_graphs():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


class SombraActualNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not path:
            raise unittest.SkipTest("set ORPHEUS_NPC_RUNTIME_DIR to the external actual-NPC runtime")
        folder = Path(path) / "data"
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        cls.addClassCleanup(cls.restore_graphs, graph.GRAPHS_PATH)
        graph.GRAPHS_PATH = folder / "game_graphs_174.json.gz"
        clear_graphs()

    @classmethod
    def restore_graphs(cls, path):
        graph.GRAPHS_PATH = path
        clear_graphs()

    def make(self, key, *, distance=1.5, team=1):
        fixture = sombra_fixture.SombraCombatTests()
        fixture.setUp()
        fixture.b.spawned = False  # leave only the actual NPC as the enemy target
        for player in fixture.run.players:
            # The model does not claim NPC projectile observer-wire acceptance.
            player.client.bot_stream = lambda body: None
            player.client.npc_deaths = []
            player.client.queue_bot_death = (
                lambda body, event, log=player.client.npc_deaths: log.append((body, event))
            )
            player.client.queue_bot_death_update = (
                lambda body, event, log=player.client.npc_deaths: log.append((body, event))
            )
        bot = npc.spawn(fixture.run.match, self.catalog[key], (0, 0, distance), team=team)
        # Explicit first authored weapon profile, not retail difficulty.
        bot.body_script.component.set(9948, 0)
        fixture.press(0, 4)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, self.catalog[key].body)
        return fixture, bot

    @staticmethod
    def press(fixture, bot, buttons, frames=1):
        x, y, z = bot.position
        yaw, pitch = aim(fixture.run.match.combat._eye(fixture.a), (x, y + 1, z))
        for _ in range(frames):
            fixture.run.step({fixture.a: (yaw, pitch, buttons)})

    def test_actual_catalog_pistol_damage_and_initial_total_health_for_all_five(self):
        self.assertEqual([self.catalog[str(i)].name for i in range(1, 6)],
                         ["Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"])
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                fight = fixture.run.match.combat
                before = definition.health + definition.armour + definition.shields
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), before)
                self.press(fixture, bot, FIRE)
                self.press(fixture, bot, 0, 3)
                self.assertEqual(fight.vitals[bot.entity].total, before - 8)
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), before - 8)
                self.assertEqual(fixture.weapon().vars[53].value(), 59)

    def test_actual_npc_melee_and_allied_exclusion_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                fight = fixture.run.match.combat
                before = fight.vitals[bot.entity].total
                self.press(fixture, bot, sombra_fixture.MELEE)
                self.press(fixture, bot, 0, 45)
                self.assertEqual(fight.vitals[bot.entity].total, before - 30)
                fixture.run.match.game_map = fixture.run.match.game_map.__class__(**{
                    **fixture.run.match.game_map.__dict__, "free_for_all": False,
                })
                bot.team = fixture.a.team
                self.press(fixture, bot, FIRE, 20)
                self.press(fixture, bot, 0, 3)
                self.press(fixture, bot, sombra_fixture.MELEE)
                self.press(fixture, bot, 0, 45)
                self.assertEqual(fight.vitals[bot.entity].total, before - 30)

    def test_actual_npc_hack_application_ownership_and_timed_status_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                if key == "2":
                    # The actual Enforcer fixture seeds the authored rejection
                    # flag14679. Preserve that rejection, then explicitly release
                    # it through the declared host variable for the second case.
                    self.assertTrue(bot.body_script.component.entity_var(14679).value())
                    self.press(fixture, bot, 2, 100)
                    self.assertFalse(bot.body_script.component.entity_var(888).value())
                    self.assertEqual(fixture.weapon().state(53).remaining(), 0)
                    bot.body_script.component.set(14679, False)
                    self.press(fixture, bot, 0, 30)
                self.press(fixture, bot, 2, 100)
                component = bot.body_script.component
                fight = fixture.run.match.combat
                self.assertTrue(component.entity_var(888).value())
                self.assertEqual(component.entity_var(6396).value().id, fixture.a.body)
                deadline = fight.hacks[bot.entity][0].until_tick
                self.press(fixture, bot, 0, deadline - fixture.run.frame)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertFalse(component.entity_var(888).value())
                self.assertFalse(component.entity_var(6396).value())
                self.assertEqual(fight.vitals[bot.entity].total,
                                 definition.health + definition.armour + definition.shields)

    def test_four_actual_weapon_npcs_deal_damage_through_authored_reports(self):
        # Assassin's TrackTargets/ModifyHealth attack is a separate OPEN path.
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=3)
                npc_control.start(fixture.run.match, fixture.a)
                fixture.press(0, 300)
                self.assertIsNotNone(bot.body_script)
                self.assertLess(fixture.health(fixture.a), 200)

    def test_enforcer_explicit_weapon_profiles_change_actual_pool_loss(self):
        for profile, damage in ((0, 2), (1, 4)):
            with self.subTest(profile=profile):
                fixture, bot = self.make("2", distance=3)
                bot.body_script.component.set(9948, profile)
                npc_control.start(fixture.run.match, fixture.a)
                for _ in range(250):
                    fixture.press(0)
                    if fixture.health(fixture.a) < 200:
                        break
                self.assertEqual(fixture.health(fixture.a), 200 - damage)

    def test_four_actual_weapon_npcs_stop_releases_input_and_prevents_further_damage(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=3)
                match = fixture.run.match
                npc_control.start(match, fixture.a)
                fixture.press(0, 10)
                npc_control.stop(match)
                before = fixture.health(fixture.a)
                fixture.press(0, 180)
                self.assertEqual(fixture.health(fixture.a), before)
                self.assertFalse(bot.firing)
                self.assertNotIn(20, bot.body_script.component.held)
                self.assertFalse(any(r.shooter is bot for r in match.combat.rockets))

    def test_actual_npc_static_wall_blocks_pistol_and_hack_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                bot.body_script.component.set(14679, False)  # isolate LOS from authored rejection flags
                fixture.wall()
                before = fixture.run.match.combat.vitals[bot.entity].total
                self.press(fixture, bot, FIRE, 30)
                self.press(fixture, bot, 0, 3)
                self.press(fixture, bot, 2, 100)
                self.assertEqual(fixture.run.match.combat.vitals[bot.entity].total, before)
                self.assertFalse(bot.body_script.component.entity_var(888).value())
                self.assertEqual(fixture.weapon().state(53).remaining(), 0)

    def test_actual_npcs_killed_by_pistol_keep_corpses_past_respawn_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                fight = fixture.run.match.combat
                for _ in range(4000):
                    if bot.dead:
                        break
                    if fixture.weapon().vars[53].value() <= 0:
                        self.press(fixture, bot, sombra_fixture.RELOAD)
                        self.press(fixture, bot, 0, 100)
                    else:
                        self.press(fixture, bot, FIRE)
                self.assertTrue(bot.dead)
                self.assertEqual(fight.vitals[bot.entity].total, 0)
                self.assertTrue(bot.retain_corpse)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertTrue(fixture.a.client.npc_deaths)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.press(fixture, bot, 0, round(combat.BOT_RESPAWN / combat.TICK) + 10)
                self.assertIn(bot, fixture.run.match.bots)
                self.assertEqual(fight.vitals[bot.entity].total, 0)
                self.assertFalse(any(t.entity == bot.entity for t in fight._enemies(fixture.a)))


if __name__ == "__main__":
    unittest.main()
