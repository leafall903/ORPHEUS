"""Reconstructed damage credit with authored cost and actual projectile/melee damage."""

import unittest
from unittest.mock import patch

import test_pharah_combat as primary
import test_pharah_defenses as defense
from test_game_combat import aim


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

    def test_barrage_emits_all_seventy_six_rockets_on_authored_cadence(self):
        run, player, enemy = self.prepare()
        enemy.mover.position[0] = 30
        enemy.mover.body = None
        fight = run.match.combat
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({player: (0, 0, 32)})
            first, = fight.rockets
            self.assertEqual(first.speed, 45)
            self.assertEqual(first.left, 5)
            self.assertEqual(first.range_limit, 100)
            self.assertEqual(first.distance_flown, 0)
            self.assertEqual(first.direct.amount, 10)
            self.assertEqual(first.blast.amount, 30)
            for _ in range(210):
                run.step({})
        self.assertFalse(script.ultimate_running())
        self.assertEqual(self.charge(player), 0)
        reports = [(call.args[3], call.args[4], call.kwargs["activation"]) for call in shoot.call_args_list]
        self.assertEqual(len(reports), 76)
        start, activation = reports[0][1:]
        self.assertEqual(reports, [
            (index, start + round(index * 1000 / 30), activation) for index in range(76)
        ])
        self.assertEqual(reports[-1][1] - start, 2500)

    def test_in_flight_primary_credits_lethal_damage_only_after_barrage_ends(self):
        for distance, during_barrage in ((35, True), (95, False)):
            with self.subTest(distance=distance):
                run, player, enemy = self.prepare()
                enemy.mover.position[2] = distance
                enemy.mover.body = None
                vitals = self.health(run, enemy)
                vitals.damage(vitals.total - 60)
                fight = run.match.combat
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 1)})
                shot, = fight.rockets
                self.assertEqual(shot.direct.amount, 40)
                run.step({player: (-32768, 0, 32)})
                self.assertTrue(script.ultimate_running())
                self.assertIn(shot, fight.rockets)
                self.assertEqual(self.charge(player), 0)
                for _ in range(200):
                    if vitals.dead:
                        break
                    run.step({player: (-32768, 0, 0)})
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, player.body)
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(script.ultimate_running(), during_barrage)
                self.assertEqual(self.charge(player), 0 if during_barrage else 60)

    def test_in_flight_primary_keeps_damage_but_cannot_charge_replacement_body(self):
        run, player, enemy = self.prepare()
        enemy.mover.position[2] = 24
        enemy.mover.body = None
        fight = run.match.combat
        old_body = player.body
        position = tuple(player.mover.position)
        run.step({player: (0, 0, 1)})
        shot, = fight.rockets
        self.assertEqual(shot.source_body, old_body)

        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old_body)
        self.assertIn(shot, fight.rockets)
        player.body_script.ult_seconds = 0
        player.mover.position = list(position)
        player.mover.body = None
        vitals = self.health(run, enemy)
        for _ in range(80):
            if shot not in fight.rockets:
                break
            run.step({})
        self.assertEqual(vitals.total, 80)
        self.assertEqual(self.charge(player), 0)

        self.fire_once(run, player)
        self.assertTrue(vitals.dead)
        self.assertEqual(self.charge(player), 80)

    def test_lethal_dart_leaves_in_flight_primary_credit_with_its_original_body(self):
        fixture = defense.PharahDefenseTests()
        run, player, ana, enemy = fixture.encounter("Ana")
        fight = run.match.combat
        y = player.mover.position[1]
        ana.mover.position = [4, y, 0]
        enemy.mover.position = [0, y, 24]
        ana.mover.body = enemy.mover.body = None
        vitals = fixture.health(run, player)
        vitals.damage(vitals.total - 1)
        target_health = fixture.health(run, enemy)
        source = player.body
        dart = aim(fight._eye(ana), fight._eye(player))
        run.step({player: (0, 0, 1), ana: (*dart, 8)})
        shot, = (rocket for rocket in fight.rockets if rocket.shooter is player)
        for _ in range(38):
            if vitals.dead:
                break
            run.step({ana: (*dart, 0)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, ana.body)
        self.assertIn(shot, fight.rockets)
        self.assertEqual(target_health.total, 200)
        self.assertEqual(self.charge(player), 0)

        for _ in range(80):
            if shot not in fight.rockets:
                break
            run.step({})
        self.assertEqual(player.body, source)
        self.assertTrue(vitals.dead)
        self.assertEqual(target_health.total, 80)
        self.assertEqual(self.charge(player), 120)

    def test_passive_charge_reaches_barrage_threshold_after_rejected_press(self):
        run, player, _ = self.encounter()
        script = player.body_script
        cost = script.component.entity_var(338).value()
        per_frame = cost * script.frame_ms / (1000 * script.ult_seconds)
        script.set_ult_charge(cost - per_frame / 2)
        self.assertLess(self.charge(player), cost)
        run.step({player: (-32768, 0, 32)})
        self.assertEqual(self.charge(player), cost)
        self.assertFalse(script.ultimate_running())
        self.assertFalse(run.match.combat.rockets)
        for _ in range(8):
            run.step({player: (-32768, 0, 32)})
        self.assertEqual(self.charge(player), cost)
        self.assertFalse(script.ultimate_running())
        run.step({})
        run.step({player: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())
        self.assertEqual(self.charge(player), 0)

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
            if not script.ultimate_running():
                break
            run.step({})
        self.assertFalse(script.ultimate_running())
        late = min(run.match.combat.rockets, key=lambda rocket: rocket.flown)
        self.assertLess(late.flown, .1)
        self.assertEqual(self.health(run, enemy).total, 200)
        # Position the body just ahead of an actual surviving round. The
        # full-duration test does not depend on straight aim at long range.
        enemy.mover.position[0] = late.position[0] + 1.5 * late.direction[0]
        enemy.mover.position[2] = late.position[2] + 1.5 * late.direction[2]
        enemy.mover.body = None
        for _ in range(35):
            run.step({})
            if self.health(run, enemy).total < 200:
                break
        self.assertLess(self.health(run, enemy).total, 200)
        self.assertEqual(self.charge(player), 0)


if __name__ == "__main__":
    unittest.main()
