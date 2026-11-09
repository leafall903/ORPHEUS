"""Actual Ana Nano cast and Bastion damage in the 104319 static host."""
import unittest
from dataclasses import replace

import test_bastion_combat as kit
import test_bastion_ironclad_transitions as ironclad
from test_game_combat import aim

from ow174.game import ana_grenade, combat, stats, zarya_barriers
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL

ROADHOG = 0x02E0000000000040


class BastionNanoTests(unittest.TestCase):
    def arena(self, mode="recon", *, distance=6, enemy_hero=None):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 8), bystander_position=(distance, 1, 0),
                     enemy_hero=enemy_hero)
        ana = case.bystander
        self.assertTrue(case.run.match.switch_hero(ana, heroes()[ana_grenade.HERO]))
        ana.mover.position[:] = (distance, 1, 0)
        ana.mover.body = None
        case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
        ana.team = case.player.team = 0
        case.enemy.team = 1
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        case.step(0, 15)
        return case, ana

    @staticmethod
    def cast(case, ana, *, charge=True, miss=False):
        script = ana.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value() if charge else 0)
        direction = (0, 0) if miss else aim(case.fight._eye(ana), (0, 2, 0))
        case.run.step({ana: (*direction, 32)})
        for _ in range(16):
            case.run.step({ana: (*direction, 0)})

    def test_actual_nano_heals_and_combines_with_ironclad_before_armour(self):
        for mode, expected in (("recon", 5), ("sentry", 4), ("tank", 4)):
            with self.subTest(mode=mode):
                case, ana = self.arena(mode)
                health = case.health(case.player)
                health.damage(270)
                case.fight._sync_body_health(case.player, health)
                before = health.total
                self.cast(case, ana)
                self.assertEqual(health.total - before, 250)
                self.assertEqual(ana.stats.value(stats.HEALING), 250)
                self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)
                self.assertGreater(health.nano_until, case.fight.now)
                before = health.total
                direction = aim(case.fight._eye(case.enemy), (0, 2, 0))
                case.run.step({case.enemy: (*direction, 1)})
                self.assertAlmostEqual(before - health.total, expected, places=5)

    def test_nano_combines_with_ironclad_at_all_five_form_boundaries(self):
        transitions = ironclad.BastionIroncladTransitionTests()
        for entered, final, boundary, before, after in (
            (1, 2, 62, 5, 4), (3, 0, 31, 4, 5), (4, 6, 93, 5, 4),
            (5, 6, 93, 4, 4), (7, 0, 63, 4, 5),
        ):
            for frame, expected_mode, loss in (
                (boundary - 1, entered, before), (boundary, final, after),
            ):
                with self.subTest(entered=entered, frame=frame):
                    case, ana = self.arena()
                    mode = transitions.enter(case, entered)
                    start = case.run.frame
                    self.cast(case, ana)
                    health = case.health(case.player)
                    self.assertGreater(health.nano_until, case.fight.now)
                    case.step(0, start + frame - case.run.frame - 1)
                    self.assertEqual(case.run.frame, start + frame - 1)
                    self.assertEqual(mode.value(), entered)
                    self.assertAlmostEqual(transitions.rifle(case), loss, places=5)
                    self.assertEqual(mode.value(), expected_mode)

    def test_projected_barrier_spends_raw_damage_before_nano_ironclad_and_armour(self):
        case, support = self.arena("sentry", distance=4)
        self.cast(case, support)
        health = case.health(case.player)
        deadline = health.nano_until
        self.assertGreater(deadline, case.fight.now)

        # The already applied Nano stays on Bastion when its caster changes hero.
        self.assertTrue(case.run.match.switch_hero(support, heroes()[zarya_barriers.HERO]))
        support.team = case.player.team
        support.mover.position[:] = (4, 1, 0)
        support.mover.body = None
        case.step(0, 15)
        case.run.step({support: (-16384, 0, 0)})
        case.run.step({support: (-16384, 0, 16)})
        projection = next(i for i in support.body_script.component.instances.values()
                          if i.graph.index == 0x025C)
        pet = projection.state(10)
        self.assertTrue(pet.active)

        rifle = ironclad.BastionIroncladTransitionTests.rifle
        before = dict(health.now)
        self.assertEqual(rifle(case), 0)
        self.assertEqual(health.now, before)
        self.assertEqual(case.fight.zarya_barriers.projected[support.body].remaining, 180)

        for _ in range(150):
            if not pet.active:
                break
            case.step()
        self.assertFalse(pet.active)
        self.assertEqual(health.nano_until, deadline)
        self.assertLess(case.fight.now, deadline)
        self.assertAlmostEqual(rifle(case), 4, places=5)
        self.assertAlmostEqual(before[ARMOUR_POOL] - health.now[ARMOUR_POOL], 4, places=5)
        self.assertEqual(health.now[HEALTH_POOL], before[HEALTH_POOL])

    def test_nano_boosts_actual_recon_sentry_and_tank_damage(self):
        for mode, damage in (("recon", 60), ("sentry", 112.5), ("tank", 307.5)):
            with self.subTest(mode=mode):
                case, ana = self.arena(mode, enemy_hero=ROADHOG)
                self.cast(case, ana)
                enemy = case.health(case.enemy)
                before = enemy.total
                case.step(1, 1 if mode == "tank" else 10)
                case.step(0, 22)
                self.assertAlmostEqual(before - enemy.total, damage, places=4)

    def test_sentry_incoming_and_outgoing_switch_on_first_expired_frame(self):
        for expired, outgoing, incoming in ((False, 22.5, 4), (True, 15, 11)):
            with self.subTest(expired=expired):
                case, ana = self.arena("sentry")
                self.cast(case, ana)
                own, enemy = case.health(case.player), case.health(case.enemy)
                deadline = own.nano_until
                self.assertGreater(deadline, case.fight.now)
                while (case.run.frame + 2) * combat.TICK < deadline:
                    case.step()
                if expired:
                    case.step()
                direction = aim(case.fight._eye(case.enemy), (0, 2, 0))
                own_before, enemy_before = own.total, enemy.total
                case.run.step({case.player: (*case.command_aim, 1), case.enemy: (*direction, 1)})
                if expired:
                    self.assertLess(case.fight.now - combat.TICK, deadline)
                    self.assertGreaterEqual(case.fight.now, deadline)
                else:
                    self.assertLess(case.fight.now, deadline)
                    self.assertGreaterEqual(case.fight.now + combat.TICK, deadline)
                self.assertAlmostEqual(enemy_before - enemy.total, outgoing, places=5)
                self.assertAlmostEqual(own_before - own.total, incoming, places=5)
                self.assertAlmostEqual(case.fight._bastion_damage_scalar(case.player), 0.8)

    def test_tank_projectile_keeps_emission_boost_across_nano_expiry(self):
        case, ana = self.arena(enemy_hero=ROADHOG)
        case.enemy.mover.position[:] = (0, 1, 30)
        case.enemy.mover.body = None
        case.command_aim = aim(case.fight._eye(case.player), (0, 2, 30))
        self.cast(case, ana)
        own = case.health(case.player)
        deadline = own.nano_until
        self.assertGreater(deadline, case.fight.now)
        while case.fight.now < deadline - 1.72:
            case.step()
        script = case.player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        case.step(32)
        case.step(0, 94)
        self.assertLess(case.fight.now, deadline)
        enemy = case.health(case.enemy)
        before = enemy.total
        case.step(1)
        self.assertTrue(case.fight.rockets)
        self.assertEqual(enemy.total, before)
        for _ in range(40):
            case.step()
            if enemy.total < before:
                break
        self.assertGreaterEqual(case.fight.now, deadline)
        self.assertAlmostEqual(before - enemy.total, 307.5, places=4)
        case.step(0, 45)
        before = enemy.total
        case.step(1)
        case.step(0, 38)
        self.assertAlmostEqual(before - enemy.total, 205, places=4)

    def test_nano_recast_requires_charge_and_refreshes_the_target_deadline(self):
        case, ana = self.arena()
        health = case.health(case.player)
        self.cast(case, ana)
        first = health.nano_until
        self.assertGreater(first, case.fight.now)
        case.step(0, 110)
        self.cast(case, ana, charge=False)
        self.assertEqual(health.nano_until, first)
        self.cast(case, ana)
        self.assertGreater(health.nano_until, first)
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)

    def test_invalid_target_or_missing_charge_does_not_heal_or_apply(self):
        for reason in ("wall", "range", "team", "miss", "charge"):
            with self.subTest(reason=reason):
                case, ana = self.arena(distance=45 if reason == "range" else 6)
                if reason == "wall":
                    ana.mover.position[:] = (0, 1, 6)
                    ana.mover.body = None
                    case.wall(3)
                    case.step(0, 4)
                elif reason == "team":
                    ana.team = 1
                health = case.health(case.player)
                health.damage(100)
                before = health.total
                self.cast(case, ana, charge=reason != "charge", miss=reason == "miss")
                self.assertEqual(health.total, before)
                self.assertEqual(health.nano_until, 0)
                script = ana.body_script
                expected = 0 if reason == "charge" else script.component.entity_var(ULT_COST).value()
                self.assertEqual(script.component.entity_var(342).value(), expected)


if __name__ == "__main__":
    unittest.main()
