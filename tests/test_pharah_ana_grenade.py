"""Actual Ana grenade and Soldier field commands against Pharah."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense
from test_game_combat import aim
from test_game_projectiles import ABILITY_2

from ow174.game import projectiles, zarya_barrier
from ow174.game.collision import World
from ow174.game.combat import TICK
from ow174.game.content import heroes


class PharahGrenadeTests(unittest.TestCase):
    def arena(self, *, friendly=False):
        fixture = defense.PharahDefenseTests()
        run, pharah, ana, soldier = fixture.encounter("Ana")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = soldier.team = 0
        ana.team = 0 if friendly else 1
        y = pharah.mover.position[1]
        soldier.mover.position = [1.5, y, 0]
        soldier.mover.body = None
        return fixture, run, pharah, ana, soldier

    def throw(self, run, ana, pharah):
        fight = run.match.combat
        vitals = fight.vitals.get(pharah.body)
        previous_end = vitals.healing_blocks[-1][1] if vitals and vitals.healing_blocks else 0
        yaw, pitch = aim(fight._eye(ana), fight._eye(pharah))
        run.step({ana: (yaw, pitch, ABILITY_2)})
        for _ in range(60):
            run.step({ana: (yaw, pitch, 0)})
            vitals = fight.vitals.get(pharah.body)
            if vitals and vitals.healing_blocks and vitals.healing_blocks[-1][1] > previous_end:
                break

    def test_actual_field_cannot_heal_until_grenade_expires(self):
        fixture, run, pharah, ana, soldier = self.arena()
        self.throw(run, ana, pharah)
        vitals = fixture.health(run, pharah)
        self.assertEqual(vitals.total, 150)
        (start, end), = vitals.healing_blocks
        self.assertEqual(end - start, 250)
        run.step({soldier: (0, 0, ABILITY_2)})
        fight = run.match.combat
        for _ in range(75):
            run.step({})
            if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                break
        self.assertTrue(any(shot.field_tick is not None for shot in fight.projectiles.shots))
        while run.frame < end - 1:
            run.step({})
        self.assertEqual(vitals.total, 150)
        run.step({})
        self.assertAlmostEqual(vitals.total, 150 + projectiles.HEAL_PER_SECOND * TICK)

    def test_batched_field_crossing_expiry_heals_only_unblocked_ticks(self):
        fixture, run, pharah, ana, soldier = self.arena()
        self.throw(run, ana, pharah)
        vitals = fixture.health(run, pharah)
        (_, end), = vitals.healing_blocks
        run.step({soldier: (0, 0, ABILITY_2)})
        while run.frame < end - 3:
            run.step({})
        self.assertEqual(vitals.total, 150)
        run.frame = end + 2
        run.match.update(run.frame * TICK, run.frame)
        self.assertAlmostEqual(vitals.total, 150 + 3 * projectiles.HEAL_PER_SECOND * TICK)

    def test_allies_and_wall_occluded_targets_do_not_receive_antiheal(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                fixture, run, pharah, ana, _ = self.arena(friendly=friendly)
                fight = run.match.combat
                y = pharah.mover.position[1]
                wall = World([(-4, y, 2, -4, y + 5, 2, 4, y + 5, 2),
                              (-4, y, 2, 4, y + 5, 2, 4, y, 2)])
                with patch.object(fight, "_collision", return_value=fight._collision() if friendly else wall):
                    self.throw(run, ana, pharah)
                vitals = fixture.health(run, pharah)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(vitals.healing_blocks)

    def test_projected_zarya_barrier_blocks_grenade_damage_and_status(self):
        fixture, run, pharah, ana, zarya = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(zarya, hero))
        zarya.mover.position = [4.5, pharah.mover.position[1], 0]
        zarya.mover.body = None
        run.step({zarya: (-16384, 1000, ABILITY_2)})
        for _ in range(3):
            run.step({})
        bubble = run.match.combat.zarya_bubbles[zarya.body, zarya_barrier.PROJECTED]
        self.assertIs(bubble.target, pharah)
        self.throw(run, ana, pharah)
        vitals = fixture.health(run, pharah)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(vitals.healing_blocks)
        self.assertEqual(bubble.health, 150)

    def test_second_ana_refreshes_the_continuous_block(self):
        fixture, run, pharah, ana, second = self.arena()
        self.assertTrue(run.match.switch_hero(second, ana.hero))
        second.team = ana.team
        second.mover.position = [2, pharah.mover.position[1], 8]
        second.mover.body = None
        self.throw(run, ana, pharah)
        vitals = fixture.health(run, pharah)
        (start, end), = vitals.healing_blocks
        for _ in range(25):
            run.step({})
        self.throw(run, second, pharah)
        (refreshed_start, refreshed_end), = vitals.healing_blocks
        self.assertEqual(refreshed_start, start)
        self.assertGreater(refreshed_end, end)
        self.assertEqual(refreshed_end - run.frame, 250)
        self.assertEqual(vitals.total, 100)

    def test_friendly_grenade_heals_then_amplifies_actual_field(self):
        fixture, run, pharah, ana, soldier = self.arena(friendly=True)
        vitals = fixture.health(run, pharah)
        vitals.damage(190)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ana), fight._eye(pharah))
        run.step({ana: (yaw, pitch, ABILITY_2)})
        for _ in range(65):
            if vitals.healing_boosts:
                break
            run.step({ana: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 110)
        (start, end), = vitals.healing_boosts
        self.assertEqual(end - start, 250)
        run.step({soldier: (0, 0, ABILITY_2)})
        for _ in range(75):
            run.step({})
            if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                break
        self.assertTrue(any(shot.field_tick is not None for shot in fight.projectiles.shots))
        run.step({})
        self.assertAlmostEqual(vitals.total, 110 + 1.5 * projectiles.HEAL_PER_SECOND * TICK)
        while run.frame < end - 2:
            run.step({})
        vitals.damage(vitals.total - 10)
        run.frame = end + 1
        run.match.update(run.frame * TICK, run.frame)
        self.assertAlmostEqual(vitals.total, 10 + 3.5 * projectiles.HEAL_PER_SECOND * TICK)
