"""Actual Ana and Reaper input through the reconstructed Wraith phase."""

import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import wraith
from ow174.game.content import heroes


class AnaWraithTests(unittest.TestCase):
    def fight(self, *, enemy=True, distance=6):
        run, ana, reaper, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(reaper, heroes()[wraith.HERO]))
        x, y, z = ana.mover.position
        reaper.mover.position = [x, y, z + distance]
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is reaper)
        return run, ana, reaper, target, fight.vitals_of(target)

    def enter(self, run, reaper):
        run.step({reaper: (0, 0, 8)})
        self.assertTrue(wraith.active(reaper))

    def attack(self, run, ana, target, button):
        direction = aim(run.match.combat._eye(ana),
                        (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({ana: (*direction, button)})
        for _ in range(64):
            run.step({ana: (*direction, 0)})

    def test_wraith_ignores_hip_and_scoped_and_expires(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, ana, reaper, target, vitals = self.fight()
                self.enter(run, reaper)
                started = run.frame
                rifle = fixture.AnaRifleTests()
                rifle.shoot(run, ana, target, scoped)
                self.assertTrue(wraith.active(reaper))
                self.assertEqual(vitals.total, 250)
                while run.frame < started + 186:
                    run.step({})
                self.assertTrue(wraith.active(reaper))
                run.step({})
                self.assertFalse(wraith.active(reaper))
                rifle.shoot(run, ana, target, scoped)
                self.assertEqual(vitals.total, 180)

    def test_wraith_rejects_dart_grenade_contact_and_melee(self):
        for button, distance, damage in ((8, 6, 5), (16, 2.5, 50), (0x800, 1.5, 30)):
            for protected in (False, True):
                with self.subTest(button=button, protected=protected):
                    run, ana, reaper, target, vitals = self.fight(distance=distance)
                    if protected:
                        self.enter(run, reaper)
                    self.attack(run, ana, target, button)
                    self.assertEqual(vitals.total, 250 if protected else 250 - damage)
                    self.assertEqual(run.match.combat.is_asleep(reaper), button == 8 and not protected)
                    self.assertEqual(vitals.healing_block_until > run.match.combat.now,
                                     button == 16 and not protected)

    def test_wraith_rejects_nearby_grenade_blast_without_direct_contact(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, ana, reaper, target, vitals = self.fight(distance=6)
                if protected:
                    self.enter(run, reaper)
                x, y, z = target.feet
                direction = fixture.grenade_aim(run, ana, (x + 1.5, y, z))
                run.step({ana: (*direction, 16)})
                for _ in range(64):
                    run.step({ana: (*direction, 0)})
                self.assertEqual(vitals.total, 250 if protected else 200)
                self.assertEqual(vitals.healing_block_until > run.match.combat.now, not protected)

    def test_wraith_cleanses_actual_antiheal_and_pending_rifle_damage(self):
        run, ana, reaper, target, vitals = self.fight()
        fight = run.match.combat
        self.attack(run, ana, target, 16)
        self.assertGreater(vitals.healing_block_until, fight.now)
        direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({ana: (*direction, 1)})
        for _ in range(20):
            run.step({})
            if fight.timed_hits:
                break
        self.assertTrue(any(hit.target == reaper.body for hit in fight.timed_hits))
        self.enter(run, reaper)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertFalse(any(hit.target == reaper.body for hit in fight.timed_hits))
        before = vitals.total
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before)

    def test_wraith_preserves_allied_rifle_healing_and_early_shift_cancel(self):
        run, ana, reaper, target, vitals = self.fight(enemy=False)
        vitals.damage(100)
        self.enter(run, reaper)
        fixture.AnaRifleTests().shoot(run, ana, target, False)
        self.assertEqual(vitals.total, 220)
        run.step({reaper: (0, 0, 8)})
        self.assertFalse(wraith.active(reaper))
        for _ in range(35):
            run.step({})
        ability = next(i.states[10] for i in reaper.body_script.component.instances.values()
                       if i.graph.index == wraith.GRAPH)
        self.assertEqual(ability.cur, 8)
        run.step({reaper: (0, 0, 8)})
        self.assertFalse(wraith.active(reaper))  # recovery/cooldown still running
        for _ in range(510):
            run.step({})
            if ability.remaining() == 0:
                break
        self.assertFalse(wraith.active(reaper))
        self.assertEqual(ability.remaining(), 0)
        self.enter(run, reaper)

    def test_allied_nano_acquires_and_heals_reaper_during_wraith(self):
        run, ana, reaper, target, vitals = self.fight(enemy=False)
        vitals.damage(120)
        self.enter(run, reaper)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.attack(run, ana, target, 32)
        self.assertTrue(wraith.active(reaper))
        self.assertEqual(vitals.total, 250)
        self.assertGreater(vitals.nano_until, run.match.combat.now)

    def test_sleep_prevents_entry_and_replacement_retires_wraith(self):
        run, ana, reaper, target, _ = self.fight()
        self.attack(run, ana, target, 8)
        self.assertTrue(run.match.combat.is_asleep(reaper))
        run.step({reaper: (0, 0, 8)})
        self.assertFalse(wraith.active(reaper))
        for _ in range(330):
            run.step({})
        self.enter(run, reaper)
        self.assertTrue(run.match.switch_hero(reaper, heroes()[fixture.ANA]))
        self.assertFalse(wraith.active(reaper))
