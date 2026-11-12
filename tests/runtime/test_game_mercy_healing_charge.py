"""Delivered Mercy healing-charge checks against the combined Python host."""
import unittest
from test_game_combat import aim
from ow174.game import stats
import test_game_mercy_staff as staff_tests
from ow174.game.commands import FIRE, Command
from ow174.game.script import expr, graph, runtime

HEAL_TAG = expr.Asset(0x0D800000000034BF)

@unittest.skipUnless(graph.effect_script(0x04A0000000000019), "prepared Mercy effect graphs required")
class MercyHealingChargeTests(unittest.TestCase):
    def setUp(self):
        self.f = staff_tests.MercyStaffTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.mercy, self.ally = self.f.mercy, self.f.ally
        self.script = self.mercy.body_script
        self.component = self.script.component
        self.component.set(12918, 0.0)

    def charge(self):
        return self.component.entity_var(342).value()

    def step(self, count, **kwargs):
        self.f.step(count, **kwargs)

    def test_actual_staff_health_amount_and_authored_tag_reach_original_award(self):
        self.step(50)
        self.assertAlmostEqual(self.f.vitals.total, 144)
        self.assertAlmostEqual(self.charge(), 44, places=4)
        graph = next(i for i in self.component.instances.values() if i.graph.index == 0x0B8B)
        self.assertEqual(graph.find_var(0, 659).value(), (HEAL_TAG,))
        self.assertEqual(graph.find_var(0, 536).value(), expr.Entity(self.ally.body))
        self.assertAlmostEqual(graph.find_var(0, 499).value(), 0.88, places=6)

    def test_earlier_staff_healing_takes_cap_room_from_future_passive_credit(self):
        run = self.f.run
        self.component.set(12918, 1.0)
        self.script.set_ult_charge(1819.5)
        start = run.frame
        run.match.update((start + 65) * 0.016, start + 65)
        self.assertEqual(self.charge(), 1820)
        self.assertTrue(self.script.world.pending_ult_credits)

        x, y, z = self.f.ally.mover.position
        yaw, pitch = aim(self.f.fight._eye(self.mercy), (x, y + 1, z))
        self.mercy.take_commands([Command(start + 1, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertAlmostEqual(self.f.vitals.total, 100.88)
        self.assertEqual(self.charge(), 1820)
        self.assertFalse(hasattr(self.script.world, "pending_ult_credits"))
        self.mercy.take_commands([Command(start + 2, yaw=yaw, pitch=pitch, buttons=FIRE | 0x20)])
        self.assertTrue(self.component.entity_var(842).value())
        self.assertEqual(self.charge(), 0)

    def test_duplicate_input_and_another_consume_cannot_award_the_same_heal(self):
        self.step(20)
        before = self.charge()
        for _ in range(10):
            self.mercy.take_commands([Command(self.script.frame, buttons=FIRE)])
            self.f.fight.command(self.mercy, None)
        self.assertEqual(self.charge(), before)

    def test_original_award_multipliers_are_independent_of_passive_multiplier(self):
        self.component.set(9532, 2.0)
        self.component.set(13022, 0.25)
        self.step(50)
        self.assertAlmostEqual(self.charge(), 22, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 44, places=4)

    def test_healing_can_finish_charging_and_original_award_clamps_at_cost(self):
        self.script.set_ult_charge(1819.75)
        self.step(20)
        self.assertEqual(self.charge(), 1820)
        self.assertGreater(self.mercy.stats.value(stats.HEALING), 0.25)

    def test_excluded_292_and_25a_tags_heal_without_charge_then_allowed_tag_awards(self):
        # Explicit engine-event fixtures verify the original tag branches using
        # actual capped health; normal beam tags above come from the real graph.
        for tag in (0x292, 0x25A):
            given = self.f.fight.heal_target(self.mercy, self.ally.body, 10, self.f.run.frame,
                                            tags=(expr.Asset(0x0D80000000000000 | tag),))
            self.assertEqual(given, 10)
            self.assertEqual(self.charge(), 0)
        self.f.fight.heal_target(self.mercy, self.ally.body, 10, self.f.run.frame, tags=(HEAL_TAG,))
        self.assertEqual(self.charge(), 10)

    def test_target17625_blocks_only_charge_and_changes_are_read_on_next_heal(self):
        target_script = self.ally.body_script
        target_script.component.set(17625, True)
        self.step(20)
        self.assertAlmostEqual(self.f.vitals.total, 117.6, places=4)
        self.assertEqual(self.charge(), 0)
        target_script.component.set(17625, False)
        self.step(20)
        self.assertAlmostEqual(self.charge(), 17.6, places=4)

    def test_original_mode_eligibility_rejects_and_recovers_actual_heal_awards(self):
        runtime.send_message(self.component, 0x024000000000002D,
                             {0x29: expr.Asset(0x0D80000000000016)}, self.mercy.entity)
        self.step(50)
        self.assertAlmostEqual(self.f.vitals.total, 144, places=4)
        self.assertEqual(self.charge(), 0)
        runtime.send_message(self.component, 0x024000000000002D,
                             {0x29: expr.Asset(0)}, self.mercy.entity)
        self.step(50)
        self.assertAlmostEqual(self.charge(), 44, places=4)

    def test_passive_self_healing_never_emits_team_heal_charge(self):
        target = next(t for t in self.f.fight._targets() if t.owner is self.mercy)
        self.f.fight._damage(self.f.enemy, target, 50, False, None, self.f.run.frame)
        self.step(130, buttons=0)
        self.assertGreater(self.f.fight.vitals_of(target).total, 150)
        self.assertEqual(self.charge(), 0)
