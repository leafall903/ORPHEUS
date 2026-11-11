"""Pharah's extracted primary weapon through real Match commands; no live-client claim."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import bots, combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes

PHARAH = 0x02E0000000000008


class PharahCombatTests(unittest.TestCase):
    def encounter(self):
        run = Run(KANEZAKA_DM, ("Pharah", "Enemy"))
        shooter, victim = run.players
        run.match.switch_hero(shooter, heroes()[PHARAH])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        y = shooter.mover.position[1]
        shooter.mover.position = [0.0, y, 0.0]
        victim.mover.position = [0.0, y, 8.0]
        for _ in range(30):
            run.step({})
        return run, shooter, victim

    def fire_once(self, run, shooter):
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(60):
            run.step({})

    def health(self, run, owner):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target)

    def test_primary_is_40_direct_plus_80_splash_without_headshot(self):
        run, shooter, victim = self.encounter()
        self.fire_once(run, shooter)
        self.assertEqual(self.health(run, victim).total, 80.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [40, 80])
        self.assertTrue(all(h["+0x78"]["+0x8"] == 0 for h in hits))
        self.assertFalse(run.match.combat.rockets)

    def test_primary_in_flight_uses_target_team_at_contact(self):
        for becomes_ally in (False, True):
            with self.subTest(becomes_ally=becomes_ally):
                run = Run(KANEZAKA_DM, ("Pharah", "Front", "Rear"))
                shooter, front, rear = run.players
                match = run.match
                self.assertTrue(match.switch_hero(shooter, heroes()[PHARAH]))
                match.bots.clear()
                match._pending_bot_scripts.clear()
                match.game_map = replace(match.game_map, free_for_all=False)
                shooter.team, front.team, rear.team = 0, int(becomes_ally), 1
                y = shooter.mover.position[1]
                for player, z in ((shooter, 0), (front, 8), (rear, 14)):
                    player.mover.position = [0, y, z]
                    player.mover.body = None
                for _ in range(30):
                    run.step({})
                shooter.body_script.ult_seconds = 0
                shooter.body_script.set_ult_charge(0)
                run.step({shooter: (0, 0, FIRE)})
                rocket, = match.combat.rockets
                self.assertLess(rocket.position[2], front.mover.position[2])
                front.team = 0 if becomes_ally else 1
                for _ in range(40):
                    run.step({})
                    if rocket not in match.combat.rockets:
                        break
                self.assertNotIn(rocket, match.combat.rockets)
                hit, untouched = (rear, front) if becomes_ally else (front, rear)
                self.assertEqual(self.health(run, hit).total, 80)
                self.assertEqual(self.health(run, untouched).total, 200)
                self.assertEqual(shooter.body_script.component.entity_var(342).value(), 120)
                self.assertEqual([event["+0x78"]["+0x4"] for event in shooter.client.messages(combat.HIT)], [40, 80])

    def test_primary_body_contact_splashes_a_second_body_unless_world_occludes(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run = Run(KANEZAKA_DM, ("Pharah", "Contact", "Splash"))
                shooter, contact, splash = run.players
                run.match.switch_hero(shooter, heroes()[PHARAH])
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                y = shooter.mover.position[1]
                for player, z in ((shooter, 0.0), (contact, 8.0), (splash, 9.5)):
                    player.mover.position = [0.0, y, z]
                    player.mover.body = None
                for _ in range(30):
                    run.step({})
                z = 8.75
                triangles = [
                    (-3, y-3, z, 3, y-3, z, 3, y+4, z),
                    (-3, y-3, z, 3, y+4, z, -3, y+4, z),
                ] if cover else []
                with patch.object(run.match.combat, "_collision", return_value=World(triangles)):
                    self.fire_once(run, shooter)

                self.assertEqual(self.health(run, contact).total, 80)
                hits = shooter.client.messages(combat.HIT)
                amounts = [hit["+0x78"]["+0x4"] for hit in hits]
                self.assertEqual(amounts[:2], [40, 80])
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == contact.body for hit in hits[:2]))
                if cover:
                    self.assertEqual(self.health(run, splash).total, 200)
                    self.assertEqual(len(amounts), 2)
                else:
                    damage = 200 - self.health(run, splash).total
                    self.assertGreater(damage, 80 * 0.308)
                    self.assertLess(damage, 80)
                    self.assertEqual(len(amounts), 3)
                    self.assertAlmostEqual(damage, 52.32, places=4)
                    self.assertEqual(amounts[2], round(damage))
                    self.assertEqual(hits[2]["+0x78"]["+0x0"]["+0x0"], splash.body)

    def test_second_primary_kills_and_dead_body_cannot_fire(self):
        run, shooter, victim = self.encounter()
        self.fire_once(run, shooter)
        self.fire_once(run, shooter)
        self.assertTrue(self.health(run, victim).dead)
        self.assertEqual(self.health(run, victim).total, 0.0)
        before = self.health(run, shooter).total
        for _ in range(20):
            run.step({victim: (-32768, 0, FIRE)})
        self.assertEqual(self.health(run, shooter).total, before)

    def test_enemy_player_rifle_damages_pharah(self):
        run, pharah, enemy = self.encounter()
        for _ in range(8):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertLess(self.health(run, pharah).total, 200.0)
        self.assertTrue(enemy.client.messages(combat.HIT))

    def test_primary_consumes_real_reinhardt_armor_and_zenyatta_shield_pools(self):
        for hero_id, expected in ((0x02E0000000000007, 390.0), (0x02E0000000000020, 80.0)):
            with self.subTest(hero=hero_id):
                run, shooter, victim = self.encounter()
                run.match.switch_hero(victim, heroes()[hero_id])
                victim.mover.position = [0, shooter.mover.position[1], 8]
                victim.mover.body = None
                self.fire_once(run, shooter)
                self.assertEqual(self.health(run, victim).total, expected)

    def test_primary_passes_allies_without_dealing_damage(self):
        run, shooter, ally = self.encounter()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = shooter.team
        self.fire_once(run, shooter)
        self.assertEqual(self.health(run, ally).total, 200.0)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_actual_soldier_field_heals_friendly_pharah_but_not_enemy(self):
        for ally, expected in ((True, 151.52), (False, 100.0)):
            with self.subTest(ally=ally):
                run, pharah, soldier = self.encounter()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                soldier.team = pharah.team if ally else 1 - pharah.team
                soldier.mover.position = [0, pharah.mover.position[1], 0.5]
                soldier.mover.body = None
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is pharah)
                fight._damage(soldier, target, 100, False, None, run.frame)
                run.step({soldier: (0, 0, 16)})
                for _ in range(130):
                    run.step({})
                self.assertAlmostEqual(self.health(run, pharah).total, expected, places=5)

    def test_primary_damages_retained_training_npc_and_preserves_corpse(self):
        run, shooter, victim = self.encounter()
        victim.mover.position[0] = 20.0
        victim.mover.body = None
        hero_key, definition = next(iter(bots.table()["heroes"].items()))
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": hero_key, "team": 1, "position": (0.0, shooter.mover.position[1], 8.0),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, definition, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        self.fire_once(run, shooter)
        self.assertLess(self.health(run, bot).total, bot.health)
        self.fire_once(run, shooter)
        self.assertTrue(bot.dead)
        for _ in range(round(combat.BOT_RESPAWN / combat.TICK) + 5):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertEqual(self.health(run, bot).total, 0.0)

    def wall(self, y):
        return World([
            (-10, y - 5, 5, 10, y - 5, 5, 10, y + 5, 5),
            (-10, y - 5, 5, 10, y + 5, 5, -10, y + 5, 5),
        ])

    def test_wall_impact_splash_cannot_damage_enemy_behind_wall(self):
        run, shooter, victim = self.encounter()
        victim.mover.position[2] = 5.8  # inside blast radius, fully behind the wall
        victim.mover.body = None
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=self.wall(shooter.mover.position[1])):
            self.fire_once(run, shooter)
        self.assertEqual(self.health(run, victim).total, 200.0)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_wall_impact_still_damages_enemy_on_incoming_side(self):
        run, shooter, victim = self.encounter()
        # Off the projectile axis: hit the wall first, then splash this nearby victim.
        victim.mover.position[0] = 1.2
        victim.mover.position[2] = 4.3
        victim.mover.body = None
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=self.wall(shooter.mover.position[1])):
            self.fire_once(run, shooter)
        self.assertLess(self.health(run, victim).total, 200.0)
        self.assertGreater(self.health(run, victim).total, 120.0)


if __name__ == "__main__":
    unittest.main()
