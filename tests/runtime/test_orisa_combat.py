"""Reconstructed Orisa combat consumers in the real Match/BodyScript path.

These checks do not establish original-client presentation or Talon NPC acceptance.
"""

import math
import unittest

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots, combat, weapon_spread
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero, heroes
from ow174.game.world import ARMOUR_POOL, SHIELDS_POOL

ORISA = 0x02E000000000013E


class OrisaCombatTests(unittest.TestCase):
    def arena(self):
        run = Run(KANEZAKA_DM, ("Orisa", "Enemy"))
        a, b = run.players
        run.match.switch_hero(a, heroes()[ORISA])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = a.mover.position
        b.mover.position = [x, y, z + 5]
        run.step({})
        return run, a, b

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def wall(self, run, a, ahead):
        x, y, z = a.mover.position
        floor = [
            (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
            (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
        ]
        wall = [
            (x - 2, y, z + ahead, x + 2, y, z + ahead, x + 2, y + 5, z + ahead),
            (x - 2, y, z + ahead, x + 2, y + 5, z + ahead, x - 2, y + 5, z + ahead),
        ]
        run.match.collision_world = World(floor + wall)

    def test_fusion_driver_command_flies_and_damages_enemy_player(self):
        run, a, b = self.arena()
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, b))
        run.step({a: (0, 0, FIRE)})
        self.assertTrue(fight.rockets)
        self.assertEqual(vitals.total, 200)
        for _ in range(5):
            run.step({})
        self.assertEqual(vitals.total, 178)  # head height, 11 * 2
        self.assertTrue(a.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)
        rifle = next(i for i in a.body_script.component.instances.values() if i.graph.index == 0xBD4)
        self.assertEqual(rifle.vars[53].value(), 149)

    def test_fusion_driver_uses_emitted_index_for_the_first_five_projectiles(self):
        run, player, enemy = self.arena()
        enemy.spawned = False
        emitted = {}
        for _ in range(35):
            run.step({player: (0, 0, FIRE)})
            for rocket in run.match.combat.rockets:
                emitted[id(rocket)] = rocket
            if len(emitted) >= 5:
                break
        self.assertEqual(len(emitted), 5)
        volley = next(iter(emitted.values())).source_state
        self.assertEqual(volley.spread_ramp, 4)
        base = weapon_spread.seed_base(player.body, volley.instance.id, volley.node.pos, volley.counter)
        for index, (rocket, degrees) in enumerate(zip(emitted.values(),
                                                       (.25, .4875, .725, .9625, 1.2), strict=True)):
            with self.subTest(index=index):
                expected = weapon_spread.direction((0, 0, 1), math.radians(degrees), base, index)
                for axis, value in zip(rocket.launch_direction, expected, strict=True):
                    self.assertAlmostEqual(axis, value, places=6)
        self.assertEqual(volley.instance.vars[53].value(), 145)

    def test_fusion_driver_command_can_kill_a_retained_training_bot(self):
        run, a, b = self.arena()
        b.spawned = False
        x, y, z = a.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": a.team + 1,
            "position": (x, y, z + 5), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training Bot", "body": "0x0400000000001403", "health": 10}, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        yaw, pitch = aim(run.match.combat._eye(a), (x, y + 0.7, z + 5))
        run.step({a: (yaw, pitch, FIRE)})
        for _ in range(5):
            run.step({})
        self.assertTrue(bot.dead)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
        for _ in range(200):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, bot)).total, 0)

    def test_fusion_driver_command_respects_hero_armour_and_shield_pools(self):
        for hero_name, pool, expected_loss in (("Orisa", ARMOUR_POOL, 17), ("Zarya", SHIELDS_POOL, 22)):
            with self.subTest(hero=hero_name):
                run, a, b = self.arena()
                run.match.switch_hero(b, find_hero(hero_name))
                fight, target = run.match.combat, self.target(run, b)
                vitals = fight.vitals_of(target)
                before = dict(vitals.now)
                yaw, pitch = aim(fight._eye(a), fight._eye(b))
                run.step({a: (yaw, pitch, FIRE)})
                for _ in range(5):
                    run.step({})
                self.assertEqual(before[pool] - vitals.now[pool], expected_loss)
                self.assertEqual(sum(before.values()) - vitals.total, expected_loss)

    def test_orisa_quick_melee_command_damages_enemy_once(self):
        run, a, b = self.arena()
        b.spawned = False
        x, y, z = a.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": a.team + 1,
            "position": (x, y, z + 1.5), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training Bot", "body": "0x0400000000001403", "health": 200}, {})
        run.match.bots.append(bot)
        run.step({a: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(self.target(run, bot)).total, 170)

    def test_fortify_command_reduces_actual_damage_and_expires(self):
        run, a, b = self.arena()
        fight, target = run.match.combat, self.target(run, a)
        vitals = fight.vitals_of(target)
        run.step({a: (0, 0, 8)})
        self.assertAlmostEqual(fight._fortify_reduction(target), 0.6)
        fight._damage(b, target, 100, False, None, run.frame)
        self.assertAlmostEqual(vitals.total, 395, places=4)  # 100 * .6 - 5 armour
        for _ in range(270):
            run.step({})
        self.assertIsNone(fight._fortify_reduction(target))
        fight._damage(b, target, 100, False, None, run.frame)
        self.assertAlmostEqual(vitals.total, 300, places=4)

    def test_fortify_suppresses_direct_critical_damage_and_hit_flag(self):
        run, a, b = self.arena()
        run.step({a: (0, 0, 8)})
        fight, target = run.match.combat, self.target(run, a)
        vitals = fight.vitals_of(target)
        fight._direct_hit(b, target, combat.HitDef(100, critical=2), 5, True, run.frame)
        self.assertAlmostEqual(vitals.total, 395, places=4)
        self.assertFalse(b.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)

    def test_npc_damage_uses_same_fortify_armour_and_death_path(self):
        run, a, _ = self.arena()
        x, y, z = a.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": a.team + 1,
            "position": (x, y, z + 5), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training Bot", "body": "0x0400000000001403", "health": 200}, {})
        run.match.bots.append(bot)
        run.step({a: (0, 0, 8)})
        fight, target = run.match.combat, self.target(run, a)
        fight._damage(bot, target, 100, False, None, run.frame)
        self.assertAlmostEqual(fight.vitals_of(target).total, 395, places=4)
        fight._damage(bot, target, 2000, False, None, run.frame)
        self.assertTrue(fight.is_dead(a))
        self.assertEqual(fight.vitals_of(target).killer, bot.entity)

    def test_fortify_does_not_prevent_ally_healing_or_restore_dead_bodies(self):
        run, a, b = self.arena()
        b.team = a.team
        fight, target = run.match.combat, self.target(run, a)
        vitals = fight.vitals_of(target)
        run.step({a: (0, 0, 8)})
        fight._damage(b, target, 100, False, None, run.frame)
        fight.heal_area(a, tuple(a.mover.position), 1, 30)
        self.assertAlmostEqual(vitals.total, 425, places=4)
        fight._damage(b, target, 2000, False, None, run.frame)
        fight.heal_area(a, tuple(a.mover.position), 1, 2000)
        self.assertEqual(vitals.total, 0)

    def test_projectile_direct_falloff_and_critical_do_not_multiply_splash(self):
        run, a, b = self.arena()
        fight, target = run.match.combat, self.target(run, b)
        vitals = fight.vitals_of(target)
        direct = combat.HitDef(20, falloff_start=0, falloff_end=4, falloff_floor=0.5, critical=2)
        blast = combat.HitDef(30, critical=3, rings=((10, 1),))
        rocket = combat.Rocket(a, a.team, fight._eye(a), (0, 0, 1), 120, 1, direct, blast)
        fight.rockets.append(rocket)
        for _ in range(4):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(vitals.total, 150)  # 20 * .5 * 2 + 30

    def test_fortify_retirement_on_hero_switch_does_not_leak(self):
        run, a, b = self.arena()
        run.step({a: (0, 0, 8)})
        run.match.switch_hero(a, b.hero)
        fight, target = run.match.combat, self.target(run, a)
        self.assertIsNone(fight._fortify_reduction(target))
        fight._damage(b, target, 20, False, None, run.frame)
        self.assertEqual(fight.vitals_of(target).total, 180)

    def test_fortify_cooldown_rejects_repeated_presses_then_recovers(self):
        run, a, _ = self.arena()
        target = self.target(run, a)
        ability = a.body_script.component.instances[6].state(0)
        run.step({a: (0, 0, 8)})
        for _ in range(250):
            run.step({})
        self.assertIsNone(run.match.combat._fortify_reduction(target))
        self.assertGreater(ability.remaining(), 9)
        for _ in range(5):
            run.step({a: (0, 0, 8)})
            run.step({})
            self.assertIsNone(run.match.combat._fortify_reduction(target))
        for _ in range(640):
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({a: (0, 0, 8)})
        self.assertAlmostEqual(run.match.combat._fortify_reduction(target), 0.6)

    def test_death_interrupts_fortify_and_rejects_new_ability_presses(self):
        run, a, b = self.arena()
        fight, target = run.match.combat, self.target(run, a)
        run.step({a: (0, 0, 8)})
        fight._damage(b, target, 2000, False, None, run.frame)
        run.step({})
        self.assertIsNone(fight._fortify_reduction(target))
        run.step({a: (0, 0, 8)})
        self.assertIsNone(fight._fortify_reduction(target))
        self.assertEqual(fight.vitals_of(target).total, 0)

    def test_fusion_driver_miss_spends_ammo_and_reload_restores_magazine(self):
        run, a, b = self.arena()
        rifle = a.body_script.component.instances[11]
        for _ in range(30):
            run.step({a: (16384, 0, FIRE)})
        run.step({})
        self.assertLess(rifle.vars[53].value(), 150)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, b)).total, 200)
        run.step({a: (0, 0, 0x400)})  # graph0BD4 logical14: reload
        for _ in range(250):
            run.step({})
        self.assertEqual(rifle.vars[53].value(), 150)

    def test_supercharger_charge_gate_and_cast_reaches_authored_volley(self):
        run, a, _ = self.arena()
        script = a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        run.step({a: (0, 0, 32)})
        for _ in range(40):
            run.step({})
        volley = script.component.instances[7].state(10)
        self.assertEqual(volley.counter, 0)
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({a: (0, 0, 32)})
        for _ in range(40):
            run.step({})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(volley.counter, 1)
        self.assertEqual(volley.reported, 1)
        self.assertTrue(any(rocket.source_graph == 0xCF5 and rocket.ended == "world"
                            for rocket in run.match.combat.ability_impacts))

    def test_fusion_driver_empty_magazine_auto_reloads_while_fire_is_held(self):
        run, a, b = self.arena()
        b.spawned = False
        rifle = a.body_script.component.instances[11]
        for _ in range(1050):
            run.step({a: (16384, 0, FIRE)})
        self.assertGreater(rifle.state(19).counter, 0)
        self.assertGreater(rifle.vars[53].value(), 0)
        self.assertLess(rifle.vars[53].value(), 150)

    def test_body_health_query_follows_damage_healing_and_death(self):
        run, a, b = self.arena()
        fight, target = run.match.combat, self.target(run, a)
        instance = a.body_script.component.instances[7]
        cfg = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(instance.evaluate(cfg), 450)
        fight._damage(b, target, 100, False, None, run.frame)
        self.assertEqual(instance.evaluate(cfg), 355)
        fight.heal_area(a, a.mover.position, 1, 40)
        self.assertEqual(instance.evaluate(cfg), 395)
        fight._damage(b, target, 2000, False, None, run.frame)
        self.assertEqual(instance.evaluate(cfg), 0)

    def test_death_during_supercharger_windup_prevents_its_volley(self):
        run, a, b = self.arena()
        script = a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({a: (0, 0, 32)})
        for _ in range(10):
            run.step({})
        fight = run.match.combat
        fight._damage(b, self.target(run, a), 2000, False, None, run.frame)
        for _ in range(40):
            run.step({})
        self.assertEqual(script.component.instances[7].state(10).counter, 0)
        self.assertFalse(fight.rockets)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_fusion_driver_leaves_friendly_target_unharmed(self):
        run, a, b = self.arena()
        run.match.game_map = KANEZAKA_DM.__class__(**{**KANEZAKA_DM.__dict__, "free_for_all": False})
        b.team = a.team
        run.step({a: (0, 0, FIRE)})
        for _ in range(5):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(self.target(run, b)).total, 200)

    def test_fusion_driver_static_wall_stops_projectile_without_damage(self):
        run, a, b = self.arena()
        self.wall(run, a, 2.5)
        run.step({a: (0, 0, FIRE)})
        for _ in range(5):
            run.step({})
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, b)).total, 200)
        self.assertFalse(a.client.messages(combat.HIT))

    def test_quick_melee_does_not_hit_through_static_wall(self):
        run, a, b = self.arena()
        b.spawned = False
        x, y, z = a.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": a.team + 1,
            "position": (x, y, z + 1.5), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training Bot", "body": "0x0400000000001403", "health": 200}, {})
        run.match.bots.append(bot)
        self.wall(run, a, 0.75)
        run.step({a: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(self.target(run, bot)).total, 200)


if __name__ == "__main__":
    unittest.main()
