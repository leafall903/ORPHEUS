"""Actual Transcendence against Pharah damage and displacement."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_ana_grenade as grenade
import test_pharah_defenses as defense

from ow174.game import defenses
from ow174.game.collision import World
from ow174.game.combat import TICK
from ow174.game.content import heroes


class PharahTranscendenceTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, shooter, zen, _ = fixture.encounter("Zenyatta")
        self.cast(run, zen)
        return fixture, run, shooter, zen

    def cast(self, run, zen):
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (-32768, 0, 32)})
        for _ in range(15):
            if defenses.transcendence(zen):
                break
            run.step({})
        self.assertEqual(defenses.phase(zen), (True, False))

    def test_actual_primary_and_melee_deal_no_damage_until_expiry(self):
        fixture, run, shooter, zen = self.arena()
        fixture.fire(run, shooter)
        self.assertEqual(fixture.health(run, zen).total, 200)
        self.assertFalse(shooter.client.messages(25001))
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        shooter.mover.position[2] = 6.4
        shooter.mover.body = None
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(fixture.health(run, zen).total, 200)
        for _ in range(420):
            if not defenses.transcendence(zen):
                break
            run.step({})
        self.assertFalse(defenses.transcendence(zen))
        fixture.fire(run, shooter)
        self.assertLess(fixture.health(run, zen).total, 200)

    def test_actual_concussion_displaces_without_damage(self):
        fixture, run, shooter, zen = self.arena()
        start = tuple(zen.mover.position)
        run.step({shooter: (0, 0, 16)})
        for _ in range(45):
            run.step({})
        self.assertTrue(defenses.transcendence(zen))
        self.assertNotEqual(tuple(zen.mover.position), start)
        self.assertEqual(fixture.health(run, zen).total, 200)

    def test_barrage_hits_solid_immune_body_then_damages_after_expiry(self):
        fixture, run, shooter, zen = self.arena()
        for _ in range(280):
            run.step({})
        self.assertTrue(defenses.transcendence(zen))
        rear = next(player for player in run.players if player not in (shooter, zen))
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        zen.mover.position[2] = 4
        zen.mover.body = None
        yaw, pitch = fixture.aim_at_body(fight, shooter, zen)
        run.step({shooter: (yaw, pitch, 32)})
        first, = fight.rockets
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(35):
                if all(rocket is not first for rocket in fight.rockets):
                    break
                run.step({})
        self.assertTrue(any(call.args[1].owner is zen and call.args[2] == 10
                            for call in damage.call_args_list))
        self.assertTrue(all(rocket is not first for rocket in fight.rockets))
        self.assertTrue(defenses.transcendence(zen))
        self.assertEqual(fixture.health(run, zen).total, 200)
        self.assertEqual(fixture.health(run, rear).total, 200)
        self.assertFalse(shooter.client.messages(25001))

        for _ in range(150):
            run.step({})
            if fixture.health(run, zen).total < 200:
                break
        self.assertFalse(defenses.transcendence(zen))
        self.assertEqual(fixture.health(run, zen).total, 160)
        self.assertEqual(fixture.health(run, rear).total, 200)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_q_restores_zen_and_heals_only_visible_nearby_allies(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, zen, enemy = fixture.encounter("Zenyatta")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = zen.team = 0
        enemy.team = 1
        own = fixture.health(run, zen)
        ally = fixture.health(run, pharah)
        opposing = fixture.health(run, enemy)
        for vitals in (own, ally, opposing):
            vitals.damage(150)
        self.cast(run, zen)
        run.step({})
        self.assertEqual(own.total, 200)
        self.assertAlmostEqual(ally.total, 50 + 300 * TICK)
        self.assertEqual(opposing.total, 50)
        tick = run.frame
        run.match.update(tick * TICK, tick)
        self.assertAlmostEqual(ally.total, 50 + 300 * TICK)
        y = pharah.mover.position[1]
        wall = World([(-5, y, 4, -5, y + 5, 4, 5, y + 5, 4),
                      (-5, y, 4, 5, y + 5, 4, 5, y, 4)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            run.step({})
        self.assertAlmostEqual(ally.total, 50 + 300 * TICK)
        pharah.mover.position[2] = -3
        pharah.mover.body = None
        run.step({})
        self.assertAlmostEqual(ally.total, 50 + 300 * TICK)

    def test_actual_enemy_grenade_blocks_aura_until_antiheal_expires(self):
        helper = grenade.PharahGrenadeTests()
        fixture, run, pharah, ana, zen = helper.arena()
        self.assertTrue(run.match.switch_hero(zen, heroes()[0x02E0000000000020]))
        zen.mover.position = [1.5, pharah.mover.position[1], 0]
        zen.mover.body = None
        helper.throw(run, ana, pharah)
        ally = fixture.health(run, pharah)
        own = fixture.health(run, zen)
        self.assertEqual(ally.total, 150)
        self.assertTrue(own.healing_blocks)
        self.cast(run, zen)
        run.step({})
        self.assertEqual(own.total, 200)
        self.assertFalse(own.healing_blocks)
        (_, end), = ally.healing_blocks
        while run.frame < end - 1:
            run.step({})
        self.assertEqual(ally.total, 150)
        run.step({})
        self.assertAlmostEqual(ally.total, 150 + 300 * TICK)

    def test_hostile_winston_barrier_occludes_ally_healing(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, zen, winston = fixture.encounter("Zenyatta")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = zen.team = 0
        winston.team = 1
        hero = next(hero for hero in heroes().values() if hero.name == "Winston")
        self.assertTrue(run.match.switch_hero(winston, hero))
        winston.mover.position = [2, pharah.mover.position[1], 0]
        winston.mover.body = None
        run.step({winston: (0, 0, 16)})
        for _ in range(5):
            run.step({})
        self.assertIn(winston.body, run.match.combat.winston_bubbles)
        ally = fixture.health(run, pharah)
        ally.damage(150)
        self.cast(run, zen)
        run.step({})
        self.assertEqual(ally.total, 50)
        previous = winston.body
        self.assertTrue(run.match.switch_hero(winston, pharah.hero))
        self.assertNotIn(previous, run.match.combat.winston_bubbles)
        run.step({})
        self.assertAlmostEqual(ally.total, 50 + 300 * TICK)
