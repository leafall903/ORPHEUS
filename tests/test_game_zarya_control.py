"""Actual Flashbang inputs vs Zarya attacks and both barrier host services."""

import unittest

import test_mccree_flashbang as flash
from test_game_combat import Run

from ow174.game.content import heroes

ZARYA, MCCREE = 0x02E0000000000068, 0x02E0000000000042


class ZaryaControlTests(unittest.TestCase):
    step = flash.FlashbangTests.step

    def arena(self, *, projected=False, far_target=False):
        names = ("Flashbang", "Victim", "Zarya") if projected or far_target else ("Flashbang", "Zarya")
        run = Run(names=names)
        source, victim, *others = run.players
        caster = others[0] if projected else victim
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(source, heroes()[MCCREE]))
        self.assertTrue(run.match.switch_hero(caster, heroes()[ZARYA]))
        source.team = 0
        caster.team = victim.team = 1
        source.mover.position[:] = [0, 1, 0]
        victim.mover.position[:] = [0, 1, 3]
        if projected:
            caster.mover.position[:] = [4, 1, 3]
        elif far_target:
            others[0].team = source.team
            others[0].mover.position[:] = [0, 1, 9]
        for _ in range(20):
            self.step(run, source, victim)
        return run, source, victim, caster

    @staticmethod
    def health(run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner)).total

    @staticmethod
    def ability(caster, projected=False):
        return next(i for i in caster.body_script.component.instances.values()
                    if i.graph.index == (0x025C if projected else 0x0244))

    def throw(self, run, source, victim, *, buttons=0, until_stun=False):
        self.step(run, source, victim, cast=16, target_buttons=buttons)
        for _ in range(35):
            if until_stun and run.match.combat.is_stunned(victim):
                return
            self.step(run, source, victim, target_buttons=buttons)
        if until_stun:
            self.fail("Actual Flashbang did not stun the unprotected body")

    def test_personal_barrier_prevents_actual_flash_damage_and_stun(self):
        run, source, victim, caster = self.arena()
        run.step({caster: (-32768, 0, 8)})
        self.assertTrue(self.ability(caster).state(2).active)
        self.throw(run, source, victim, buttons=1)
        self.assertEqual(self.health(run, victim), 400)
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 5)
        self.assertLess(self.health(run, source), 225)

    def test_projected_barrier_prevents_actual_flash_damage_and_stun_on_ally(self):
        run, source, victim, caster = self.arena(projected=True)
        run.step({caster: (-16384, 0, 16)})
        self.assertTrue(self.ability(caster, True).state(10).active)
        self.throw(run, source, victim)
        self.assertEqual(self.health(run, victim), 200)
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 5)

    def test_flash_that_exhausts_either_barrier_still_cannot_stun_body(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, source, victim, caster = self.arena(projected=projected)
                run.step({caster: (-16384 if projected else -32768, 0, 16 if projected else 8)})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is victim)
                # Capacity setup uses production damage; the final breaking
                # Flashbang comes from its real input/projectile/effect path.
                fight._damage(source, target, 190, False, None, run.frame)
                before = self.health(run, victim)
                self.throw(run, source, victim)
                self.assertEqual(self.health(run, victim), before)
                self.assertFalse(fight.is_stunned(victim))
                self.assertEqual(caster.body_script.component.entity_var(87).value(), 40)
                self.assertFalse(self.ability(caster, projected).state(10 if projected else 2).active)

    def test_flash_stops_primary_and_movement_then_new_press_recovers(self):
        run, source, victim, _ = self.arena()
        for _ in range(10):
            self.step(run, source, victim, target_buttons=1)
        self.assertLess(self.health(run, source), 225)
        self.throw(run, source, victim, buttons=1, until_stun=True)
        fight = run.match.combat
        self.assertEqual(self.health(run, victim), 375)
        end = fight.stuns[victim.body]
        before, position = self.health(run, source), tuple(victim.mover.position)
        while run.frame < end - 1:
            self.step(run, source, victim, target_buttons=1, forward=127)
            self.assertEqual(self.health(run, source), before)
            self.assertEqual(tuple(victim.mover.position), position)
        self.step(run, source, victim)
        for _ in range(25):
            self.step(run, source, victim, target_buttons=1, forward=127)
        self.assertLess(self.health(run, source), before)
        self.assertNotEqual(tuple(victim.mover.position), position)

    def test_stunned_body_cannot_start_barriers_or_spend_ultimate_charge(self):
        run, source, victim, _ = self.arena()
        self.throw(run, source, victim, until_stun=True)
        victim.body_script.ult_seconds = 0
        victim.body_script.set_ult_charge(2100)
        end = run.match.combat.stuns[victim.body]
        while run.frame < end - 1:
            self.step(run, source, victim, target_buttons=8 | 16 | 0x20)
            self.assertFalse(self.ability(victim).state(2).active)
            self.assertFalse(self.ability(victim, True).state(10).active)
            self.assertEqual(victim.body_script.component.entity_var(342).value(), 2100)
            self.assertFalse(any(r.shooter is victim for r in run.match.combat.rockets))
        self.step(run, source, victim)
        self.step(run, source, victim, target_buttons=8)
        self.assertTrue(self.ability(victim).state(2).active)

    def test_secondary_already_in_flight_survives_owner_stun(self):
        run, source, victim, _ = self.arena(far_target=True)
        far = run.players[2]
        run.step({source: (0, 0, 16), victim: (0, 0, 2)})
        for _ in range(35):
            if run.match.combat.is_stunned(victim):
                break
            self.step(run, source, victim)
        self.assertTrue(run.match.combat.is_stunned(victim))
        self.assertEqual(self.health(run, far), 200)
        self.assertTrue(any(r.shooter is victim for r in run.match.combat.rockets))
        for _ in range(35):
            self.step(run, source, victim)
        self.assertAlmostEqual(self.health(run, far), 153)

    def test_deployed_graviton_continues_after_owner_stun(self):
        run, source, victim, _ = self.arena(far_target=True)
        far = run.players[2]
        victim.body_script.set_ult_charge(2100)
        run.step({victim: (0, 0, 0x20)})
        for _ in range(40):
            if run.match.combat.zarya_gravitons.fields:
                break
            self.step(run, source, victim)
        self.assertTrue(run.match.combat.zarya_gravitons.fields)
        self.throw(run, source, victim, until_stun=True)
        before = self.health(run, far)
        self.assertTrue(run.match.combat.zarya_gravitons.fields)
        for _ in range(30):
            self.step(run, source, victim)
        self.assertLess(self.health(run, far), before)


if __name__ == "__main__":
    unittest.main()
