"""Actual personal/projected barrier and Pharah combat inputs."""

import unittest
from dataclasses import replace

import test_pharah_defenses as defense

from ow174.game.zarya_barrier import PERSONAL, PROJECTED


class ZaryaProjectileTests(unittest.TestCase):
    def arena(self, projected, *, distance=8):
        fixture = defense.PharahDefenseTests()
        run, shooter, caster, ally = fixture.encounter("Zarya")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = 0
        caster.team = ally.team = 1
        y = caster.mover.position[1]
        caster.mover.position = [4.5 if projected else 0, y, distance]
        ally.mover.position = [0 if projected else 20, y, distance]
        caster.mover.body = ally.mover.body = None
        target = ally if projected else caster
        return fixture, run, shooter, caster, target

    def deploy(self, run, caster, target, projected):
        fight = run.match.combat
        run.step({caster: (-16384 if projected else 0, 1000 if projected else 0, 16 if projected else 8)})
        for _ in range(3):
            run.step({})
        key = caster.body, PROJECTED if projected else PERSONAL
        self.assertIn(key, fight.zarya_bubbles)
        bubble = fight.zarya_bubbles[key]
        self.assertIs(bubble.target, target)
        self.assertEqual(bubble.health, 200)
        return bubble

    def encounter(self, projected=False):
        fixture, run, shooter, caster, target = self.arena(projected)
        bubble = self.deploy(run, caster, target, projected)
        return fixture, run, shooter, caster, target, bubble

    def test_primary_consumes_personal_and_projected_pool_and_awards_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                fixture, run, shooter, caster, target, bubble = self.encounter(projected)
                before = fixture.health(run, target).total
                run.step({shooter: (0, 0, 1)})
                for _ in range(45):
                    if bubble.health < 200:
                        break
                    run.step({})
                self.assertEqual(bubble.health, 80)
                self.assertEqual(fixture.health(run, target).total, before)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 24, places=4)
                for _ in range(95):
                    run.step({shooter: (0, 0, 1)})
                    if bubble.health == 0:
                        break
                self.assertEqual(bubble.health, 0)
                self.assertEqual(fixture.health(run, target).total, before)
                self.assertIsNone(bubble.phase())
                self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_projected_barrier_deployed_in_flight_intercepts_rocket_or_concussion(self):
        for button in (1, 16):
            for protected in (False, True):
                with self.subTest(button=button, protected=protected):
                    fixture, run, shooter, caster, target = self.arena(True, distance=24)
                    fight = run.match.combat
                    vitals = fixture.health(run, target)
                    before = vitals.total
                    position = tuple(target.mover.position)
                    run.step({shooter: (0, 0, button)})
                    for _ in range(35):
                        if any(rocket.shooter is shooter for rocket in fight.rockets):
                            break
                        run.step({})
                    self.assertTrue(any(rocket.shooter is shooter for rocket in fight.rockets))
                    self.assertFalse(fight.zarya_bubbles)
                    if protected:
                        bubble = self.deploy(run, caster, target, True)
                        self.assertTrue(any(rocket.shooter is shooter for rocket in fight.rockets))
                    self.assertEqual(vitals.total, before)
                    for _ in range(75):
                        if not any(rocket.shooter is shooter for rocket in fight.rockets):
                            break
                        run.step({})
                    self.assertFalse(any(rocket.shooter is shooter for rocket in fight.rockets))
                    energy = caster.body_script.component.entity_var(87).value() or 0
                    charge = shooter.body_script.component.entity_var(342).value()
                    if protected:
                        self.assertEqual(bubble.health, 80 if button == 1 else 200)
                        self.assertAlmostEqual(energy, 24 if button == 1 else 0, places=4)
                        self.assertEqual(charge, 0)
                    elif button == 1:
                        self.assertEqual(charge, 120)
                    self.assertEqual(vitals.total, before - (120 if button == 1 and not protected else 0))
                    if button == 16:
                        for _ in range(20):
                            run.step({})
                        if protected:
                            self.assertEqual(tuple(target.mover.position), position)
                        else:
                            self.assertGreater(target.mover.position[2], position[2] + 1)

    def test_projected_barrier_expiry_rechecks_in_flight_primary_and_concussion(self):
        # At 24 m these input offsets place contact on the expiry update
        # or leave the projectile in flight at the end of that update.
        for button, last_protected_launch in ((1, 79), (16, 86)):
            for delayed in (False, True):
                with self.subTest(button=button, delayed=delayed):
                    fixture, run, shooter, caster, target = self.arena(True, distance=24)
                    fight = run.match.combat
                    bubble = self.deploy(run, caster, target, True)
                    expiry = run.frame + 121
                    vitals = fixture.health(run, target)
                    before = vitals.total
                    position = tuple(target.mover.position)
                    for _ in range(last_protected_launch + delayed):
                        run.step({})
                    run.step({shooter: (0, 0, button)})
                    while run.frame < expiry - 1:
                        run.step({})
                    self.assertIsNotNone(bubble.phase())
                    shot, = (rocket for rocket in fight.rockets if rocket.shooter is shooter)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(bubble.health, 200)

                    run.step({})
                    self.assertIsNone(bubble.phase())
                    self.assertEqual(shot in fight.rockets, delayed)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(bubble.health, 80 if button == 1 and not delayed else 200)
                    for _ in range(20):
                        run.step({})
                    self.assertNotIn(shot, fight.rockets)
                    self.assertEqual(vitals.total, before - (120 if button == 1 and delayed else 0))
                    if button == 16 and delayed:
                        self.assertGreater(target.mover.position[2], position[2] + 1)
                    elif not delayed:
                        self.assertEqual(tuple(target.mover.position), position)

    def test_body_replacement_retires_projected_barrier_before_in_flight_impact(self):
        for button in (1, 16):
            for replace_caster in (False, True):
                with self.subTest(button=button, replace_caster=replace_caster):
                    fixture, run, shooter, caster, target = self.arena(True, distance=24)
                    fight = run.match.combat
                    bubble = self.deploy(run, caster, target, True)
                    run.step({shooter: (0, 0, button)})
                    for _ in range(35):
                        if fight.rockets:
                            break
                        run.step({})
                    shot, = (rocket for rocket in fight.rockets if rocket.shooter is shooter)
                    self.assertIsNotNone(bubble.phase())
                    self.assertEqual(bubble.health, 200)

                    replaced = caster if replace_caster else target
                    old_body = replaced.body
                    position = tuple(replaced.mover.position)
                    self.assertTrue(run.match.switch_hero(replaced, replaced.hero))
                    self.assertNotEqual(replaced.body, old_body)
                    replaced.mover.position = list(position)
                    replaced.mover.body = None
                    self.assertFalse(fight.zarya_bubbles)
                    self.assertIn(shot, fight.rockets)
                    vitals = fixture.health(run, target)
                    before = vitals.total
                    target_z = target.mover.position[2]
                    for _ in range(75):
                        run.step({})
                    self.assertNotIn(shot, fight.rockets)
                    self.assertEqual(bubble.health, 200)
                    self.assertEqual(vitals.total, before - (120 if button == 1 else 0))
                    self.assertEqual(shooter.body_script.component.entity_var(342).value(),
                                     120 if button == 1 else 0)
                    if button == 16:
                        self.assertGreater(target.mover.position[2], target_z + 1)

    def test_personal_bubble_blocks_concussion_then_absorbs_melee(self):
        fixture, run, shooter, caster, target, bubble = self.encounter()
        position = tuple(target.mover.position)
        before = fixture.health(run, target).total
        run.step({shooter: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertEqual(tuple(target.mover.position), position)
        self.assertEqual(bubble.health, 200)
        shooter.mover.position[2] = 6.4
        shooter.mover.body = None
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(35):
            if bubble.health < 200:
                break
            run.step({})
        self.assertEqual(bubble.health, 170)
        self.assertEqual(fixture.health(run, target).total, before)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 6, places=4)

    def test_projected_bubble_takes_splash_from_nearby_rocket_contact(self):
        fixture, run, shooter, caster, target, bubble = self.encounter(projected=True)
        shooter.mover.position[0] = caster.mover.position[0] = 2.3
        shooter.mover.body = caster.mover.body = None
        before = fixture.health(run, target).total
        caster_before = fixture.health(run, caster).total
        fixture.fire(run, shooter)
        self.assertLess(fixture.health(run, caster).total, caster_before)
        self.assertGreater(bubble.health, 80)
        self.assertLess(bubble.health, 200)
        self.assertEqual(fixture.health(run, target).total, before)

    def test_barrage_breaking_round_is_absorbed_and_next_round_hits(self):
        for projected, prehit in ((False, False), (True, False), (True, True)):
            with self.subTest(projected=projected, prehit=prehit):
                fixture, run, shooter, caster, target, bubble = self.encounter(projected)
                health = fixture.health(run, target)
                before = health.total
                if prehit:
                    shooter.mover.position[2] = 6.4
                    shooter.mover.body = None
                    run.step({shooter: (0, 0, 0x800)})
                    for _ in range(35):
                        if bubble.health < 200:
                            break
                        run.step({})
                    self.assertEqual(bubble.health, 170)
                    self.assertEqual(health.total, before)
                shooter.mover.position[2] = 4
                shooter.mover.body = None
                yaw, pitch = fixture.aim_at_body(run.match.combat, shooter, target)
                script = shooter.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({shooter: (yaw, pitch, 32)})
                pools = [bubble.health]
                for _ in range(60):
                    run.step({shooter: (yaw, pitch, 0)})
                    self.assertEqual(health.total, before)
                    if bubble.health != pools[-1]:
                        pools.append(bubble.health)
                    if bubble.health == 0:
                        break
                expected = [170, 130, 90, 50, 10, 0] if prehit else [200, 160, 120, 80, 40, 0]
                self.assertEqual(pools, expected)
                self.assertIsNone(bubble.phase())
                self.assertGreater(caster.body_script.component.entity_var(87).value(), 0)
                for _ in range(24):
                    run.step({shooter: (yaw, pitch, 0)})
                    if health.total < before:
                        break
                self.assertEqual(health.total, before - 40)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_barrage_debits_projected_pool_and_personal_scope_expires(self):
        fixture, run, shooter, _, target, bubble = self.encounter(projected=True)
        before = fixture.health(run, target).total
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({shooter: (0, 0, 32)})
        for _ in range(15):
            run.step({})
        self.assertGreater(bubble.health, 0)
        self.assertLess(bubble.health, 200)
        self.assertEqual(fixture.health(run, target).total, before)

        fixture, run, shooter, _, target, bubble = self.encounter()
        for _ in range(130):
            run.step({})
        self.assertIsNone(bubble.phase())
        before = fixture.health(run, target).total
        fixture.fire(run, shooter)
        self.assertLess(fixture.health(run, target).total, before)
        self.assertEqual(bubble.health, 200)
