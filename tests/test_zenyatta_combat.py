"""Actual extracted Zenyatta weapon execution; offline evidence, not client acceptance."""

import unittest

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL

ZENYATTA = 0x02E0000000000020


class ZenyattaCombatTests(unittest.TestCase):
    def arena(self):
        run = Run(KANEZAKA_DM, ("Zenyatta", "Enemy"))
        zen, enemy = run.players
        run.match.bots.clear()
        run.match.switch_hero(zen, heroes()[ZENYATTA])
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 8.0]
        run.step({})  # establish the world clock before any death schedules a respawn
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        return run, zen, enemy, fight.vitals_of(target)

    def test_primary_head_and_body_damage_use_actual_weapon_and_hit_notices(self):
        for pitch, damage, flags in ((0, 96, combat.CRITICAL), (600, 48, 0)):
            with self.subTest(pitch=pitch):
                run, zen, _, vitals = self.arena()
                run.step({zen: (0, pitch, FIRE)})
                for _ in range(15):
                    run.step({})
                self.assertEqual(vitals.total, 200 - damage)
                hits = zen.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], damage)
                self.assertEqual(hits[0]["+0x78"]["+0x8"], flags)

    def test_full_secondary_emits_five_independently_damaging_orbs(self):
        run, zen, enemy, _ = self.arena()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        self.assertEqual(vitals.total, 1000)
        for _ in range(60):
            run.step({zen: (0, 600, 0)})
        self.assertEqual(vitals.total, 760)
        self.assertEqual(len(zen.client.messages(combat.HIT)), 5)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 15)

    def test_primary_miss_spends_ammo_without_damage(self):
        run, zen, _, vitals = self.arena()
        run.step({zen: (16384, 0, FIRE)})
        for _ in range(15):
            run.step({})
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_range_limit_retires_projectile_before_far_target(self):
        run, zen, enemy, vitals = self.arena()
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 182]
        enemy.mover.body = None  # recreate the physics capsule at the test's new feet
        run.step({zen: (0, 0, FIRE)})
        for _ in range(140):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_does_not_damage_teammate(self):
        run, zen, enemy, vitals = self.arena()
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        enemy.team = zen.team
        run.step({zen: (0, 0, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_stops_at_actual_static_collision(self):
        run, zen, _, vitals = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y, z+4, x+5, y, z+4, x+5, y+5, z+4),
            (x-5, y, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 0, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_repeated_presses_cannot_bypass_primary_fire_interval(self):
        ammo = []
        for pulse in (False, True):
            run, zen, _, _ = self.arena()
            for i in range(40):
                run.step({zen: (16384, 0, FIRE if not pulse or i % 2 == 0 else 0)})
            weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
            ammo.append(weapon.vars[53].value())
        self.assertEqual(ammo, [18, 18])

    def test_reload_has_pending_window_and_restores_actual_twenty_round_magazine(self):
        run, zen, _, _ = self.arena()
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        for _ in range(40):
            run.step({zen: (16384, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 18)
        for i in range(30):
            run.step({zen: (16384, 0, 0x400 if i == 0 else FIRE)})
        self.assertEqual(weapon.vars[53].value(), 18)
        for _ in range(140):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 20)
        run.step({zen: (16384, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 19)

    def test_enemy_fire_drains_zen_shields_before_health(self):
        run, zen, enemy, _ = self.arena()
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        vitals = run.match.combat.vitals_of(target)
        run.step({enemy: (-32768, 0, FIRE)})
        self.assertEqual(vitals.now[SHIELDS_POOL], 110)
        self.assertEqual(vitals.now[HEALTH_POOL], 50)
        self.assertEqual(zen.body_script.component.owner_dead, False)

    def test_dead_zen_cannot_emit_weapon_damage(self):
        run, zen, enemy, vitals = self.arena()
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        run.match.combat._damage(enemy, target, 500, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(zen))
        for _ in range(25):
            run.step({zen: (0, 600, FIRE)})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_quick_melee_deals_thirty_and_does_not_headshot(self):
        run, zen, enemy, vitals = self.arena()
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        enemy.mover.body = None
        for i in range(30):
            run.step({zen: (0, 0, 0x800 if i == 0 else 0)})
        self.assertEqual(vitals.total, 170)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], 0)

    def test_three_primary_headshots_kill_and_emit_killing_notice(self):
        run, zen, _, vitals = self.arena()
        for _ in range(65):
            run.step({zen: (0, 0, FIRE)})
        self.assertTrue(vitals.dead)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [96, 96, 8])
        self.assertEqual(hits[-1]["+0x78"]["+0x8"], combat.CRITICAL | combat.KILLING)

    def test_explicit_noncritical_projectile_stays_noncritical_at_head_height(self):
        run, zen, _, vitals = self.arena()
        fight = run.match.combat
        fight.rockets.append(combat.Rocket(
            zen, zen.team, fight._eye(zen), (0, 0, 1), 90, 1,
            combat.HitDef(40, critical=1), None,
        ))
        for _ in range(15):
            run.step({})
        self.assertEqual(vitals.total, 160)
        self.assertEqual(zen.client.messages(combat.HIT)[0]["+0x78"]["+0x8"], 0)

    def test_direct_projectile_falloff_precedes_armour_and_splash_does_not_crit(self):
        run, zen, enemy, _ = self.arena()
        fight = run.match.combat
        vitals = combat.Vitals(enemy.body, 200, armour=100)
        fight.vitals[enemy.body] = vitals
        rocket = combat.Rocket(
            zen, zen.team, fight._eye(zen), (0, 0, 1), 90, 1,
            combat.HitDef(48, falloff_start=0, falloff_end=1, falloff_floor=0.5, critical=2),
            combat.HitDef(10, critical=2, rings=((1, 1),)),
        )
        fight.rockets.append(rocket)
        for _ in range(15):
            run.step({})
        # Direct: 48 * .5 * 2 - 5 armour. Splash: 10 - 5 armour.
        self.assertEqual(vitals.total, 252)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [43, 5])
        self.assertEqual([h["+0x78"]["+0x8"] for h in hits], [combat.CRITICAL, 0])


if __name__ == "__main__":
    unittest.main()
