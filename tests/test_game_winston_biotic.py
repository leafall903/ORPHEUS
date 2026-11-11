"""Actual Ana grenade and Winston's tagged transformation refill."""

import unittest

import test_game_winston_sleep as fixtures
from test_game_combat import aim

from ow174.game import combat, winston, zarya
from ow174.game.content import heroes


class WinstonBioticTests(unittest.TestCase):
    setUp = fixtures.WinstonSleepTests.setUp
    move = staticmethod(fixtures.WinstonSleepTests.move)
    step = fixtures.WinstonSleepTests.step
    health = fixtures.WinstonSleepTests.health

    def vitals(self):
        return self.fight.vitals[self.owner.body]

    def grenade(self, status):
        self.step(cast=16)
        for _ in range(60):
            windows = getattr(self.vitals(), status)
            if windows:
                return windows[-1][1]
            self.step()
        self.fail('the authored grenade failed to apply its body effect')

    def field_heal(self, amount):
        self.fight.heal_area(self.ally, tuple(self.owner.mover.position), 1, amount)

    def test_reflected_allied_grenade_antiheals_winston_or_hits_his_barrier(self):
        for reflect, shield in ((False, False), (True, False), (True, True)):
            with self.subTest(reflect=reflect, shield=shield):
                self.setUp()
                genji = self.ally
                self.assertTrue(self.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.team = 1
                self.caster.team = self.owner.team
                self.move(genji, (0, 1, 8))
                self.move(self.caster, (0, 1, -4))
                self.move(self.owner, (2, 1, 0))
                if shield:
                    self.step(16)
                    for _ in range(4):
                        self.step()
                genji_angle = aim(self.fight._eye(genji), (2.6, 1.5, 0))
                self.run.step({genji: (*genji_angle, 16 if reflect else 0)})
                for _ in range(4):
                    self.run.step({genji: (*genji_angle, 0)})
                ana_angle = aim(self.fight._eye(self.caster), (0, 2.35, 8))
                self.run.step({genji: (*genji_angle, 0), self.caster: (*ana_angle, 16)})
                onset = None
                for _ in range(65):
                    self.run.step({genji: (*genji_angle, 0)})
                    if onset is None and self.vitals().healing_blocks:
                        onset = self.run.frame
                self.assertEqual(self.health(genji), 200 if reflect else 150)
                self.assertEqual(self.health(self.owner), 455 if reflect and not shield else 500)
                self.assertFalse(self.vitals().healing_boosts)
                if reflect and not shield:
                    self.assertIsNotNone(onset)
                    end = self.vitals().healing_blocks[-1][1]
                    self.assertEqual(end - onset, 250)
                    point = tuple(self.owner.mover.position)
                    self.fight.heal_area(self.caster, point, 1, 20)
                    self.assertEqual(self.health(self.owner), 455)
                    self.match.update(end * combat.TICK, end, frames=False)
                    self.fight.heal_area(self.caster, point, 1, 20)
                    self.assertEqual(self.health(self.owner), 475)
                else:
                    self.assertFalse(self.vitals().healing_blocks)
                if shield:
                    self.assertEqual(self.fight.barriers.actors[self.owner.body].health, 650)

    def test_enemy_grenade_blocks_ordinary_healing_until_world_deadline(self):
        end = self.grenade('healing_blocks')
        self.assertEqual(end - self.run.frame, 250)
        self.assertEqual(self.health(self.owner), 455)
        self.field_heal(20)
        self.assertEqual(self.health(self.owner), 455)
        self.run.frame = end
        self.match.update(end * combat.TICK, end, frames=False)
        self.field_heal(20)
        self.assertEqual(self.health(self.owner), 475)

    def test_primal_refill_bypasses_active_antiheal_without_clearing_it(self):
        end = self.grenade('healing_blocks')
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.health(self.owner), 1000)
        self.assertEqual(sum(self.vitals().most.values()), 1000)
        self.assertEqual(self.vitals().healing_blocks[-1][1], end)
        self.assertGreater(end, self.run.frame)

    def test_barrier_intercepts_grenade_before_damage_and_antiheal(self):
        self.move(self.caster, (0, 1, 9))
        self.step(16)
        for _ in range(3):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        self.step(cast=16)
        for _ in range(60):
            self.step()
        self.assertEqual(bubble.health, 650)
        self.assertEqual(self.health(self.owner), 500)
        self.assertFalse(self.vitals().healing_blocks)

    def test_projected_barrier_absorbs_grenade_damage_and_antiheal(self):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[zarya.HERO]))
        self.move(self.ally, (0, 1, -3))
        self.step(ally=16)
        for _ in range(4):
            self.step()
        self.step(cast=16)
        for _ in range(60):
            self.step()
        self.assertEqual(self.fight.zarya_barriers.projected[self.ally.body].remaining, 150)
        self.assertEqual(self.health(self.owner), 500)
        self.assertFalse(self.vitals().healing_blocks)

    def test_actual_biotic_field_starts_healing_at_antiheal_expiry(self):
        end = self.grenade('healing_blocks')
        self.move(self.ally, (0, 1, -1))
        self.step(ally=16)
        while self.run.frame < end - 1:
            self.step()
        self.assertEqual(self.health(self.owner), 455)
        self.step()
        self.assertGreater(self.health(self.owner), 455)

    def test_actual_biotic_field_gap_preserves_antiheal_expiry_boundary(self):
        results = []
        for sparse in (False, True):
            self.setUp()
            end = self.grenade('healing_blocks')
            self.move(self.ally, (0, 1, -1))
            self.step(ally=16)
            while self.run.frame < end - 12:
                self.step()
            self.assertEqual(self.health(self.owner), 455)
            if sparse:
                self.run.frame = end + 10
                self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
            else:
                while self.run.frame < end + 10:
                    self.step()
            results.append(self.health(self.owner))
        self.assertAlmostEqual(results[0], 461.16)
        self.assertAlmostEqual(results[0], results[1])

    def test_allied_grenade_heals_then_boosts_ordinary_healing(self):
        self.ally.team = 1
        self.move(self.ally, (0, 1, -3))
        for _ in range(32):
            self.step(ally=1)
        self.ally.team = self.caster.team = self.owner.team
        self.move(self.ally, (30, 1, 30))
        injured = self.health(self.owner)
        self.assertLess(injured, 370)
        self.grenade('healing_boosts')
        self.assertEqual(self.health(self.owner), injured + 100)
        self.field_heal(10)
        self.assertEqual(self.health(self.owner), injured + 115)
