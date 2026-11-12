"""Tank release/repress at authored recovery; actual projectile and pool outcomes."""

import unittest

import test_bastion_combat as kit

from ow174.game import combat


class BastionTankCadenceTests(unittest.TestCase):
    def fixture(self):
        case = kit.BastionCombatTests()
        case.fixture()
        case.tank()
        # A declared large enemy pool lets several real65+140 impacts settle
        # without death/respawn hiding a repeated shot or its damage.
        pool = combat.Vitals(case.enemy.body, 10000, 0)
        case.fight.vitals[case.enemy.body] = pool
        case.fight._sync_body_health(case.enemy, pool)
        return case, pool

    def sample(self, case, pool, buttons, frames):
        seen, fired, impacts = [], [], []
        before = pool.total
        for frame in range(1, frames + 1):
            case.step(buttons(frame))
            for rocket in case.fight.rockets:
                if not any(rocket is old for old in seen):
                    seen.append(rocket)
                    fired.append(frame)
            if pool.total != before:
                impacts.append((frame, before - pool.total))
                before = pool.total
        return fired, impacts

    def test_release_repress_does_not_bypass_recovery_and_resumes_actual_damage(self):
        for release, resume, expected in (
            (2, 3, [1, 63, 126]),
            (2, 32, [1, 63, 126]),
            (2, 63, [1, 63, 126]),
            (2, 81, [1, 81, 143]),
            (32, 33, [1, 63, 126]),
        ):
            with self.subTest(release=release, resume=resume):
                case, pool = self.fixture()
                fired, impacts = self.sample(
                    case, pool,
                    lambda frame, release=release, resume=resume: int(frame < release or frame >= resume),
                    160)
                self.assertEqual(fired, expected)
                # The first hit pushes the victim farther away. Follow-up
                # projectiles take one more world tick while launch recovery holds.
                self.assertEqual(impacts, [(expected[0] + 10, 205),
                                           *((frame + 11, 205) for frame in expected[1:])])
                self.assertGreater(case.enemy.mover.position[2], 10)
                self.assertEqual(pool.total, 10000 - 615)
                self.assertFalse(pool.dead)
                self.assertEqual(case.player.body_script.component.entity_var(1216).value(), 6)
                self.assertFalse(case.fight.rockets)

    def test_released_trigger_does_not_queue_another_shot_and_ready_tap_damages(self):
        case, pool = self.fixture()
        fired, impacts = self.sample(case, pool, lambda frame: int(frame in (1, 101)), 190)
        self.assertEqual(fired, [1, 101])
        self.assertEqual(impacts, [(11, 205), (112, 205)])
        self.assertGreater(case.enemy.mover.position[2], 10)
        self.assertEqual(pool.total, 10000 - 410)
        self.assertFalse(case.fight.rockets)


if __name__ == '__main__':
    unittest.main()
