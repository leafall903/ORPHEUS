"""Real incoming rifle damage across 104319 Ironclad boundaries."""
import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import defensive_casts, world, zarya_barriers
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class BastionIroncladTransitionTests(unittest.TestCase):
    @staticmethod
    def rifle(case):
        health = case.health(case.player)
        before = health.total
        direction = aim(case.fight._eye(case.enemy), (0, 2, 0))
        case.run.step({case.enemy: (*direction, 1)})
        return before - health.total

    def prepare(self, entered):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 10))
        return case, self.enter(case, entered)

    def enter(self, case, entered):
        script = case.player.body_script
        mode = script.component.entity_var(1216)
        if entered in (3, 5):
            case.configure()
        if entered in (1, 3):
            case.step(8)
        elif entered in (4, 5):
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            case.step(32)
        else:
            case.tank()
            for _ in range(600):
                if mode.value() == 7:
                    break
                case.step()
        self.assertEqual(mode.value(), entered)
        return mode

    def test_real_rifle_samples_last_transition_and_first_finished_frames(self):
        for entered, final, boundary, before, after in (
            (1, 2, 62, 15, 11), (3, 0, 31, 11, 15),
            (4, 6, 93, 15, 11), (5, 6, 93, 11, 11), (7, 0, 63, 11, 15),
        ):
            for frame, expected_mode, expected_damage in (
                (boundary - 1, entered, before), (boundary, final, after),
            ):
                with self.subTest(entered=entered, frame=frame):
                    case, mode = self.prepare(entered)
                    case.step(0, frame - 1)
                    self.assertEqual(mode.value(), entered)
                    self.assertAlmostEqual(self.rifle(case), expected_damage, places=5)
                    self.assertEqual(mode.value(), expected_mode)

    def test_flying_orisa_round_uses_ironclad_at_contact_across_reconfigure(self):
        for entered, final, boundary, expected in (
            (1, 2, 62, 4.4), (3, 0, 31, 6), (4, 6, 93, 4.4),
            (5, 6, 93, 4.4), (7, 0, 63, 6),
        ):
            with self.subTest(entered=entered):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 10), enemy_hero=defensive_casts.ORISA)
                mode = self.enter(case, entered)
                case.step(0, boundary - 4)
                health = case.health(case.player)
                case.run.step({case.enemy: (-32768, 0, 1)})
                self.assertEqual(mode.value(), entered)
                rocket, = case.fight.rockets
                self.assertIs(rocket.shooter, case.enemy)
                self.assertEqual(health.total, 300)
                case.step(0, 3)
                self.assertEqual(mode.value(), final)
                self.assertIn(rocket, case.fight.rockets)
                self.assertEqual(health.total, 300)
                case.step(0, 20)
                self.assertFalse(case.fight.rockets)
                self.assertAlmostEqual(health.now[world.ARMOUR_POOL], 100 - expected)
                self.assertEqual(health.now[world.HEALTH_POOL], 200)

    def test_projected_barrier_spends_raw_contact_then_ironclad_applies_after_expiry(self):
        for form in ("sentry", "tank"):
            with self.subTest(form=form):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 10), bystander_position=(4, 1, 0))
                zarya = case.bystander
                self.assertTrue(case.run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
                zarya.mover.position[:] = (4, 1, 0)
                zarya.mover.body = None
                case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                case.player.team = zarya.team = 0
                case.enemy.team = 1
                case.configure() if form == "sentry" else case.tank()
                case.run.step({zarya: (-16384, 0, 0)})
                case.run.step({zarya: (-16384, 0, 16)})
                instances = zarya.body_script.component.instances.values()
                pet = next(i for i in instances if i.graph.index == 0x025C).state(10)
                self.assertTrue(pet.active)
                self.assertEqual(self.rifle(case), 0)
                self.assertEqual(case.fight.zarya_barriers.projected[zarya.body].remaining, 180)
                for _ in range(150):
                    case.step()
                    if not pet.active:
                        break
                self.assertFalse(pet.active)
                self.assertAlmostEqual(self.rifle(case), 11, places=5)


if __name__ == "__main__":
    unittest.main()
