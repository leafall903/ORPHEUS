"""Actual Sigma Flux input and Zarya barrier timing."""

import unittest

from test_game_combat import Run, aim

from ow174.game import combat, defenses, phase, sigma_flux
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ZaryaSigmaFluxTests(unittest.TestCase):
    def arena(self, *, projected=False):
        run = Run(names=("Sigma", "Target", "Other"))
        sigma, target, other = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        caster = other if projected else target
        self.assertTrue(match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
        sigma.team, target.team, other.team = 0, 1, 1 if projected else 0
        for player, position in ((sigma, (0, 1, 0)), (target, (0, 1, 5)), (other, (10, 1, 5))):
            player.mover.position[:] = position
            player.mover.body = None
        return run, sigma, target, other, caster

    @staticmethod
    def effect(target):
        return next((i for i in target.body_script.component.instances.values()
                     if i.graph.index == sigma_flux.FLUX_EFFECT), None)

    def select(self, run, sigma, point, *, charge=True):
        component = sigma.body_script.component
        if charge:
            sigma.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
        for frame in range(81):
            angles = aim(run.match.combat._eye(sigma), point)
            run.step({sigma: (*angles, 32 if frame == 0 else 1 if frame == 80 else 0)})
        return next(i for i in component.instances.values() if i.graph.index == sigma_flux.FLUX)

    def test_actual_flux_entry_and_slam_and_barrier_timing(self):
        for case in ("bare", "entry", "personal_slam", "projected_slam"):
            with self.subTest(case=case):
                projected = case == "projected_slam"
                run, sigma, target, _, caster = self.arena(projected=projected)
                fight = run.match.combat
                receiver = next(t for t in fight._targets() if t.owner is target)
                vitals = fight.vitals_of(receiver)
                maximum = vitals.maximum
                point = tuple(target.mover.position)
                instance = self.select(run, sigma, point)
                self.assertEqual(sigma.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(instance.vars[59].value(), 2)
                self.assertAlmostEqual(instance.vars[2478].value().z, point[2], delta=0.02)
                if case == "entry":
                    for _ in range(40):
                        run.step({})
                    run.step({caster: (0, 0, 8)})
                for _ in range(100):
                    run.step({})
                    if self.effect(target) is not None:
                        break
                effect = self.effect(target)
                self.assertIsNotNone(effect)
                self.assertEqual(effect.source_entity(), sigma.body)
                self.assertEqual(instance.vars[375].value(), (expr.Entity(target.body),))
                self.assertEqual(vitals.total, maximum - (0 if case == "entry" else 50))
                if case == "entry":
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10)
                peak = point[1]
                for _ in range(260):
                    run.step({})
                    y = target.mover.position[1]
                    peak = max(peak, y)
                    if peak > 10 and 3 < y < 8 and target.mover.velocity[1] < -1:
                        break
                self.assertGreater(peak, 10)
                self.assertTrue(target.mover.airborne)
                if case.endswith("slam"):
                    angles = aim(fight._eye(caster), fight._eye(target)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                for _ in range(35):
                    run.step({})
                self.assertFalse(target.mover.airborne)
                expected = maximum - (0 if case == "entry" else 50)
                if case == "personal_slam":
                    # Entry damage was old enough for a small shield refill by this sample.
                    self.assertGreaterEqual(vitals.total, expected)
                    self.assertLess(vitals.total, expected + 3)
                    self.assertEqual(len(sigma.client.messages(combat.HIT)), 1)
                else:
                    self.assertEqual(vitals.total, expected - (0 if projected else maximum / 2))
                if case.endswith("slam"):
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), maximum / 10)
                for _ in range(230):
                    run.step({})
                self.assertIsNone(self.effect(target))

    def test_flux_rejects_zero_charge_and_excludes_ally_range_and_cover(self):
        for case in ("no_charge", "ally", "distant", "wall"):
            with self.subTest(case=case):
                run, sigma, target, _, _ = self.arena()
                if case == "ally":
                    target.team = sigma.team
                point = (12, 1, 5) if case == "distant" else tuple(target.mover.position)
                if case == "wall":
                    point = (0, 1, 2)
                    run.match.collision_world = World([
                        (-20, 1, -20, -20, 1, 20, 20, 1, 20),
                        (-20, 1, -20, 20, 1, 20, 20, 1, -20),
                        (-10, 1, 3, 10, 1, 3, 10, 8, 3),
                        (-10, 1, 3, 10, 8, 3, -10, 8, 3),
                    ])
                instance = self.select(run, sigma, point, charge=case != "no_charge")
                for _ in range(130):
                    run.step({})
                self.assertIsNone(self.effect(target))
                self.assertEqual(target.mover.position[1], 1)
                if case != "no_charge":
                    self.assertEqual(instance.vars[375].value(), ())

    def test_sleep_dart_interrupts_flux_until_descent_without_refunding_charge(self):
        for case in ("selection", "lift", "descent"):
            with self.subTest(case=case):
                run, sigma, target, ana, _ = self.arena()
                fight = run.match.combat
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.team = target.team
                ana.mover.position[:] = [0, 1, -4]
                ana.mover.body = None
                point = tuple(target.mover.position)
                if case == "selection":
                    sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(ULT_COST).value())
                    run.step({sigma: (*aim(fight._eye(sigma), point), 32)})
                    for _ in range(65):
                        run.step({})
                    self.assertGreater(sigma.mover.position[1], 2)
                else:
                    self.select(run, sigma, point)
                    self.wait_for_flux_phase(run, target, lifting=case == "lift")
                angles = aim(fight._eye(ana), fight._eye(sigma))
                run.step({ana: (*angles, 8)})
                for _ in range(40):
                    run.step({ana: (*angles, 0)})
                    if sigma.body in fight.sleeps:
                        break
                self.assertIn(sigma.body, fight.sleeps)
                self.assertEqual(self.effect(target) is not None, case == "descent")
                script = sigma.body_script
                component = script.component
                cost = component.entity_var(ULT_COST).value()
                expected_passive = cost / script.ult_seconds * script.frame_ms / 1000
                self.assertAlmostEqual(component.entity_var(ULT_CHARGE).value(), expected_passive, places=4)
                instance = next(i for i in component.instances.values() if i.graph.index == sigma_flux.FLUX)
                self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
                for _ in range(300):
                    run.step({})
                hits = {"selection": 0, "lift": 1, "descent": 2}
                self.assertEqual(len(sigma.client.messages(combat.HIT)), hits[case])
                self.assertFalse(target.mover.airborne)
                self.assertIsNone(self.effect(target))

    def wait_for_flux_phase(self, run, target, *, lifting):
        for _ in range(260):
            run.step({})
            effect = self.effect(target)
            if effect is None:
                continue
            lift = effect.states.get(16)
            if lift is not None and lift.counter and lift.active == lifting:
                return effect
        self.fail("Flux never reached the expected lift/descent phase")

    def test_lethal_zarya_grenade_cancels_lift_but_keeps_committed_slam(self):
        for lifting in (True, False):
            with self.subTest(lifting=lifting):
                run, sigma, target, attacker, _ = self.arena()
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(attacker, heroes()[0x02E0000000000068]))
                attacker.team = target.team
                attacker.mover.position[:] = [0, 1, -4]
                attacker.mover.body = None
                self.select(run, sigma, tuple(target.mover.position))
                self.wait_for_flux_phase(run, target, lifting=lifting)
                sigma_target = next(t for t in fight._targets() if t.owner is sigma)
                vitals = fight.vitals_of(sigma_target)
                vitals.damage(380)
                angles = aim(fight._eye(attacker), fight._eye(sigma))
                run.step({attacker: (*angles, 2)})
                for _ in range(40):
                    run.step({attacker: (*angles, 0)})
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, attacker.body)
                self.assertEqual(self.effect(target) is not None, not lifting)
                for _ in range(250):
                    run.step({})
                self.assertEqual(len(sigma.client.messages(combat.HIT)), 1 if lifting else 2)
                self.assertFalse(target.mover.airborne)
                self.assertIsNone(self.effect(target))

    def test_phase_abilities_exclude_or_release_flux_targets(self):
        for hero, *_ in phase.PROFILES:
            for early in (False, True):
                with self.subTest(hero=hero, early=early):
                    run, sigma, target, _, _ = self.arena()
                    self.assertTrue(run.match.switch_hero(target, heroes()[hero]))
                    target.mover.position[:] = [0, 1, 5]
                    point = tuple(target.mover.position)
                    self.select(run, sigma, point)
                    if early:
                        for _ in range(40):
                            run.step({})
                    else:
                        self.wait_for_flux_phase(run, target, lifting=True)
                        for _ in range(12):
                            run.step({})
                        self.assertGreater(target.mover.position[1], point[1] + 1)
                    run.step({target: (0, 0, 8)})
                    self.assertIsNotNone(phase.protection(target))
                    self.assertIsNone(self.effect(target))
                    for _ in range(280):
                        run.step({})
                        self.assertIsNone(self.effect(target))
                    hits = sigma.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [] if early else [50])
                    self.assertFalse(target.mover.airborne)

    def test_fortify_reduces_entry_or_releases_an_active_lift(self):
        for early in (False, True):
            with self.subTest(early=early):
                run, sigma, target, _, _ = self.arena()
                self.assertTrue(run.match.switch_hero(target, heroes()[defenses.ORISA]))
                target.mover.position[:] = [0, 1, 5]
                point = tuple(target.mover.position)
                self.select(run, sigma, point)
                if early:
                    for _ in range(40):
                        run.step({})
                else:
                    self.wait_for_flux_phase(run, target, lifting=True)
                    for _ in range(12):
                        run.step({})
                    self.assertTrue(target.mover.airborne)
                run.step({target: (0, 0, 8)})
                self.assertIsNotNone(defenses.fortify_state(target))
                self.assertIsNone(self.effect(target))
                peak = target.mover.position[1]
                for _ in range(250):
                    run.step({})
                    peak = max(peak, target.mover.position[1])
                    self.assertIsNone(self.effect(target))
                hits = sigma.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [25 if early else 45])
                if early:
                    self.assertEqual(peak, point[1])
                self.assertFalse(target.mover.airborne)

    def test_team_change_releases_live_flux_without_slam(self):
        run, sigma, zarya, _, _ = self.arena()
        fight = run.match.combat
        self.select(run, sigma, tuple(zarya.mover.position))
        for _ in range(180):
            run.step({})
            if zarya.mover.position[1] > 2 and self.effect(zarya) is not None:
                break
        self.assertGreater(zarya.mover.position[1], 2)
        self.assertIsNotNone(self.effect(zarya))
        self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 50)
        zarya.team = sigma.team
        run.step({})
        self.assertIsNone(self.effect(zarya))
        for _ in range(260):
            run.step({})
            if not zarya.mover.airborne:
                break
        self.assertFalse(zarya.mover.airborne)
        self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 50)
        self.assertFalse(fight.is_stunned(zarya))

    def test_body_replacement_retires_live_flux_without_damaging_new_body(self):
        for replace_source in (False, True):
            with self.subTest(replace_source=replace_source):
                run, sigma, target, _, _ = self.arena()
                fight = run.match.combat
                self.select(run, sigma, tuple(target.mover.position))
                effect = self.wait_for_flux_phase(run, target, lifting=True)
                for _ in range(12):
                    run.step({})
                self.assertTrue(target.mover.airborne)
                replaced = sigma if replace_source else target
                previous_body = replaced.body
                previous_script = replaced.body_script
                self.assertTrue(run.match.switch_hero(replaced, replaced.hero))
                self.assertNotEqual(replaced.body, previous_body)
                for _ in range(260):
                    run.step({})
                self.assertTrue(previous_script.retired)
                self.assertIsNone(previous_script.world.match)
                if replace_source:
                    self.assertTrue(effect.stopped)
                self.assertIsNone(self.effect(target))
                self.assertFalse(target.mover.airborne)
                target_body = next(t for t in fight._targets() if t.owner is target)
                self.assertGreaterEqual(fight.vitals_of(target_body).total, 350 if replace_source else 400)
                hits = sigma.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [50])

    def start_graviton_flux_overlap(self):
        run, sigma, target, zarya, _ = self.arena()
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.mover.position[:] = [-3, 1, 0]
        fight = run.match.combat
        point = tuple(target.mover.position)
        flux = self.select(run, sigma, point)
        zarya.body_script.set_ult_charge(2100)
        angles = aim(fight._eye(zarya), (2, 1, 5))
        run.step({zarya: (*angles, 32)})
        return run, sigma, target, zarya, flux, point

    def test_graviton_preserves_flux_lift_and_grounded_slam_on_same_player(self):
        run, sigma, target, zarya, flux, point = self.start_graviton_flux_overlap()
        fight = run.match.combat
        peak = point[1]
        lifted_while_pulled = False
        for _ in range(420):
            previous_x = target.mover.position[0]
            run.step({})
            x, y, _ = target.mover.position
            peak = max(peak, y)
            if y > point[1] + 1 and fight.zarya_gravitons.fields:
                lifted_while_pulled |= x > previous_x
                self.assertTrue(target.mover.airborne)
                self.assertEqual(sigma.stats.values.get(combat.stats.HERO_DAMAGE, 0), 50)
        self.assertEqual(flux.vars[375].value(), (expr.Entity(target.body),))
        self.assertTrue(lifted_while_pulled)
        self.assertGreater(peak, point[1] + 1)
        self.assertAlmostEqual(target.mover.position[1], point[1], delta=0.01)
        self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 250)
        field_damage = zarya.stats.values[combat.stats.HERO_DAMAGE]
        self.assertGreater(field_damage, 0)
        self.assertLess(field_damage, 20)
        self.assertIsNone(self.effect(target))
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertFalse(fight.zarya_gravitons.player_pulls)

    def test_flux_slam_breaks_personal_barrier_during_graviton_without_spill(self):
        run, sigma, target, zarya, _, point = self.start_graviton_flux_overlap()
        fight = run.match.combat
        gravitons = fight.zarya_gravitons
        for _ in range(300):
            run.step({})
            if point[1] + 1 < target.mover.position[1] < point[1] + 3 and target.mover.velocity[1] < -1:
                break
        self.assertTrue(target.mover.airborne)
        field, = gravitons.fields
        vitals = fight.vitals[target.body]
        run.step({target: (0, 0, 8)})
        barriers = fight.zarya_barriers
        self.assertTrue(barriers.protects(target))
        pool = barriers.active[target.body]
        self.assertEqual(pool.remaining, 200)
        before = vitals.total
        field_damage = zarya.stats.values[combat.stats.HERO_DAMAGE]

        for _ in range(35):
            remaining_before_slam = pool.remaining
            run.step({})
            self.assertEqual(vitals.total, before)
            if pool.remaining == 0:
                break
        self.assertEqual(pool.remaining, 0)
        self.assertLess(remaining_before_slam, 200)
        self.assertGreater(remaining_before_slam, 0)
        self.assertFalse(target.mover.airborne)
        self.assertIn(field, gravitons.fields)
        self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 50)
        self.assertEqual(zarya.stats.values[combat.stats.HERO_DAMAGE], field_damage)
        self.assertAlmostEqual(target.body_script.component.entity_var(87).value(), 40, places=4)
        energy = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value(), expr.Entity(sigma.body))

        run.step({})
        self.assertAlmostEqual(before - vitals.total, 0.08, places=4)
        self.assertAlmostEqual(zarya.stats.values[combat.stats.HERO_DAMAGE] - field_damage, 0.08, places=4)
        while run.frame < field.end_tick:
            run.step({})
        self.assertFalse(gravitons.fields)
        self.assertFalse(gravitons.player_pulls)
        self.assertIsNone(self.effect(target))

    def test_graviton_pulls_enemy_sigma_during_flux_and_preserves_ally_flight(self):
        for case in ("selection", "confirmed", "ally"):
            with self.subTest(case=case):
                run, sigma, zarya, _, _ = self.arena()
                fight = run.match.combat
                if case == "ally":
                    zarya.team = sigma.team
                point = tuple(zarya.mover.position)
                if case == "confirmed":
                    instance = self.select(run, sigma, point)
                else:
                    script = sigma.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    for frame in range(55):
                        angles = aim(fight._eye(sigma), point)
                        run.step({sigma: (*angles, 32 if frame == 0 else 0)})
                    instance = next(i for i in script.component.instances.values()
                                    if i.graph.index == sigma_flux.FLUX)
                self.assertGreater(sigma.mover.position[1], 2)
                self.assertEqual(instance.vars[59].value(), 2 if case == "confirmed" else 1)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(ULT_COST).value())
                angles = aim(fight._eye(zarya), (2, 1, 0))
                run.step({zarya: (*angles, 32)})
                for _ in range(80):
                    run.step({})
                    if fight.zarya_gravitons.fields:
                        break
                field, = fight.zarya_gravitons.fields
                self.assertIs(field.shooter, zarya)
                self.assertEqual(zarya.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                start = sigma.mover.position[0]
                for _ in range(25):
                    run.step({})
                self.assertGreater(field.position[0], 1)
                self.assertEqual(sigma.body in fight.zarya_gravitons.player_pulls, case != "ally")
                if case == "ally":
                    self.assertAlmostEqual(sigma.mover.position[0], start)
                    self.assertEqual(fight.vitals[sigma.body].total, 400)
                else:
                    self.assertTrue(sigma.mover.driven)
                    self.assertGreater(sigma.mover.position[0], start + 0.3)
                    self.assertAlmostEqual(fight.vitals[sigma.body].total, 398)
                if case == "confirmed":
                    for _ in range(100):
                        if self.effect(zarya) is not None:
                            break
                        run.step({})
                    self.assertIsNotNone(self.effect(zarya))
                    self.assertIn(field, fight.zarya_gravitons.fields)
                    self.assertIn(sigma.body, fight.zarya_gravitons.player_pulls)
                else:
                    self.assertEqual(instance.vars[59].value(), 1)
                while fight.tick < field.end_tick:
                    run.step({})
                self.assertNotIn(sigma.body, fight.zarya_gravitons.player_pulls)
                self.assertFalse(fight.zarya_gravitons.fields)
