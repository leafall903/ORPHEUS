"""Actual incoming shots while defender input is stalled; native VM stays untouched."""

import unittest

from test_game_combat import aim
from test_widowmaker_barriers import _PersonalBarrierMatch, _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, SCOPE
from test_widowmaker_deflect import _DeflectMatch
from test_widowmaker_grasp import _GraspMatch
from test_widowmaker_matrix import _MatrixMatch

from ow174.game import combat, sigma
from ow174.game.commands import Command
from ow174.game.script import expr


class WidowmakerGraspClockTests(_GraspMatch, unittest.TestCase):
    def advance_world(self, ticks):
        self.run.frame += ticks
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)

    def fire_without_defender_input(self, buttons=FIRE):
        fight = self.run.match.combat
        direction = aim(fight._eye(self.a),
                        (0, self.b.mover.position[1] + 1, self.b.mover.position[2]))
        self.a.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1],
                                     buttons=buttons, extra=0)])

    def throw_without_defender_input(self):
        self.fire_without_defender_input(buttons=16)
        for _ in range(40):
            self.advance_world(1)
            self.fire_without_defender_input(buttons=0)

    def test_stalled_expired_grasp_cannot_consume_a_later_mine(self):
        self.activate()
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.throw_without_defender_input()
        mines = self.run.match.combat.venom
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(len(mines.mines), 1)
        self.assertIs(mines.mines[0].owner, self.a)
        self.assertEqual(self.instance().vars[21312].value(), 0)

    def test_future_grasp_activation_cannot_protect_earlier_world_hit(self):
        self.b.take_commands([Command(self.run.frame + 5, yaw=-32768, pitch=0, buttons=8, extra=0)])
        self.assertTrue(self.instance().state(1).active)
        self.advance_world(1)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 387)
        self.assertEqual(self.instance().vars[21312].value(), 0)

    def test_stalled_grasp_stops_absorbing_on_world_clock(self):
        self.activate()
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.fire_without_defender_input()
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(self.instance().vars[21312].value(), 0)
        self.assertEqual(self.health(self.b), 387)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 13)

    def test_stalled_grasp_still_absorbs_before_deadline(self):
        self.activate()
        self.advance_world(60)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.instance().vars[21312].value(), 13)

    def test_server_wall_epoch_preserves_grasp_until_simulation_deadline(self):
        self.activate()
        owner_frame = self.b.body_script.frame
        self.run.frame += 60
        self.run.match.update(1_700_000_000 + self.run.frame * combat.TICK, self.run.frame)
        self.fire_without_defender_input()
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.instance().vars[21312].value(), 13)

        self.run.frame += 140
        self.run.match.update(1_700_000_000 + self.run.frame * combat.TICK, self.run.frame)
        before = self.health(self.b)
        self.assertAlmostEqual(before, 407.8, places=5)
        self.fire_without_defender_input()
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertAlmostEqual(before - self.health(self.b), 13)
        self.assertEqual(self.instance().vars[21312].value(), 13)

    def test_world_grant_is_once_and_resumed_cooldown_keeps_native_time(self):
        self.step(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        self.step(widow=FIRE | SCOPE)
        deadline = sigma.grasp_deadline(self.instance())
        self.advance_world(200)
        fight = self.run.match.combat
        vitals = fight.vitals[self.b.body]
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 72)
        self.assertAlmostEqual(fight.grasp_shields[self.b.body].starts, deadline + 3)
        self.step(grasp=8)
        self.assertFalse(self.instance().state(1).active)
        self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 72)
        self.assertGreater(self.instance().vars[61].value(), 10)
        self.assertLess(self.instance().vars[61].value(), 12)

    def test_skipped_world_time_ages_shields_from_actual_expiry(self):
        self.step(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        self.step(widow=FIRE | SCOPE)
        deadline = sigma.grasp_deadline(self.instance())
        self.advance_world(400)
        fight = self.run.match.combat
        remaining = fight.vitals[self.b.body].temporary_shields[sigma.SHIELD_TAG].now
        expected = 72 - max(0, fight.now - deadline - 3) * 5
        self.assertAlmostEqual(remaining, expected)

    def test_world_lifetime_uses_actual_chase_duration(self):
        instance = self.instance()
        instance.write((0, 567), 4.0)
        self.activate()
        self.advance_world(180)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(instance.vars[21312].value(), 13)
        self.advance_world(1)
        self.fire_without_defender_input(buttons=0)
        self.advance_world(100)
        self.fire_without_defender_input()
        self.assertEqual(instance.vars[21312].value(), 13)
        self.assertAlmostEqual(self.health(self.b), 394.8, places=4)


class WidowmakerMatrixClockTests(_MatrixMatch, unittest.TestCase):
    advance_world = WidowmakerGraspClockTests.advance_world
    fire_without_defender_input = WidowmakerGraspClockTests.fire_without_defender_input
    throw_without_defender_input = WidowmakerGraspClockTests.throw_without_defender_input

    def test_stalled_exhausted_matrix_cannot_consume_a_later_mine(self):
        self.b.mover.position[:] = (0, 0, 7)
        self.b.mover.body = None
        self.step(2, matrix=2)
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.throw_without_defender_input()
        mines = self.run.match.combat.venom
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(len(mines.mines), 1)
        self.assertIs(mines.mines[0].owner, self.a)

    def test_future_matrix_activation_cannot_protect_earlier_world_hit(self):
        self.b.take_commands([Command(self.run.frame + 5, yaw=-32768, pitch=0, buttons=2, extra=0)])
        self.assertTrue(self.matrix().state(3).active)
        self.advance_world(1)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 592)

    def test_stalled_matrix_exhausts_before_late_incoming_shot(self):
        self.step(2, matrix=2)
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.fire_without_defender_input()
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(self.health(self.b), 592)
        self.assertEqual(self.charge(), 8)

    def test_stalled_matrix_protects_while_actual_budget_remains(self):
        self.step(2, matrix=2)
        self.advance_world(60)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 600)
        self.assertEqual(self.charge(), 0)

    def test_partly_filled_matrix_budget_exhausts_earlier(self):
        self.matrix().write((0, 87), 25.0)
        self.step(2, matrix=2)
        self.assertTrue(self.matrix().state(3).active)
        self.advance_world(60)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 592)

    def test_modified_drain_rate_changes_world_budget(self):
        self.matrix().write((0, 12097), 25.0)
        self.step(2, matrix=2)
        self.advance_world(180)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 600)
        self.advance_world(1)
        self.fire_without_defender_input(buttons=0)
        self.advance_world(100)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 592)


class WidowmakerDeflectClockTests(_DeflectMatch, unittest.TestCase):
    advance_world = WidowmakerGraspClockTests.advance_world
    fire_without_defender_input = WidowmakerGraspClockTests.fire_without_defender_input
    throw_without_defender_input = WidowmakerGraspClockTests.throw_without_defender_input

    def test_stalled_expired_deflect_cannot_take_a_later_mine(self):
        self.start()
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.throw_without_defender_input()
        mines = self.run.match.combat.venom
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(len(mines.mines), 1)
        self.assertIs(mines.mines[0].owner, self.a)

    def test_future_deflect_activation_cannot_return_earlier_world_hit(self):
        self.b.take_commands([Command(self.run.frame + 5, yaw=-32768, pitch=0, buttons=16, extra=0)])
        self.assertTrue(self.deflect().state(10).active)
        self.advance_world(1)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 187)
        self.assertEqual(self.health(self.a), self.initial_widow_health)

    def test_stalled_deflect_cannot_return_late_bullet_or_melee(self):
        self.place(1.5)
        self.start()
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.fire_without_defender_input()
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(self.health(self.b), 187)
        self.assertEqual(self.health(self.a), self.initial_widow_health)
        self.step(widow=0)
        self.step(widow=0x800)
        self.step(30)
        self.assertEqual(self.health(self.b), 157)

    def test_stalled_deflect_still_returns_before_authored_wait_ends(self):
        self.start()
        self.advance_world(60)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.health(self.a), self.initial_widow_health - 13)


class WidowmakerPersonalBarrierClockTests(_PersonalBarrierMatch, unittest.TestCase):
    advance_world = WidowmakerGraspClockTests.advance_world
    fire_without_defender_input = WidowmakerGraspClockTests.fire_without_defender_input

    def test_paused_breaking_hit_does_not_backdate_cooldown(self):
        samples = []
        for paused in (False, True):
            self.setUp()
            self.idle(100, widow=SCOPE)
            self.activate(widow=SCOPE)
            if paused:
                for _ in range(59):
                    self.advance_world(1)
                    self.a.take_commands([Command(self.run.frame, buttons=SCOPE, extra=0)])
            else:
                self.idle(59, widow=SCOPE)
            self.advance_world(1)
            direction = aim(self.run.match.combat._eye(self.a),
                            (0, self.b.mover.position[1] + combat.EYE, 1.5))
            self.a.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1],
                                         buttons=FIRE | SCOPE, extra=0)])
            barrier = self.run.match.combat.zarya_barriers.active[self.b.body]
            self.assertEqual(barrier.remaining, 0)
            self.assertEqual(self.health(self.b), 400)
            ability = next(s for s in self.barrier().states.values()
                           if s.node.cls == 'STUStatescriptStateAbility')
            self.step()
            samples.append(ability.remaining())
            self.assertGreater(samples[-1], 9.9)
            self.step(zarya=8)
            self.assertFalse(self.barrier().state(2).active)
        self.assertAlmostEqual(samples[0], samples[1], places=5)

    def test_paused_energy_message_uses_hit_time_for_decay(self):
        samples = []
        for paused in (False, True):
            self.setUp()
            self.activate()
            if paused:
                self.advance_world(60)
            else:
                self.idle(59)
                self.advance_world(1)
            self.fire_without_defender_input()
            row = []
            for tick in range(182):
                if tick in (1, 61, 121, 181):
                    row.append(self.energy())
                self.step()
            samples.append(row)
        self.assertGreater(samples[0][0], 2)
        self.assertEqual(samples[0], samples[1])
        self.assertEqual(samples[1][-1], 0)

    def test_stalled_personal_barrier_expires_without_absorption_energy(self):
        self.activate()
        owner_frame = self.b.body_script.frame
        self.advance_world(200)
        self.fire_without_defender_input()
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(self.health(self.b), 387)
        self.assertEqual(self.charge(), 13)
        self.step()
        self.assertEqual(expr.to_float(self.energy()), 0)

    def test_stalled_personal_barrier_protects_before_countdown_ends(self):
        self.activate()
        self.advance_world(60)
        self.fire_without_defender_input()
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.run.match.combat.zarya_barriers.active[self.b.body].remaining, 187)
        self.step()  # queued native energy message becomes visible when caster input resumes
        self.assertGreater(self.energy(), 0)


class WidowmakerProjectedBarrierClockTests(_ProjectedBarrierMatch, unittest.TestCase):
    advance_world = WidowmakerGraspClockTests.advance_world

    def fire_without_caster_input(self):
        direction = aim(self.run.match.combat._eye(self.b), (0, 1, 0))
        self.b.take_commands([Command(self.run.frame, yaw=direction[0], pitch=direction[1],
                                     buttons=FIRE, extra=0)])

    def test_stalled_projected_barrier_expires_for_protected_widow(self):
        self.activate()
        owner_frame = self.c.body_script.frame
        self.advance_world(200)
        self.fire_without_caster_input()
        self.assertEqual(self.c.body_script.frame, owner_frame)
        self.assertEqual(self.health(self.a), 155)
        self.step()
        self.assertEqual(expr.to_float(self.c.body_script.component.entity_var(87).value()), 0)

    def test_stalled_projected_barrier_protects_before_countdown_ends(self):
        self.activate()
        self.advance_world(60)
        self.fire_without_caster_input()
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(self.run.match.combat.zarya_barriers.projected[self.c.body].remaining, 180)
        self.step()
        self.assertGreater(self.c.body_script.component.entity_var(87).value(), 0)
