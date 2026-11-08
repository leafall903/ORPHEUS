"""Reconstructed damage credit with authored cost and actual projectile/melee damage."""

import unittest

import test_pharah_combat as primary


class PharahChargeTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter
    fire_once = primary.PharahCombatTests.fire_once
    health = primary.PharahCombatTests.health

    def prepare(self):
        run, player, enemy = self.encounter()
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        return run, player, enemy

    def charge(self, player):
        return player.body_script.component.entity_var(342).value()

    def test_actual_primary_credits_120_and_lethal_second_hit_only_remaining_80(self):
        run, player, enemy = self.prepare()
        self.fire_once(run, player)
        self.assertEqual(self.health(run, enemy).total, 80)
        self.assertEqual(self.charge(player), 120)
        self.fire_once(run, player)
        self.assertTrue(self.health(run, enemy).dead)
        self.assertEqual(self.charge(player), 200)

    def test_melee_credits_actual_30_and_miss_does_not_credit(self):
        run, player, enemy = self.prepare()
        enemy.mover.position[2] = 1.8
        enemy.mover.body = None
        run.step({player: (0, 0, 0x800)})
        for _ in range(70):
            run.step({})
        self.assertEqual(self.health(run, enemy).total, 170)
        self.assertEqual(self.charge(player), 30)
        run.step({player: (-16384, 0, 0x800)})
        for _ in range(70):
            run.step({})
        self.assertEqual(self.charge(player), 30)

    def test_credit_caps_at_authored_cost(self):
        run, player, _ = self.prepare()
        script = player.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 10)
        self.fire_once(run, player)
        self.assertEqual(self.charge(player), cost)

    def test_barrage_damage_never_recharges_after_channel_has_ended(self):
        run, player, enemy = self.prepare()
        script = player.body_script
        enemy.mover.position[0] = 20
        enemy.mover.body = None
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        # Keep the real target off axis until the channel ends, then move it into
        # the late authored missiles. No hand-authored projectile/hit definition.
        for _ in range(210):
            run.step({})
        self.assertFalse(script.ultimate_running())
        self.assertTrue(run.match.combat.rockets)
        self.assertEqual(self.health(run, enemy).total, 200)
        enemy.mover.position[0] = 0
        enemy.mover.position[2] = 95
        enemy.mover.body = None
        for _ in range(140):
            run.step({})
        self.assertLess(self.health(run, enemy).total, 200)
        self.assertEqual(self.charge(player), 0)


if __name__ == "__main__":
    unittest.main()
