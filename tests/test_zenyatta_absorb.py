"""Actual Sigma Shift and Zen inputs, with explicit reconstructed grasp hosting."""

import struct
import unittest

import test_zenyatta_combat as weapon_tests

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import graph
from ow174.game.world import POOLS, SHIELDS_POOL


class ZenyattaAbsorbTests(unittest.TestCase):
    def arena(self, yaw=-32768, start=True):
        run, zen, sigma, _ = weapon_tests.ZenyattaCombatTests().arena()
        hero = int(graph.bodies()[0x0400000000002AB1]['hero'], 16)
        run.match.switch_hero(sigma, heroes()[hero])
        x, y, z = zen.mover.position
        sigma.mover.position = [x, y, z+8]
        sigma.mover.body = None
        if start:
            for _ in range(10):
                run.step({sigma: (yaw, 0, 0x08)})
            self.assertTrue(self.active(sigma))
        return run, zen, sigma

    def instance(self, sigma):
        return next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)

    def active(self, sigma):
        return any(s.active and s.node.cls == 'STUStatescriptStateDeflectProjectiles'
                   for s in self.instance(sigma).states.values())

    def vitals(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target)

    def test_front_absorbs_actual_orb_and_resource_sum_without_hit_or_reflection(self):
        run, zen, sigma = self.arena()
        charge = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(30):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertEqual(self.vitals(run, zen).total, 200)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(sigma.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-charge,
                               (run.frame-frame)*combat.TICK*5, places=4)

    def test_back_is_not_protected_and_grants_no_extra_shields(self):
        run, zen, sigma = self.arena(yaw=0)
        run.step({zen: (0, 600, FIRE)})
        for _ in range(160):
            run.step({})
        self.assertFalse(self.active(sigma))
        self.assertEqual(self.vitals(run, sigma).total, 352)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)

    def test_full_charged_burst_is_consumed_and_converts_to_separate_shields_at_native_end(self):
        run, zen, sigma = self.grant_burst()
        self.assertFalse(zen.client.messages(combat.HIT))
        parts = self.vitals(run, sigma).component((SHIELDS_POOL,))[51]
        pool = parts[SHIELDS_POOL*POOLS+1]
        self.assertEqual(struct.unpack('<IffI', pool[:16]), (4, 144, 144, SHIELDS_POOL))
        self.assertEqual(pool[17], 0)  # explicit reconstructed over-health classification

    def grant_burst(self):
        run, zen, sigma = self.arena(start=False)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        for _ in range(10):
            run.step({zen: (0, 600, SECONDARY), sigma: (-32768, 0, 0x08)})
        self.assertTrue(self.active(sigma))
        for _ in range(60):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 240)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
        for _ in range(80):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(self.active(sigma))
        self.assertAlmostEqual(self.vitals(run, sigma).bonus_shields, 144, places=4)
        self.assertAlmostEqual(self.vitals(run, sigma).total, 544, places=4)
        return run, zen, sigma

    def test_extra_shields_take_orb_damage_first_are_unhealable_and_decay_after_authored_wait(self):
        run, zen, sigma = self.grant_burst()
        vitals = self.vitals(run, sigma)
        normal = dict(vitals.now)
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(20):
            run.step({sigma: (-32768, 0, 0)})
        self.assertAlmostEqual(vitals.bonus_shields, 96, places=4)
        self.assertEqual(vitals.now, normal)
        run.match.combat.heal_area(sigma, run.match.combat._eye(sigma), 10, 1000)
        self.assertAlmostEqual(vitals.bonus_shields, 96, places=4)
        decay = None
        for _ in range(250):
            decay = next((s for s in self.instance(sigma).states.values()
                          if s.node.pos == 70 and s.active), None)
            if decay is not None:
                break
            self.assertAlmostEqual(vitals.bonus_shields, 96, places=4)
            run.step({sigma: (-32768, 0, 0)})
        self.assertIsNotNone(decay)
        run.step({sigma: (-32768, 0, 0)})  # account for the native sub-frame boundary first
        before = vitals.bonus_shields
        for _ in range(60):
            run.step({sigma: (-32768, 0, 0)})
        self.assertAlmostEqual(before-vitals.bonus_shields, 4.8, places=3)
        self.assertEqual(vitals.now, normal)

    def test_three_actual_zen_bursts_reach_authored_four_hundred_extra_shield_cap(self):
        run = weapon_tests.Run(PRACTICE_RANGE, ('Zen1', 'Zen2', 'Zen3', 'Sigma'))
        zens, sigma = run.players[:3], run.players[3]
        run.match.bots.clear()
        for zen in zens:
            run.match.switch_hero(zen, heroes()[weapon_tests.ZENYATTA])
            zen.team = 0
        hero = int(graph.bodies()[0x0400000000002AB1]['hero'], 16)
        run.match.switch_hero(sigma, heroes()[hero])
        sigma.team = 1
        x, y, z = zens[0].mover.position
        for zen in zens:
            zen.mover.position = [x, y, z]
            zen.mover.body = None
        sigma.mover.position = [x, y, z+8]
        sigma.mover.body = None
        commands = dict.fromkeys(zens, (0, 600, SECONDARY))
        for _ in range(130):
            run.step(commands)
        for _ in range(10):
            run.step({**commands, sigma: (-32768, 0, 0x08)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 720)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 400)
        self.assertEqual(self.vitals(run, sigma).total, 800)
        for zen in zens:
            self.assertFalse(zen.client.messages(combat.HIT))

    def test_static_wall_intercepts_before_grasp_and_grants_no_shields(self):
        run, zen, sigma = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
        self.assertFalse(run.match.combat.rockets)

    def test_absorbed_cast_death_or_body_replacement_cannot_grant_shields_later(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, zen, sigma = self.arena()
                run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
                for _ in range(20):
                    run.step({sigma: (-32768, 0, 0)})
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
                old = sigma.body
                if replacement:
                    self.assertTrue(run.match.switch_hero(sigma, sigma.hero))
                    self.assertNotEqual(sigma.body, old)
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is sigma)
                    run.match.combat._damage(zen, target, 10000, False, None, run.frame)
                    self.assertTrue(run.match.combat.is_dead(sigma))
                for _ in range(200):
                    run.step({sigma: (-32768, 0, 0)})
                self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
                if replacement:
                    self.assertNotIn(old, run.match.combat.vitals)
                    self.assertFalse(any(k[0] == old for k in run.match.combat.sigma.conversions))

    def test_flashbang_front_is_consumed_without_splash_or_stun_but_back_interrupts_grasp(self):
        for front in (True, False):
            with self.subTest(front=front):
                run, caster, sigma = self.arena(start=False)
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                x, y, z = sigma.mover.position
                caster.mover.position = [x, y, z-3]
                caster.mover.body = None
                yaw = -32768 if front else 0
                for _ in range(10):
                    run.step({sigma: (yaw, 0, 0x08)})
                self.assertTrue(self.active(sigma))
                run.step({caster: (0, 600, 0x10), sigma: (yaw, 0, 0)})
                was_stunned = False
                for _ in range(160):
                    run.step({sigma: (yaw, 0, 0)})
                    was_stunned |= run.match.combat.is_stunned(sigma)
                self.assertEqual(was_stunned, not front)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 25 if front else 0)
                self.assertAlmostEqual(self.vitals(run, sigma).bonus_shields, 15 if front else 0, places=4)
                self.assertEqual(sum(self.vitals(run, sigma).now.values()), 400 if front else 375)

    def test_discord_targeting_bypasses_grasp_but_absorbed_orb_has_no_victim_damage_boost(self):
        run, zen, sigma = self.arena()
        run.step({zen: (0, 0, 0x08), sigma: (-32768, 0, 0)})
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertEqual(owner.discord.target, sigma.body)
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(25):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertEqual(owner.discord.target, sigma.body)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_skipped_world_frames_still_consume_projectile_once(self):
        results = []
        for advances in (range(1, 21), (20,)):
            run, zen, sigma = self.arena()
            run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
            run.step({sigma: (-32768, 0, 0)})
            frame = run.frame
            for advance in advances:
                tick = frame+advance
                run.match.update(tick*combat.TICK, tick)
            results.append((self.vitals(run, sigma).total,
                            self.instance(sigma).find_var(0, 21312).value(),
                            len(run.match.combat.rockets), len(run.match.combat.projectiles.shots)))
        self.assertEqual(results, [(400, 48, 0, 0), (400, 48, 0, 0)])

    def test_actual_zen_melee_bypasses_grasp_without_becoming_extra_shields(self):
        run, zen, sigma = self.arena()
        x, y, z = zen.mover.position
        sigma.mover.position = [x, y, z+1.5]
        sigma.mover.body = None
        run.step({zen: (0, 0, 0x800), sigma: (-32768, 0, 0)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.vitals(run, sigma).total, 370)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)

    def test_extra_shields_fully_decay_and_next_actual_cast_uses_only_new_absorbed_damage(self):
        run, zen, sigma = self.grant_burst()
        vitals = self.vitals(run, sigma)
        normal = dict(vitals.now)
        for _ in range(2400):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(vitals.bonus_shields, 0)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.now, normal)
        for _ in range(10):
            run.step({sigma: (-32768, 0, 0x08)})
        self.assertTrue(self.active(sigma))
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
        self.assertAlmostEqual(vitals.bonus_shields, 28.8, places=4)
        self.assertEqual(vitals.now, normal)


if __name__ == '__main__':
    unittest.main()
