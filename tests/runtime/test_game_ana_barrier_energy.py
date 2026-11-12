"""Actual Ana contacts bridge modeled Pet health loss into Zarya's0242 graph."""
import unittest

import test_game_ana_barrier as personal
import test_game_ana_projected_barrier as projected
from test_game_combat import aim

from ow174.game.content import find_hero


def energy(caster):
    slot = caster.body_script.component.entity_var(87, create=False)
    return slot.value() if slot is not None and slot.value() is not None else 0


class AnaBarrierEnergyTests(unittest.TestCase):
    def fight(self, projected_target=True, *, friendly=False, distance=6):
        if projected_target:
            helper = projected.AnaProjectedBarrierTests()
            run, ana, caster, target = helper.fight(ally_ana=friendly, distance=distance)
            barrier = helper.cast(run, caster, target)
            entry = helper.entry(run, target)
        else:
            helper = personal.AnaPersonalBarrierTests()
            run, ana, caster, entry = helper.fight(ally=friendly, distance=distance)
            barrier = run.match.combat.body_barriers[caster.body]
        return run, ana, caster, entry, barrier

    def contact(self, run, ana, entry, barrier, button, *, scoped=False):
        direction = aim(run.match.combat._eye(ana),
                        (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2)})
        before = barrier.health
        run.step({ana: (*direction, button | (2 if scoped else 0))})
        for _ in range(60):
            if barrier.health < before:
                return
            run.step({ana: (*direction, 0)})
        self.fail('actual Ana input never contacted the active barrier')

    def test_both_barriers_gain_authored_energy_from_actual_ana_contacts(self):
        for other in (False, True):
            for button, scoped, gain in ((1, False, 14), (1, True, 14), (8, False, 1),
                                        (16, False, 10), (0x800, False, 6)):
                with self.subTest(projected=other, button=button, scoped=scoped):
                    run, ana, caster, entry, barrier = self.fight(
                        other, distance=1.5 if button == 0x800 else 6)
                    self.assertEqual(energy(caster), 0)
                    before = run.match.combat.vitals_of(entry).total
                    self.contact(run, ana, entry, barrier, button, scoped=scoped)
                    self.assertEqual(energy(caster), gain)
                    self.assertEqual(run.match.combat.vitals_of(entry).total, before)
                    self.assertEqual(ana.body_script.component.vars[342].value(), 0)
                    self.assertIsNone(caster.body_script.component.fault)

    def test_friendly_rifle_grenade_healing_and_missed_shots_give_no_energy(self):
        for other in (False, True):
            for button in (1, 16):
                with self.subTest(projected=other, button=button):
                    run, ana, caster, entry, barrier = self.fight(other, friendly=True)
                    fight = run.match.combat
                    fight.vitals_of(entry).damage(100)
                    direction = aim(fight._eye(ana),
                                    (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                    run.step({ana: (*direction, button)})
                    for _ in range(60):
                        run.step({ana: (*direction, 0)})
                    self.assertGreater(fight.vitals_of(entry).total,
                                       sum(fight.vitals_of(entry).most.values()) - 100)
                    self.assertEqual(barrier.health, 200)
                    self.assertEqual(energy(caster), 0)
        run, ana, caster, _entry, barrier = self.fight()
        run.step({ana: (-16384, 0, 1)})
        for _ in range(60):
            run.step({ana: (-16384, 0, 0)})
        self.assertEqual((barrier.health, energy(caster)), (200, 0))

    def test_breaking_hit_credits_effective_pool_loss_once_and_authored_cap_is100(self):
        for initial, expected in ((0, 40), (95, 100)):
            with self.subTest(initial=initial):
                run, ana, caster, entry, barrier = self.fight()
                caster.body_script.component.set(87, initial)
                fight = run.match.combat
                fight._damage(ana, entry, 1000, False, None, run.frame)
                self.assertEqual((barrier.health, energy(caster)), (0, expected))
                self.assertEqual(fight.vitals_of(entry).total, 200)
                fight._damage(ana, entry, 5, False, None, run.frame)
                self.assertEqual(energy(caster), expected)
                self.assertEqual(fight.vitals_of(entry).total, 195)

    def test_native_gain_restarts_delay_then_graph_decays_to_zero(self):
        run, ana, caster, entry, _barrier = self.fight()
        fight = run.match.combat
        fight._damage(ana, entry, 70, False, None, run.frame)
        for _ in range(50):
            run.step({})
        self.assertEqual(energy(caster), 14)
        fight._damage(ana, entry, 50, False, None, run.frame)
        self.assertEqual(energy(caster), 24)
        for _ in range(60):
            run.step({})
        self.assertEqual(energy(caster), 24)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(energy(caster), 24 - (80 * .016 - 1) * 1.8, delta=.04)
        for _ in range(1000):
            run.step({})
        self.assertEqual(energy(caster), 0)

    def test_caster_death_or_replacement_does_not_credit_new_or_retired_body(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, ana, caster, entry, barrier = self.fight()
                run.match.combat._damage(ana, entry, 70, False, None, run.frame)
                self.assertEqual(energy(caster), 14)
                old = caster.body_script
                if death:
                    own = next(t for t in run.match.combat._targets() if t.owner is caster)
                    run.match.combat._damage(ana, own, 10000, False, None, run.frame)
                else:
                    self.assertTrue(run.match.switch_hero(caster, find_hero('Zarya')))
                before = energy(caster)
                run.match.combat._damage(ana, entry, 70, False, None, run.frame)
                self.assertEqual(barrier.health, 60)
                self.assertEqual(energy(caster), before)
                slot = old.component.entity_var(87, create=False)
                self.assertEqual(slot.value(), 14)

    def test_nano_offense_increases_energy_but_recipient_reduction_does_not(self):
        for target_nano in (False, True):
            with self.subTest(target_nano=target_nano):
                run, ana, caster, entry, barrier = self.fight()
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is ana)
                fight.vitals_of(own).nano_until = fight.now + 8
                if target_nano:
                    fight.vitals_of(entry).nano_until = fight.now + 8
                self.contact(run, ana, entry, barrier, 1)
                self.assertEqual((barrier.health, energy(caster)), (95, 21))

    def test_same_caster_personal_and_projected_pools_feed_one_native_energy_value(self):
        run, ana, caster, entry, projected_barrier = self.fight()
        run.step({caster: (0, 0, 8)})
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is caster)
        personal_barrier = fight.body_barriers[caster.body]
        fight._damage(ana, entry, 70, False, None, run.frame)
        fight._damage(ana, own, 50, False, None, run.frame)
        self.assertEqual((projected_barrier.health, personal_barrier.health), (130, 150))
        self.assertEqual(energy(caster), 24)

    def test_expired_protection_does_not_generate_energy_from_body_damage(self):
        run, ana, caster, entry, barrier = self.fight()
        for _ in range(130):
            run.step({})
        self.assertGreaterEqual(run.match.combat.now, barrier.until)
        run.match.combat._damage(ana, entry, 70, False, None, run.frame)
        self.assertEqual(energy(caster), 0)
        self.assertEqual(run.match.combat.vitals_of(entry).total, 130)

    def test_actual_ana_contact_then_zarya_secondary_uses_authored_energy_damage(self):
        for gained in (False, True):
            with self.subTest(gained=gained):
                run, ana, caster, entry, barrier = self.fight(False)
                if gained:
                    self.contact(run, ana, entry, barrier, 1)
                self.assertEqual(energy(caster), 14 if gained else 0)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is ana)
                direction = aim(fight._eye(caster),
                                (own.feet[0], own.feet[1] + own.height / 2, own.feet[2]))
                run.step({caster: (*direction, 2)})
                for _ in range(55):
                    run.step({caster: (*direction, 0)})
                #0241 uses47 + energy*.01*(95-47), including native f32 rounding.
                expected = 53.720001220703125 if gained else 47
                self.assertAlmostEqual(fight.vitals_of(own).total, 200 - expected)
                self.assertIsNone(caster.body_script.component.fault)

    def test_secondary_keeps_firing_time_energy_when_more_damage_is_absorbed_in_flight(self):
        run, ana, caster, entry, barrier = self.fight(False)
        self.contact(run, ana, entry, barrier, 1)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ana)
        direction = aim(fight._eye(caster),
                        (own.feet[0], own.feet[1] + own.height / 2, own.feet[2]))
        run.step({caster: (*direction, 2)})
        for _ in range(10):
            if fight.rockets:
                break
            run.step({caster: (*direction, 0)})
        rocket = next(rocket for rocket in fight.rockets if rocket.source_entity == caster.body)
        self.assertEqual(rocket.blast.amount, 53.720001220703125)
        fight._damage(ana, entry, 50, False, None, run.frame)
        self.assertEqual(energy(caster), 24)
        self.assertEqual(rocket.blast.amount, 53.720001220703125)
        for _ in range(55):
            run.step({caster: (*direction, 0)})
        self.assertAlmostEqual(fight.vitals_of(own).total, 200 - 53.720001220703125)


if __name__ == '__main__':
    unittest.main()
