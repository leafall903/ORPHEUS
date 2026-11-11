"""Actual spray impacts schedule a bounded, refreshed trailing heal."""

import unittest

import test_game_moira as fixtures
from test_game_combat import aim, in_world

from ow174.game import stats


class MoiraSprayTailTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    def apply_spray(self, run, source):
        for _ in range(10):
            run.step({source: (0, 0, 1)})
        self.settle_impacts(run, source)

    def settle_impacts(self, run, source):
        # Piercing packets continue beyond the recipient. Wait for their final
        # contact here, rather than their later max-range flight retirement.
        target = run.players[1].body
        for _ in range(20):
            run.step({})
            if not any(rocket.shooter is source and rocket.spray is not None
                       and target not in rocket.spray.hits for rocket in run.match.combat.rockets):
                return
        self.fail("spray impacts did not settle")

    def test_tail_delays_then_heals_35_once_and_expires(self):
        run, source, target, vitals = self.setup_fight()
        self.apply_spray(run, source)
        self.assertIn(target.entity, run.match.combat.moira_hots)
        before = vitals.total
        for _ in range(8):
            run.step({})
        self.assertEqual(vitals.total, before)
        for _ in range(150):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 35, places=4)
        self.assertNotIn(target.entity, run.match.combat.moira_hots)
        settled = vitals.total
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, settled)

    def test_repeated_impacts_refresh_one_tail_without_stacking(self):
        run, source, target, vitals = self.setup_fight()
        for _ in range(20):
            run.step({source: (0, 0, 1)})
        self.settle_impacts(run, source)
        self.assertEqual(list(run.match.combat.moira_hots), [target.entity])
        before = vitals.total
        for _ in range(150):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 35, places=4)

    def test_second_moira_replaces_active_tail_and_owns_remaining_healing(self):
        run, first, target, vitals = self.setup_fight()
        match = run.match
        self.apply_spray(run, first)
        effect = match.combat.moira_hots[target.entity]
        before = vitals.total
        for _ in range(40):
            run.step({})
            if vitals.total > before:
                break
        self.assertGreater(vitals.total, before)

        second = match.add_player(2, "Second Moira", fixtures.MOIRA, first.team, False)
        in_world(match, second)
        run.players.append(second)
        self.assertTrue(match.switch_hero(second, first.hero))
        second.team = first.team
        x, y, z = first.mover.position
        second.mover.position = [x + 1, y, z]
        direction = aim(match.combat._eye(second),
                        (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        for _ in range(10):
            run.step({second: (*direction, 1)})
        self.settle_impacts(run, second)

        replacement = match.combat.moira_hots[target.entity]
        self.assertIsNot(effect, replacement)
        self.assertIs(replacement.healer, second)
        self.assertEqual(replacement.source_body, second.body)
        first_healing = first.stats.value(stats.HEALING)
        second_healing = second.stats.value(stats.HEALING)
        before = vitals.total
        for _ in range(150):
            run.step({})

        self.assertNotIn(target.entity, match.combat.moira_hots)
        self.assertEqual(first.stats.value(stats.HEALING), first_healing)
        self.assertAlmostEqual(second.stats.value(stats.HEALING) - second_healing, 35, places=4)
        self.assertAlmostEqual(vitals.total - before, 35, places=4)

    def test_tail_does_not_follow_a_team_change(self):
        run, source, target, vitals = self.setup_fight()
        self.apply_spray(run, source)
        target.owner.team = 1 - source.team
        before = vitals.total
        for _ in range(150):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertNotIn(target.entity, run.match.combat.moira_hots)

    def test_tail_is_canceled_on_target_death(self):
        run, source, target, vitals = self.setup_fight()
        self.apply_spray(run, source)
        vitals.damage(1000)
        vitals.died_at = run.match.combat.now
        for _ in range(150):
            run.step({})
        self.assertEqual(vitals.total, 0)
        self.assertTrue(vitals.dead)
        self.assertNotIn(target.entity, run.match.combat.moira_hots)
