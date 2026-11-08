"""Actual enemy Zarya weapon inputs against personal/projected barriers."""

import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.content import heroes
from ow174.game.script import expr

ZARYA = 0x02E0000000000068


class ZaryaBarrierWeaponTests(unittest.TestCase):
    def encounter(self, projected, *, close=False):
        run = Run(names=("Attacker", "Caster", "Ally"))
        attacker, caster, ally = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in (attacker, caster):
            self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        caster.team = ally.team = 1
        attacker.team = 0
        attacker.mover.position[:] = [0, 1, 3.5 if close else 0]
        caster.mover.position[:] = [3 if projected else 0, 1, 5]
        ally.mover.position[:] = [0, 1, 5 if projected else 20]
        victim = ally if projected else caster
        run.step({caster: (-16384 if projected else 0, 0, 16 if projected else 8)})
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == (0x025C if projected else 0x0244))
        self.assertTrue(instance.state(10 if projected else 2).active)
        return run, fight, attacker, caster, victim

    @staticmethod
    def health(fight, owner):
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target).total

    @staticmethod
    def energy(caster):
        return caster.body_script.component.entity_var(87).value()

    def weapon(self, projected, button, frames, *, energy=0):
        run, fight, attacker, caster, victim = self.encounter(projected, close=button == 0x800)
        before = self.health(fight, victim)
        attacker.body_script.component.entity_var(87).base = energy
        run.step({attacker: (0, 0, button)})
        for _ in range(frames - 1):
            run.step({attacker: (0, 0, button if button == 1 else 0)})
        self.assertEqual(self.health(fight, victim), before)
        self.assertFalse(attacker.client.messages(combat.HIT))
        energy_graph = next(i for i in caster.body_script.component.instances.values()
                            if i.graph.index == 0x0242)
        self.assertEqual(energy_graph.vars[392].value(), expr.Entity(attacker.body))
        storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
        return caster, storage[caster.body]

    def test_primary_60_absorbed_into_personal_and_projected_gives_12_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 1, 51)
                self.assertAlmostEqual(self.energy(caster), 12, places=4)
                self.assertAlmostEqual(barrier.remaining, 140, places=4)

    def test_full_energy_primary_136_absorbed_gives_27_2_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 1, 51, energy=100)
                self.assertAlmostEqual(self.energy(caster), 27.2, places=4)
                self.assertAlmostEqual(barrier.remaining, 64, places=4)

    def test_secondary_47_absorbed_into_both_barriers_gives_9_4_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 2, 60)
                self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
                self.assertAlmostEqual(barrier.remaining, 153, places=4)

    def test_melee_30_absorbed_into_both_barriers_gives_6_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 0x800, 41)
                self.assertAlmostEqual(self.energy(caster), 6)
                self.assertAlmostEqual(barrier.remaining, 170)

    def test_barriers_block_actual_graviton_impact_and_initial_field_damage(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, caster, victim = self.encounter(projected)
                before = self.health(fight, victim)
                attacker.body_script.set_ult_charge(2100)
                run.step({attacker: (0, 0, 0x20)})
                for _ in range(60):
                    run.step({})
                self.assertTrue(fight.zarya_gravitons.fields)
                self.assertEqual(self.health(fight, victim), before)
                self.assertGreater(self.energy(caster), 1)
                for _ in range(110):
                    run.step({})
                self.assertLess(self.health(fight, victim), before)

    def test_actual_high_energy_beam_breaks_both_barriers_then_damages_body(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, caster, victim = self.encounter(projected)
                before = self.health(fight, victim)
                attacker.body_script.component.entity_var(87).base = 100
                for _ in range(100):
                    run.step({attacker: (0, 0, 1)})
                self.assertLess(self.health(fight, victim), before)
                self.assertGreater(self.health(fight, victim), 0)
                self.assertAlmostEqual(self.energy(caster), 40, places=4)
                storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                self.assertNotIn(caster.body, storage)
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == (0x025C if projected else 0x0244))
                self.assertFalse(instance.state(10 if projected else 2).active)
                self.assertGreater(instance.vars[61].value(), 0)

    def absorbed_secondary(self):
        run, fight, attacker, caster, victim = self.encounter(False)
        run.step({attacker: (0, 0, 2)})
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
        energy_graph = next(i for i in caster.body_script.component.instances.values()
                            if i.graph.index == 0x0242)
        wait = energy_graph.state(energy_graph.graph.node(7).state)
        return run, fight, attacker, caster, victim, wait

    def test_energy_waits_one_second_then_decays_at_authored_1_8_per_second(self):
        run, _, _, caster, _, wait = self.absorbed_secondary()
        component = caster.body_script.component
        while component.now < wait.start + 900:
            run.step({})
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
        while component.now < wait.start + 1200:
            run.step({})
        before, now = self.energy(caster), component.now
        self.assertLess(before, 9.4)
        for _ in range(63):
            run.step({})
        # The shared ChaseVar applies its native-style epsilon and float32 steps.
        self.assertAlmostEqual(before - self.energy(caster),
                               1.8 * (component.now - now) / 1000, delta=0.003)

    def test_later_absorption_restarts_delay_and_pauses_existing_decay(self):
        run, _, attacker, caster, _, wait = self.absorbed_secondary()
        first_start = wait.start
        while caster.body_script.component.now < first_start + 1200:
            run.step({})
        before = self.energy(caster)
        self.assertLess(before, 9.4)
        run.step({attacker: (0, 0, 2)})
        before_hit = self.energy(caster)
        for _ in range(30):
            before_hit = self.energy(caster)
            run.step({})
            if wait.start > first_start:
                break
        self.assertGreater(wait.start, first_start)
        # Decay continues during the second projectile's flight; only its actual
        # absorption restarts the delay. Allow one final16ms chase step at impact.
        self.assertAlmostEqual(self.energy(caster) - before_hit, 9.4, delta=0.04)
        after = self.energy(caster)
        for _ in range(50):
            run.step({})
        self.assertEqual(self.energy(caster), after)

    def test_energy_decay_clamps_at_zero_and_does_not_become_negative(self):
        run, _, _, caster, _, _ = self.absorbed_secondary()
        for _ in range(450):
            run.step({})
        self.assertEqual(self.energy(caster), 0)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.energy(caster), 0)

    def test_death_and_body_replacement_discard_absorbed_energy_and_pool(self):
        run, fight, attacker, caster, victim, _ = self.absorbed_secondary()
        target = next(t for t in fight._targets() if t.owner is victim)
        fight._damage(attacker, target, 500, False, None, run.frame)
        fight._damage(attacker, target, 500, False, None, run.frame)
        self.assertTrue(fight.is_dead(caster))
        old = caster.body
        self.assertTrue(run.match.switch_hero(caster, heroes()[ZARYA]))
        self.assertNotEqual(caster.body, old)
        self.assertEqual(expr.to_float(self.energy(caster)), 0)
        self.assertNotIn(old, fight.zarya_barriers.active)


if __name__ == "__main__":
    unittest.main()
