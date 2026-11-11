"""Actual Zenyatta Discord commands and Pharah combat in a static match."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.zarya_barrier import PERSONAL


class PharahDiscordTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, zen, enemy = fixture.encounter("Zenyatta")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = zen.team = 0
        enemy.team = 1
        zen.mover.position[0] = 3
        zen.mover.body = None
        return fixture, run, pharah, zen, enemy

    def cast(self, run, zen, target):
        fight = run.match.combat
        facing = aim(fight._eye(zen), fight._eye(target))
        run.step({zen: (*facing, 8)})
        run.step({})
        return fight.discord[zen.body]

    def test_allied_discord_amplifies_actual_rocket_and_pharah_charge(self):
        fixture, run, pharah, zen, enemy = self.arena()
        orb = self.cast(run, zen, enemy)
        self.assertEqual(orb.target, enemy.body)
        fixture.fire(run, pharah)
        self.assertEqual(fixture.health(run, enemy).total, 50)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 150)
        self.assertEqual(zen.body_script.component.entity_var(342).value(), 0)

    def test_discord_applied_in_flight_amplifies_barrage_without_ult_credit(self):
        fixture, run, pharah, zen, enemy = self.arena()
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        run.step({pharah: (0, 0, 32)})
        first, = fight.rockets
        self.assertIsNone(fight.discord[zen.body].target)
        orb = self.cast(run, zen, enemy)
        self.assertEqual(orb.target, enemy.body)
        self.assertTrue(any(rocket is first for rocket in fight.rockets))
        health = fixture.health(run, enemy)
        self.assertEqual(health.total, 200)
        for _ in range(40):
            if all(rocket is not first for rocket in fight.rockets):
                break
            run.step({})
        self.assertTrue(all(rocket is not first for rocket in fight.rockets))
        self.assertEqual(health.total, 150)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(zen.body_script.component.entity_var(342).value(), 0)

    def test_enemy_discord_amplifies_damage_received_by_pharah(self):
        fixture, run, pharah, zen, enemy = self.arena()
        zen.team = enemy.team
        orb = self.cast(run, zen, pharah)
        self.assertEqual(orb.target, pharah.body)
        x, y, z = pharah.mover.position
        direction = aim(run.match.combat._eye(enemy), (x, y + 1, z))
        run.step({enemy: (*direction, 1)})
        for _ in range(20):
            run.step({})
        self.assertEqual(fixture.health(run, pharah).total, 175)

    def test_wall_rejects_application_and_expires_a_previously_cast_orb(self):
        fixture, run, pharah, zen, enemy = self.arena()
        fight = run.match.combat
        y = pharah.mover.position[1]
        wall = World([(1.5, y, 0, 1.5, y + 5, 20, 1.5, y, 20),
                      (1.5, y, 0, 1.5, y + 5, 0, 1.5, y + 5, 20)])
        with patch.object(fight, "_collision", return_value=wall):
            self.assertIsNone(self.cast(run, zen, enemy).target)
        orb = self.cast(run, zen, enemy)
        self.assertEqual(orb.target, enemy.body)
        with patch.object(fight, "_collision", return_value=wall):
            for _ in range(125):
                run.step({})
            self.assertEqual(orb.target, enemy.body)
            for _ in range(70):
                run.step({})
            self.assertIsNone(orb.target)
            fixture.fire(run, pharah)
        self.assertEqual(fixture.health(run, enemy).total, 80)

    def test_zarya_barrier_cleanses_discord_and_receives_base_rocket_damage(self):
        fixture, run, pharah, zen, enemy = self.arena()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000068]))
        enemy.mover.position = [0, pharah.mover.position[1], 12]
        enemy.mover.body = None
        for _ in range(8):
            run.step({})
        orb = self.cast(run, zen, enemy)
        self.assertEqual(orb.target, enemy.body)
        fight = run.match.combat
        run.step({enemy: (0, 0, 8)})
        for _ in range(3):
            run.step({})
        bubble = fight.zarya_bubbles[enemy.body, PERSONAL]
        self.assertIsNone(orb.target)
        while fight.now < orb.ready:
            run.step({})
        self.assertIsNone(self.cast(run, zen, enemy).target)
        before = fixture.health(run, enemy).total
        fixture.fire(run, pharah)
        self.assertEqual(bubble.health, 80)
        self.assertEqual(fixture.health(run, enemy).total, before)

    def test_discord_boosts_melee_without_adding_damage_to_concussion(self):
        fixture, run, pharah, zen, enemy = self.arena()
        enemy.mover.position[2] = 1.5
        enemy.mover.body = None
        orb = self.cast(run, zen, enemy)
        self.assertEqual(orb.target, enemy.body)
        run.step({pharah: (0, 0, 0x800)})
        for _ in range(32):
            run.step({})
        vitals = fixture.health(run, enemy)
        self.assertEqual(vitals.total, 162.5)
        old = enemy.mover.position[2]
        run.step({pharah: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertGreater(enemy.mover.position[2], old + 1)
        self.assertEqual(vitals.total, 162.5)

    def test_pharah_killing_the_orb_owner_restores_normal_incoming_damage(self):
        fixture, run, pharah, zen, enemy = self.arena()
        zen.team = enemy.team
        zen.mover.position[0] = 0
        zen.mover.body = None
        orb = self.cast(run, zen, pharah)
        self.assertEqual(orb.target, pharah.body)
        zen_health = fixture.health(run, zen)
        zen_health.damage(zen_health.total - 1)
        fixture.fire(run, pharah)
        self.assertTrue(zen_health.dead)
        self.assertNotIn(zen.body, run.match.combat.discord)
        x, y, z = pharah.mover.position
        direction = aim(run.match.combat._eye(enemy), (x, y + 1, z))
        run.step({enemy: (*direction, 1)})
        for _ in range(20):
            run.step({})
        self.assertEqual(fixture.health(run, pharah).total, 180)

    def test_two_actual_discord_casts_do_not_stack(self):
        fixture = defense.PharahDefenseTests()
        run = Run(KANEZAKA_DM, ("Pharah", "Zen A", "Zen B", "Enemy"))
        pharah, first, second, enemy = run.players
        by_name = {hero.name: hero for hero in heroes().values()}
        for player, name in ((pharah, "Pharah"), (first, "Zenyatta"), (second, "Zenyatta")):
            self.assertTrue(run.match.switch_hero(player, by_name[name]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = first.team = second.team = 0
        enemy.team = 1
        y = pharah.mover.position[1]
        for player, x, z in ((pharah, 0, 0), (first, -3, 6), (second, 3, 6), (enemy, 0, 12)):
            player.mover.position = [x, y, z]
            player.mover.body = None
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(15):
            run.step({})
        self.assertEqual(self.cast(run, first, enemy).target, enemy.body)
        self.assertEqual(self.cast(run, second, enemy).target, enemy.body)
        fixture.fire(run, pharah)
        self.assertEqual(fixture.health(run, enemy).total, 50)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 150)
