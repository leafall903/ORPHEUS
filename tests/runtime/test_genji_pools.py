"""Genji commands against actual Reinhardt armour and Zarya shield schemas."""

import unittest

from test_game_combat import aim
from test_genji_combat import GenjiFixture
from test_genji_ultimate import aimed_burst

from ow174.game import combat
from ow174.game.content import heroes


class GenjiPoolTests(GenjiFixture):
    def scene(self, name, distance=1):
        run, genji = self.spawn(distance)
        target = run.players[1]
        definition = next(h for h in heroes().values() if h.name == name)
        self.assertTrue(run.match.switch_hero(target, definition))
        target.mover.position[:] = [0, 1, distance]
        genji.body_script.ult_seconds = 0
        body = next(t for t in run.match.combat._targets() if t.owner is target)
        return run, genji, target, run.match.combat.vitals_of(body)

    def test_actual_pool_schemas_and_primary_cross_pool_damage_charge_and_body_health(self):
        for name, initial, expected, charge in (
            ("Reinhardt", {0: 300, 1: 200, 2: 0}, {0: 284, 1: 0, 2: 0}, 216),
            ("Zarya", {0: 200, 1: 0, 2: 200}, {0: 139, 1: 0, 2: 0}, 261),
        ):
            with self.subTest(hero=name):
                run, genji, target, vitals = self.scene(name, 4)
                self.assertEqual(vitals.most, initial)
                for _ in range(3):
                    aimed_burst(run, genji)
                self.assertEqual(vitals.now, expected)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), charge)
                for variable in (253, 1732):
                    self.assertEqual(target.body_script.component.entity_var(variable).value(),
                                     sum(expected.values()))

    def test_fan_melee_strike_and_blade_consume_the_real_defensive_pool(self):
        for name, pool in (("Reinhardt", 1), ("Zarya", 2)):
            for attack, damage in (("fan", 87), ("melee", 30), ("strike", 50), ("blade", 110)):
                with self.subTest(hero=name, attack=attack):
                    run, genji, target, vitals = self.scene(name, 1.5)
                    if attack == "fan":
                        # At point blank an eye-height origin hits Reinhardt's
                        # head before reaching a torso aim point. Aim low enough
                        # to exercise three body impacts on both actual capsules.
                        yaw, pitch = aim(run.match.combat._eye(genji), (0, 1.25, 1.5))
                        for frame in range(60):
                            run.step({genji: (yaw, pitch, 2 if frame == 0 else 0)})
                        self.assertEqual(len(genji.client.messages(combat.HIT)), 3)
                        self.assertTrue(all(not m["+0x78"]["+0x8"] & combat.CRITICAL
                                            for m in genji.client.messages(combat.HIT)))
                    elif attack == "melee":
                        self.fire(run, genji, 0x800)
                    else:
                        if attack == "blade":
                            genji.body_script.set_ult_charge(
                                genji.body_script.component.entity_var(338).value())
                        for frame in range(110):
                            button = (32 if frame == 0 else (1 if frame > 70 else 0)) \
                                if attack == "blade" else (8 if frame == 0 else 0)
                            run.step({genji: (0, 0, button)})
                    effective = damage - (15 if attack == "fan" else 5) if pool == 1 else damage
                    self.assertEqual(vitals.now[pool], 200 - effective)
                    self.assertEqual(vitals.now[0], vitals.most[0])
                    charge = genji.body_script.component.entity_var(342).value()
                    self.assertEqual(charge, 0 if attack == "blade" else effective)
                    self.assertFalse(run.match.combat.is_dead(target))

    def test_real_defensive_pools_do_not_prevent_death_or_credit_overkill(self):
        for name, total in (("Reinhardt", 500), ("Zarya", 400)):
            with self.subTest(hero=name):
                run, genji, target, vitals = self.scene(name, 4)
                for _ in range(8):
                    aimed_burst(run, genji)
                    if run.match.combat.is_dead(target):
                        break
                self.assertTrue(run.match.combat.is_dead(target))
                self.assertEqual(vitals.total, 0)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), total)
                self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)


if __name__ == "__main__":
    unittest.main()
