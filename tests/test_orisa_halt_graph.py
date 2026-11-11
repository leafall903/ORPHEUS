"""Actual Halt creates original0CCE; native force/continuous search remain open."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_orisa_barrier as barrier_fixture
import test_orisa_halt as fixture
from test_game_combat import SOLDIER, in_world

from ow174.game import orisa_halt
from ow174.game.commands import FIRE, Command
from ow174.game.content import find_hero
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "original Halt graph supplement required")
class OrisaHaltGraphTests(unittest.TestCase):
    def cast(self, *, target_orisa=False):
        helper = fixture.HaltTests()
        run, caster, target = helper.arena()
        if target_orisa:
            position = tuple(target.mover.position)
            run.match.switch_hero(target, find_hero("Orisa"))
            helper.place(run, target, position)
        orb = helper.detonate(run, caster)
        run.step({})
        pull = run.match.combat.orisa.pulls[target.body]
        return run, caster, target, orb, pull

    @staticmethod
    def step(run, target, buttons=0, forward=0):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for player in run.players:
            player.take_commands([Command(run.frame, extra=0, yaw=0, pitch=0,
                                          buttons=buttons if player is target else 0,
                                          forward=forward if player is target else 0)])

    def linger(self, run, target, pull):
        for _ in range(100):
            self.step(run, target)
            wait = pull.script.states.get(pull.script.graph.node(5).state)
            if wait is not None and wait.active:
                return wait
        self.fail("actual original MoveTo did not reach its post-movement wait")

    def test_caster_leave_retires_requested_orb_before_acquisition(self):
        helper = fixture.HaltTests()
        run, caster, target = helper.arena()
        orb = helper.detonate(run, caster)
        halt = run.match.combat.orisa
        before = tuple(target.mover.position)
        run.match.leave(caster)
        run.players.remove(caster)
        self.assertNotIn(orb.entity, halt.orbs)
        self.assertNotIn(orb.rocket, run.match.combat.rockets)
        for _ in range(100):
            run.step({})
        self.assertFalse(halt.results)
        self.assertFalse(halt.pulls)
        self.assertEqual(tuple(target.mover.position), before)

    def test_caster_leave_keeps_acquired_slow_until_the_original_wait_ends(self):
        run, caster, target, _, pull = self.cast()
        wait = self.linger(run, target, pull)
        halt = run.match.combat.orisa
        modifier = target.body_script.component.entity_var(23186)
        run.match.leave(caster)
        run.players.remove(caster)
        self.step(run, target, forward=127)
        self.assertIs(halt.pulls[target.body], pull)
        self.assertTrue(wait.active)
        self.assertAlmostEqual(modifier.value(), 0.65)
        for _ in range(25):
            self.step(run, target, forward=127)
        self.assertAlmostEqual(target.mover.velocity[2], 1.925, delta=1 / 1024)
        for _ in range(50):
            self.step(run, target, forward=127)
        self.assertEqual(wait.ended_at - wait.start, 650)
        self.assertNotIn(target.body, halt.pulls)
        self.assertTrue(pull.script.stopped)
        self.assertFalse(modifier.value())
        self.assertAlmostEqual(target.mover.velocity[2], 5.5, delta=1 / 1024)

    def test_target_leave_immediately_retires_acquired_slow(self):
        run, _, target, _, pull = self.cast()
        wait = self.linger(run, target, pull)
        body = target.body
        modifier = target.body_script.component.entity_var(23186)
        self.assertAlmostEqual(modifier.value(), 0.65)
        run.match.leave(target)
        run.players.remove(target)
        self.assertNotIn(body, run.match.combat.orisa.pulls)
        self.assertTrue(pull.script.stopped)
        self.assertFalse(wait.active)
        run.step({})
        self.assertNotIn(body, run.match.combat.orisa.pulls)

    def test_second_caster_replaces_active_slow_without_leaving_old_modifier(self):
        run, first_caster, target, first_orb, first = self.cast()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        first_wait = self.linger(run, target, first)
        modifier = target.body_script.component.entity_var(23186)
        self.assertAlmostEqual(modifier.value(), 0.65)
        second_caster = run.match.add_player(3, "Second Orisa", SOLDIER, 2, False)
        in_world(run.match, second_caster)
        run.players.append(second_caster)
        run.match.switch_hero(second_caster, find_hero("Orisa"))
        second_caster.team = first_caster.team
        x, y, z = first_caster.mover.position
        helper = fixture.HaltTests()
        helper.place(run, second_caster, (x + 1.5, y, z))
        second_orb = helper.detonate(run, second_caster)
        self.assertTrue(first_wait.active)
        run.step({})

        halt = run.match.combat.orisa
        second = halt.pulls[target.body]
        self.assertIsNot(second, first)
        self.assertTrue(first.script.stopped)
        self.assertFalse(first_wait.active)
        self.assertFalse(modifier.value())
        self.assertEqual(second.script.vars[40].value(), expr.Entity(second_orb.entity))
        self.assertEqual(second.script.vars[41].value(), expr.Entity(second_caster.entity))
        self.assertEqual(tuple(halt.results)[-2:], (
            (first_orb.entity, (target.body,)), (second_orb.entity, (target.body,)),
        ))
        second_wait = self.linger(run, target, second)
        self.assertAlmostEqual(modifier.value(), 0.65)
        for _ in range(50):
            self.step(run, target, forward=127)
        self.assertEqual(second_wait.ended_at - second_wait.start, 650)
        self.assertNotIn(target.body, halt.pulls)
        self.assertTrue(second.script.stopped)
        self.assertFalse(modifier.value())
        self.assertAlmostEqual(target.mover.velocity[2], 5.5, delta=1 / 1024)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    def test_hostile_barrier_does_not_block_halt_flight_or_target_acquisition(self):
        for flight_crosses in (True, False):
            with self.subTest(flight_crosses=flight_crosses):
                run, target, caster, _, shield = barrier_fixture.BarrierTests().deploy()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                run.match.switch_hero(caster, find_hero("Orisa"))
                x, y, z = shield.position
                helper = fixture.HaltTests()
                helper.place(run, caster, (x, y, z + (5 if flight_crosses else 11)))
                helper.place(run, target, (x, y, z - (6 if flight_crosses else 2)))
                fight = run.match.combat
                halt = fight.orisa
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                before = health.total
                run.step({caster: (-32768, 0, 0)})
                launch = None
                for frame in range(26):
                    run.step({caster: (-32768, 0, 2 if frame in (0, 25) else 0)})
                    if launch is None and halt.orbs:
                        launch = next(iter(halt.orbs.values())).rocket.position
                orb, = halt.orbs.values()
                self.assertEqual(orb.requested, run.match.tick)
                self.assertIsNotNone(launch)
                centre = orb.rocket.position
                if flight_crosses:
                    start, end = launch, centre
                else:
                    start, end = centre, fight._eye(target)
                distance = math.dist(start, end)
                direction = tuple((b - a) / distance for a, b in zip(start, end, strict=True))
                self.assertIs(halt.barriers.trace(caster, start, direction, distance)[1], shield)
                run.step({})
                self.assertEqual(halt.results[-1], (orb.entity, (target.body,)))
                pull = halt.pulls[target.body]
                self.linger(run, target, pull)
                self.assertEqual(health.total, before)
                self.assertEqual(shield.health, 600)

    def test_fortify_cleanses_one_target_without_affecting_other_target(self):
        helper = fixture.HaltTests()
        run, caster, fortified = helper.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.switch_hero(fortified, find_hero("Orisa"))
        other = run.match.add_player(3, "Other enemy", SOLDIER, 2, False)
        in_world(run.match, other)
        run.players.append(other)
        self.assertTrue(run.match.switch_hero(other, find_hero("Soldier")))
        other.team = fortified.team
        x, y, z = caster.mover.position
        helper.place(run, fortified, (x - 1.5, y, z + 5))
        helper.place(run, other, (x + 1.5, y, z + 5))
        run.step({})
        original_fortified = tuple(fortified.mover.position)
        original_other = tuple(other.mover.position)
        orb = helper.detonate(run, caster)
        run.step({})
        halt = run.match.combat.orisa
        self.assertEqual(halt.results[-1], (orb.entity, (fortified.body, other.body)))
        first, second = halt.pulls[fortified.body], halt.pulls[other.body]
        self.assertIsNot(first.script, second.script)
        self.assertEqual(first.script.vars[40].value(), expr.Entity(orb.entity))
        self.assertEqual(second.script.vars[40].value(), expr.Entity(orb.entity))

        self.step(run, fortified, 8)
        self.step(run, fortified)
        self.assertTrue(first.script.stopped)
        self.assertNotIn(fortified.body, halt.pulls)
        self.assertIs(halt.pulls[other.body], second)
        wait = self.linger(run, other, second)
        self.assertNotEqual(tuple(other.mover.position), original_other)
        self.assertEqual(tuple(fortified.mover.position), original_fortified)
        self.assertAlmostEqual(other.body_script.component.entity_var(23186).value(), 0.65)
        for _ in range(50):
            self.step(run, other)
        self.assertEqual(wait.ended_at - wait.start, 650)
        self.assertFalse(halt.pulls)

    def test_actual_input_binds_typed_start_and_original_twenty_speed_offset_request(self):
        run, caster, target, orb, pull = self.cast()
        instance = pull.script
        self.assertEqual(instance.graph.index, 0xCCE)
        self.assertIs(instance.component, target.body_script.component)
        self.assertEqual(instance.vars[40].value(), expr.Entity(orb.entity))
        self.assertEqual(instance.vars[41].value(), expr.Entity(caster.entity))
        self.assertEqual(instance.vars[431].value(), expr.Vec3(*orb.rocket.position))
        self.assertEqual(instance.vars[422].value(), 5)
        original = tuple(target.mover.position)
        for _ in range(23):
            self.step(run, target)
        self.assertEqual(tuple(target.mover.position), original)
        for _ in range(4):
            self.step(run, target)
            state = instance.states.get(instance.graph.node(3).state)
            if state is not None and state.active:
                break
        destination, speed = state.move_request()
        self.assertEqual(speed, 20)
        self.assertAlmostEqual(math.dist(tuple(destination), orb.rocket.position), 1, places=4)
        self.assertGreater(destination.y, original[1])

    def test_actual_arrival_owns_slow_then_recovers_and_retires_status(self):
        run, _, target, _, pull = self.cast()
        wait = self.linger(run, target, pull)
        self.assertEqual(wait.ms, 650)
        self.assertAlmostEqual(target.body_script.component.entity_var(23186).value(), 0.65)
        for _ in range(30):
            self.step(run, target, forward=127)
        self.assertTrue(wait.active)
        self.assertAlmostEqual(target.mover.velocity[2], 1.925, delta=1 / 1024)
        for _ in range(40):
            self.step(run, target, forward=127)
        self.assertFalse(wait.active)
        self.assertEqual(wait.ended_at - wait.start, 650)
        self.assertAlmostEqual(target.mover.velocity[2], 5.5, delta=1 / 1024)
        self.assertNotIn(target.body, run.match.combat.orisa.pulls)
        self.assertTrue(pull.script.stopped)

    def test_post_pull_slow_dominates_firing_penalty_then_restores_firing_speed(self):
        run, _, target, _, pull = self.cast(target_orisa=True)
        self.linger(run, target, pull)
        for _ in range(30):
            self.step(run, target, FIRE, 127)
        self.assertAlmostEqual(target.mover.velocity[2], 1.925, delta=1 / 1024)
        for _ in range(40):
            self.step(run, target, FIRE, 127)
        self.assertAlmostEqual(target.mover.velocity[2], 3.85, delta=1 / 1024)

    def test_actual_fortify_cleanses_original_post_pull_slow(self):
        run, _, target, _, pull = self.cast(target_orisa=True)
        self.linger(run, target, pull)
        self.step(run, target, 8, 127)
        for _ in range(20):
            self.step(run, target, forward=127)
        self.assertTrue(pull.script.stopped)
        self.assertFalse(target.body_script.component.entity_var(23186).value())
        self.assertAlmostEqual(target.mover.velocity[2], 5.5, delta=1 / 1024)

    def test_missing_authored_graph_does_not_restore_legacy_fifty_speed_control(self):
        with patch.object(orisa_halt, "control_graph", return_value=None):
            helper = fixture.HaltTests()
            run, caster, target = helper.arena()
        original = tuple(target.mover.position)
        helper.detonate(run, caster)
        for _ in range(80):
            run.step({})
        self.assertEqual(tuple(target.mover.position), original)
        self.assertFalse(run.match.combat.orisa.pulls)
