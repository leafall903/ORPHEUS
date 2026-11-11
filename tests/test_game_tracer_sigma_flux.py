"""Actual Gravitic Flux input, selection and target effects against Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer_sigma_accretion as accretion_tests
from test_game_combat import aim

from ow174.game import combat, sigma_flux, tracer
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.commands import CROUCH, FIRE, JUMP
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.nodes import Ability


class TracerSigmaFluxTests(unittest.TestCase):
    def setUp(self):
        accretion_tests.TracerSigmaAccretionTests.setUp(self)
        self.script = self.sigma.body_script
        self.instance = next(i for i in self.script.component.instances.values()
                             if i.graph.index == sigma_flux.FLUX)
        self.ability = next(s for s in self.instance.states.values() if isinstance(s, Ability))
        self.point = tuple(self.tracer.mover.position)
        self.cost = self.script.component.entity_var(338).value()

    def step(self, *, sigma_buttons=0, tracer_buttons=0):
        yaw, pitch = aim(self.fight._eye(self.sigma), self.point)
        self.run.step({self.sigma: (yaw, pitch, sigma_buttons), self.tracer: (0, 0, tracer_buttons)})

    def select(self):
        self.script.set_ult_charge(self.cost)
        self.step(sigma_buttons=32)
        self.assertTrue(self.ability.flags & Ability.RUNNING)
        self.assertEqual(self.script.component.entity_var(342).value(), 0)
        for _ in range(80):
            self.step()
        self.assertEqual(self.instance.vars[59].value(), 1)

    def effect(self):
        return next((i for i in self.tracer.body_script.component.instances.values()
                     if i.graph.index == sigma_flux.FLUX_EFFECT and not i.stopped), None)

    def wait_for_entry(self):
        self.step(sigma_buttons=FIRE)
        for _ in range(105):
            if self.effect() is not None and self.vitals.total == 100:
                return self.effect()
            self.step()
        self.fail("Flux did not apply the selected target's entry hit")

    def test_actual_charge_selection_lift_slam_and_effect_end(self):
        self.assertEqual(self.cost, 1960)
        self.step(sigma_buttons=32)
        self.assertFalse(self.ability.flags & Ability.RUNNING)
        self.step()
        self.select()
        point = self.instance.vars[2478].value()
        self.assertIsInstance(point, expr.Vec3)
        for coordinate, expected in zip(point, self.point, strict=True):
            self.assertAlmostEqual(coordinate, expected, delta=0.03)
        export_body_vm(self.script)
        effect = self.wait_for_entry()
        self.assertEqual(self.instance.vars[375].value(), (expr.Entity(self.tracer.body),))
        self.assertEqual(effect.source_entity(), self.sigma.body)
        export_body_vm(self.tracer.body_script)
        peak = self.point[1]
        for _ in range(430):
            self.step()
            peak = max(peak, self.tracer.mover.position[1])
            if self.vitals.total < 100:
                self.assertFalse(self.tracer.mover.airborne)
            if self.effect() is None and not self.ability.flags & Ability.RUNNING:
                break
        self.assertGreater(peak - self.point[1], 3)
        self.assertEqual(self.vitals.total, 25)
        self.assertIsNone(self.effect())
        self.assertFalse(self.ability.flags & Ability.RUNNING)
        for _ in range(20):
            self.step()
        self.assertEqual(self.vitals.total, 25)
        self.step(sigma_buttons=32)
        self.assertFalse(self.ability.flags & Ability.RUNNING)
        self.step()
        self.select()

    def test_caster_can_ascend_descend_and_lands_after_unconfirmed_expiry(self):
        floor = self.sigma.mover.position[1]
        self.select()
        hover = self.sigma.mover.position[1]
        self.assertGreater(hover - floor, 2)
        self.assertEqual(self.sigma.mover.gravity_scale, 0)
        for _ in range(10):
            self.step(sigma_buttons=JUMP)
        self.assertGreater(self.sigma.mover.position[1] - hover, 1)
        for _ in range(10):
            self.step(sigma_buttons=CROUCH)
        self.assertAlmostEqual(self.sigma.mover.position[1], hover, delta=0.02)
        for _ in range(560):
            self.step()
            if not self.ability.flags & Ability.RUNNING and not self.sigma.mover.airborne:
                break
        self.assertFalse(self.ability.flags & Ability.RUNNING)
        self.assertFalse(self.sigma.mover.airborne)
        self.assertEqual(self.vitals.total, 150)

    def test_target_in_active_recall_is_not_selected(self):
        self.select()
        self.step(sigma_buttons=FIRE)
        for _ in range(45):
            self.step()
        self.step(tracer_buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        for _ in range(40):
            self.step()
        self.assertEqual(self.instance.vars[375].value(), ())
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 150)

    def test_caster_body_replacement_after_lift_cancels_slam(self):
        self.select()
        self.wait_for_entry()
        for _ in range(12):
            self.step()
        self.assertTrue(self.tracer.mover.airborne)
        self.assertTrue(self.run.match.switch_hero(self.sigma, heroes()[0x02E000000000006E]))
        for _ in range(430):
            self.step()
            if self.effect() is None and not self.tracer.mover.airborne:
                break
        self.assertIsNone(self.effect())
        self.assertFalse(self.tracer.mover.airborne)
        self.assertEqual(self.vitals.total, 100)

    def test_ally_at_the_selected_point_is_excluded(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team = self.sigma.team
        self.select()
        self.step(sigma_buttons=FIRE)
        for _ in range(130):
            self.step()
        self.assertEqual(self.instance.vars[375].value(), ())
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 150)

    def test_recall_press_during_lift_is_rejected_then_fresh_press_works(self):
        self.select()
        self.wait_for_entry()
        for _ in range(12):
            self.step()
        self.assertTrue(self.tracer.mover.airborne)
        component = self.tracer.body_script.component
        recall = next(i for i in component.instances.values() if i.graph.index == 0x1A)
        self.assertEqual(recall.vars[61].value(), 0)
        self.step(tracer_buttons=16)
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(recall.vars[61].value(), 0)
        for _ in range(420):
            self.step()
            if self.effect() is None:
                break
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 25)
        self.assertFalse(self.tracer.mover.airborne)
        self.assertFalse(tracer.recalling(self.tracer))
        self.step(tracer_buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))

    def test_actual_flux_lift_rejects_blink_aliases_until_slam_ends(self):
        self.select()
        self.wait_for_entry()
        for _ in range(12):
            self.step()
        self.assertTrue(self.tracer.mover.airborne)
        component = self.tracer.body_script.component
        blink = next(i for i in component.instances.values() if i.graph.index == 0x24)
        x, _, z = self.tracer.mover.position
        for button in (8, 2):
            self.step(tracer_buttons=button)
            self.assertIsNotNone(self.effect())
            self.assertTrue(self.tracer.mover.airborne)
            self.assertAlmostEqual(self.tracer.mover.position[0], x, places=2)
            self.assertAlmostEqual(self.tracer.mover.position[2], z, places=2)
            self.assertEqual(blink.vars[11].value(), 3)
            self.step()
        for _ in range(420):
            if self.effect() is None:
                break
            self.step(tracer_buttons=8)
            self.assertEqual(blink.vars[11].value(), 3)
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 25)
        self.assertFalse(self.tracer.mover.airborne)
        self.step(tracer_buttons=8)
        self.assertEqual(blink.vars[11].value(), 3)
        self.step()
        z = self.tracer.mover.position[2]
        self.step(tracer_buttons=8)
        self.assertAlmostEqual(self.tracer.mover.position[2] - z, 7.5, places=2)
        self.assertEqual(blink.vars[11].value(), 2)

    def stick_airborne_sigma_during_flux(self):
        self.select()
        effect = self.wait_for_entry()
        for _ in range(30):
            self.step()
        self.assertTrue(self.tracer.mover.airborne)
        self.assertTrue(self.sigma.mover.airborne)
        script = self.tracer.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        target = next(t for t in self.fight._targets() if t.owner is self.sigma)
        sigma_health = self.fight.vitals_of(target)
        before = sigma_health.total
        pulse = self.fight.pulse_bombs

        self.run.step({self.tracer: (0, 2000, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(pulse.bombs)
        for _ in range(16):
            if pulse.bombs:
                break
            self.run.step({self.tracer: (0, 2000, 0)})
        bomb, = pulse.bombs
        self.assertIs(bomb.shooter, self.tracer)
        self.assertIs(self.effect(), effect)
        for _ in range(65):
            if bomb.stuck:
                break
            self.step()
        self.assertEqual(bomb.attached, self.sigma.body)
        self.assertEqual(sigma_health.total, before - 5)
        self.assertIs(self.effect(), effect)
        return bomb, sigma_health, before

    def test_actual_pulse_released_during_flux_hits_the_airborne_caster(self):
        bomb, sigma_health, before = self.stick_airborne_sigma_during_flux()
        pulse = self.fight.pulse_bombs
        script = self.tracer.body_script
        for _ in range(420):
            if bomb not in pulse.bombs and self.effect() is None:
                break
            self.step()
        self.assertNotIn(bomb, pulse.bombs)
        self.assertEqual(sigma_health.total, before - 355)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 25)

    def test_lethal_pulse_during_flux_releases_tracer_without_slam_damage(self):
        bomb, sigma_health, _ = self.stick_airborne_sigma_during_flux()
        pulse = self.fight.pulse_bombs
        for _ in range(55):
            if sigma_health.total <= 350:
                break
            x, y, z = self.sigma.mover.position
            facing = aim(self.fight._eye(self.tracer), (x, y + 1.1, z))
            self.run.step({self.tracer: (*facing, FIRE)})
        self.assertGreater(sigma_health.total, 0)
        self.assertLessEqual(sigma_health.total, 350)
        self.assertIn(bomb, pulse.bombs)
        self.assertIsNotNone(self.effect())
        self.assertEqual(self.vitals.total, 100)
        for _ in range(75):
            if bomb not in pulse.bombs:
                break
            self.step()
        self.assertTrue(sigma_health.dead)
        self.assertNotIn(bomb, pulse.bombs)
        self.assertEqual(self.vitals.total, 100)
        for _ in range(420):
            if self.effect() is None and not self.tracer.mover.airborne:
                break
            self.step()
        self.assertIsNone(self.effect())
        self.assertFalse(self.tracer.mover.airborne)
        self.assertEqual(self.vitals.total, 100)
        for _ in range(20):
            self.step()
        self.assertEqual(self.vitals.total, 100)

    def test_selection_outside_target_volume_does_not_lift_tracer(self):
        x, y, z = self.point
        self.point = (x + 10, y, z)
        self.select()
        self.step(sigma_buttons=FIRE)
        for _ in range(130):
            self.step()
        self.assertEqual(self.instance.vars[375].value(), ())
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 150)

    def test_static_wall_occludes_target_inside_the_selected_radius(self):
        x, y, z = self.point
        accretion_tests.TracerSigmaAccretionTests.wall(self, x, y, z + 1.5, z + 2)
        self.point = (x, y, z + 4)
        self.select()
        self.step(sigma_buttons=FIRE)
        for _ in range(130):
            self.step()
        self.assertEqual(self.instance.vars[375].value(), ())
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 150)

    def test_actual_blink_exits_volume_before_target_sampling(self):
        x, y, z = self.point
        self.point = (x, y, z + 3)
        self.select()
        self.step(sigma_buttons=FIRE)
        for _ in range(20):
            self.step()
        self.run.step({self.tracer: (-32768, 0, 8)})
        self.assertLess(self.tracer.mover.position[2], z - 6)
        for _ in range(125):
            self.step()
        self.assertEqual(self.instance.vars[375].value(), ())
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 150)

    def test_tracer_pistol_kill_interrupts_caster_before_confirmation(self):
        self.select()
        caster = next(t for t in self.fight._targets() if t.owner is self.sigma)
        vitals = self.fight.vitals_of(caster)
        for pool in vitals.now:
            vitals.now[pool] = 6 if pool == combat.HEALTH_POOL else 0
        yaw, pitch = aim(self.fight._eye(self.tracer), self.fight._eye(self.sigma))
        self.run.step({self.tracer: (yaw, pitch, FIRE)})
        self.assertTrue(self.fight.is_dead(self.sigma))
        self.assertFalse(self.ability.flags & Ability.RUNNING)
        for _ in range(140):
            self.step(sigma_buttons=FIRE)
        self.assertIsNone(self.effect())
        self.assertEqual(self.vitals.total, 150)
