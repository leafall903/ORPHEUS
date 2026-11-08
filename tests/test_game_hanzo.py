"""Hanzo's actual command/volley damage and reconstructed ballistic collision.

These are headless server checks, not original-client or five-Talon-NPC acceptance.
"""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bodytrace, combat, movelog
from ow174.game.collision import World
from ow174.game.content import heroes

HANZO = 0x02E0000000000005


class HanzoCombatTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, distance=5):
        run = Run(KANEZAKA_DM, ("Hanzo", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, victim = run.players
        match.switch_hero(shooter, heroes()[HANZO])
        x, y, z = shooter.mover.position
        victim.mover.position = [x, y, z + distance]
        for _ in range(20):
            run.step({})
        return run, shooter, victim

    def test_full_draw_releases_one_damaging_ballistic_arrow(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
        for _ in range(70):
            run.step({shooter: (yaw, pitch, 1)})
        self.assertFalse(fight.rockets)
        run.step({shooter: (yaw, pitch, 0)})
        arrow, = fight.rockets
        self.assertAlmostEqual(arrow.gravity, 9.8, places=5)
        self.assertEqual(arrow.direct.amount, 125)
        self.assertEqual(arrow.speed, 110)
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 75)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 1)

    def test_storm_arrows_deal_authored_damage_and_kill(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        run.step({shooter: (0, 0, 0x10)})
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                run.step({shooter: (0, 0, 1)})
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [70, 70, 60])
        self.assertTrue(hits[-1]["+0x78"]["+0x8"] & combat.KILLING)
        self.assertTrue(fight.vitals[victim.body].dead)

    def test_sonic_arrow_selection_preserves_direct_damage(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        run.step({shooter: (0, 0, 0x08)})
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(70):
                run.step({shooter: (0, 0, 1)})
            run.step({})
        call, = fired.call_args_list
        self.assertEqual(call.args[3], 0x0400000000000149)
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 75)
        # Reveal and attachment remain open; selection and damage alone do not
        # establish Sonic Arrow acceptance.

    def test_storm_cooldown_rejects_old_press_and_allows_fresh_press(self):
        run, shooter, _ = self.arena()
        fight = run.match.combat
        ability = next(i for i in shooter.body_script.component.instances.values()
                       if i.graph.index == 0x146A)
        run.step({shooter: (0, 0, 0x10)})
        run.step({})
        for _ in range(140):
            run.step({shooter: (0, 0, 1)})
        run.step({})
        self.assertEqual(ability.find_var(0, 472).value(), 10)
        self.assertGreater(ability.find_var(0, 61).value(), 8)
        run.step({shooter: (0, 0, 0x10)})  # rejected early retry during cooldown
        run.step({})
        for _ in range(700):
            run.step({})
        self.assertEqual(ability.find_var(0, 61).value(), 0)
        self.assertFalse(ability.states[0].flags & 1)
        run.step({shooter: (0, 0, 0x10)})  # a fresh edge after the10s cooldown
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                run.step({shooter: (0, 0, 1)})
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)

    def test_quick_melee_changes_enemy_health_once(self):
        run, shooter, victim = self.arena(distance=2)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals[victim.body].total, 170)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 1)

    def test_melee_stops_at_wall_but_can_hit_a_target_before_it(self):
        for wall_distance, expected in ((1, 200), (3, 170)):
            with self.subTest(wall_distance=wall_distance):
                run, shooter, victim = self.arena(distance=2)
                x, y, z = shooter.mover.position
                w = z + wall_distance
                run.match.collision_world = World([
                    (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
                    (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
                    (x-10, y, w, x+10, y, w, x+10, y+5, w),
                    (x-10, y, w, x+10, y+5, w, x-10, y+5, w),
                    (x-10, y, w, x+10, y+5, w, x+10, y, w),
                    (x-10, y, w, x-10, y+5, w, x+10, y+5, w),
                ], name="explicit floor and melee wall")
                run.step({shooter: (0, 0, 0x800)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(run.match.combat.vitals[victim.body].total, expected)

    def test_enemy_player_can_damage_and_kill_hanzo(self):
        run, hanzo, enemy = self.arena()
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertEqual(fight.vitals[hanzo.body].total, 180)
        for _ in range(90):
            run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(fight.vitals[hanzo.body].dead)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(70):
                run.step({hanzo: (0, 0, 1)})
            run.step({})
        self.assertFalse(shoot.called)

    def test_ballistic_path_survives_gap_without_lifetime_extension(self):
        results = []
        for advances in (range(1, 11), (10,)):
            run, shooter, victim = self.arena()
            victim.mover.position[2] += 100
            fight = run.match.combat
            start = fight._eye(shooter)
            arrow = combat.Rocket(shooter, shooter.team, start, (0, 0, 1), 25,
                                  0.1, combat.HitDef(125), None,
                                  last_tick=run.frame, gravity=9.8)
            fight.rockets.append(arrow)
            for offset in advances:
                tick = run.frame + offset
                run.match.update(tick * combat.TICK, tick)
            self.assertFalse(fight.rockets)
            self.assertAlmostEqual(arrow.position[1], start[1] - 0.5 * 9.8 * 0.1**2)
            self.assertAlmostEqual(arrow.position[2], start[2] + 2.5)
            self.assertAlmostEqual(arrow.flown, 0.1)
            results.append(arrow.position)
        for a, b in zip(*results, strict=True):
            self.assertAlmostEqual(a, b)

    def test_low_speed_arrow_drops_under_target_instead_of_hitting_straight_ahead(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        arrow = combat.Rocket(shooter, shooter.team, fight._eye(shooter), (0, 0, 1),
                              5, 2, combat.HitDef(125), None,
                              last_tick=run.frame, gravity=9.8)
        fight.rockets.append(arrow)
        for _ in range(80):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 200)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)  # intercepted by the collision floor

    def test_projectile_critical_multiplier_uses_impact_height(self):
        # Explicit definitions exercise shared semantics. Hanzo's null multiplier
        # remains unresolved; this test does not invent its native default.
        for height, multiplier, expected, critical in (
            (1.55, 2, 50, True), (0.8, 2, 125, False), (1.55, 1, 125, False),
        ):
            with self.subTest(height=height, multiplier=multiplier):
                run, shooter, victim = self.arena()
                fight = run.match.combat
                x, y, z = shooter.mover.position
                arrow = combat.Rocket(shooter, shooter.team, (x, y + height, z),
                                      (0, 0, 1), 100, 1,
                                      combat.HitDef(75, critical=multiplier), None)
                fight.rockets.append(arrow)
                for _ in range(5):
                    run.step({})
                self.assertEqual(fight.vitals[victim.body].total, expected)
                hit, = shooter.client.messages(combat.HIT)
                self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), critical)
