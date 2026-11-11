"""Real Q input -> mine entities/flight/arming/130 damage/50HP/lifetime.

Requires the explicitly prepared external mine graph bundle. World-up formation,
first-contact settling, capsule proximity and server-owned IDs are reconstructed.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_server_snapshot as snapshot_tests
import test_wrecking_ball_combat as ball_tests
import test_wrecking_ball_emp as emp_tests
import test_wrecking_ball_phase as phase_tests
import test_wrecking_ball_reload as reload_tests
from test_game_combat import KANEZAKA_DM, PRACTICE_RANGE, FakeClient, Run, aim
from test_wrecking_ball_control import ANA
from test_wrecking_ball_fireball import prepare_swing, relocate
from test_wrecking_ball_grapple import hold
from test_wrecking_ball_phase import REAPER, phase_instance

from ow174.game import (
    ball,
    ball_mines,
    barrier_geometry,
    barriers,
    combat,
    deflection,
    server_snapshot,
    sombra_emp,
    stats,
    world,
)
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import entity_queries, expr, graph, nodes, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class NativeFlightTests(unittest.TestCase):
    def setUp(self):
        # Retain the archived emulator fixtures with their explicit primitive.
        # Production uses host RSQRTSS; original-instruction/host-bound parity
        # is separately qualified instead of relabeling these old outputs.
        primitive = patch.object(ball_mines.scalar_math, 'reciprocal_sqrt',
                                 side_effect=lambda v: expr.f32(1 / expr.f32(math.sqrt(v))))
        primitive.start()
        self.addCleanup(primitive.stop)

    def test_captured_sse_gravity_and_position_trajectory(self):
        # Original104319 D71A70 and D70454, no world/type service adapters.
        # These are captured output values, not a second implementation of
        # the kernel. In particular100 steps differ from binary64 arithmetic.
        expected = {
            1: (9.520000457763672, 4.15231990814209),
            10: (5.2000017166137695, 5.177600383758545),
            50: (-13.999994277954102, 2.208001136779785),
            100: (-37.99997329711914, -18.783985137939453),
        }
        position, velocity = (0, 4, 0), (0, 10, 0)
        for number in range(1, 101):
            velocity, _, position = ball_mines.flight_step(position, velocity, 30, 0.016)
            if number in expected:
                self.assertEqual((velocity[1], position[1]), expected[number])
        velocity, _, position = ball_mines.flight_step((1.125, 4.5, -7.25), (11.25, -3.125, -13.75), 0, .016)
        self.assertEqual(velocity, (11.25, -3.125, -13.75))
        self.assertEqual(position, (1.3050000667572021, 4.449999809265137, -7.46999979019165))

    def test_captured_distance_and_partial_budget_outputs(self):
        # Original5E2610 and D72C80 outputs under the characterized static
        # RSQRT primitive; hardware approximation qualification remains open.
        self.assertEqual(ball_mines.flight_distance((0, 0, 0), (3, 4, 0)), 4.999999523162842)
        self.assertEqual(ball_mines.flight_distance((5, 6, 7), (5, 6, 7)), 0)
        time = ball_mines.clip_flight((0, 0, 0), (0, 10, 0), (3, 4, 0), (0, 9, 0), .25, 500, 0, 0, .5)
        self.assertEqual(time, ((1.5, 2, 0), (0, 9.5, 0), .25, 2.499999761581421, True))
        reach = ball_mines.clip_flight((0, 0, 0), (0, 10, 0), (3, 4, 0), (0, 9, 0), 1, 1, .5, 0, .5)
        self.assertEqual(reach, (
            (.6000000238418579, .8000000715255737, 0), (0, 9.800000190734863, 0),
            .10000000894069672, 1, True,
        ))

    def test_exact_budget_boundary_and_disabled_time_limit(self):
        for lifetime in (-1, 0, ball_mines.FLIGHT_EPSILON / 2):
            with self.subTest(lifetime=lifetime):
                result = ball_mines.clip_flight(
                    (0, 0, 0), (1, 0, 0), (1, 0, 0), (1, 0, 0), lifetime, 1, 999, 0, 1,
                )
                self.assertFalse(result[-1])  # distance equals reach, not beyond
        result = ball_mines.clip_flight((1, 0, 0), (1, 0, 0), (2, 0, 0), (1, 0, 0), 0, 1, 999, 1, 1)
        self.assertEqual(result, ((1, 0, 0), (1, 0, 0), 0, 0, True))
        self.assertFalse(ball_mines.clip_flight(
            (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), 1, 0, 1, 0, 0,
        )[-1])  # zero movement at the exact time/reach boundary
        self.assertTrue(ball_mines.clip_flight(
            (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), 1, 0, 1, 0, .016,
        )[-1])


@unittest.skipUnless(os.environ.get('ORPHEUS_BALL_MINE_BUNDLE'), 'external mine graphs not selected')
class MineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = patch.object(graph, 'GRAPHS_PATH', Path(os.environ['ORPHEUS_BALL_MINE_BUNDLE']))
        cls.bundle.start()
        cls.clear_graphs()
        if graph.entity_body(ball_mines.BODY) is None or graph.graph(0x151A) is None:
            raise ValueError('actual mine entity/151A graph required')

    @classmethod
    def tearDownClass(cls):
        cls.bundle.stop()
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def scenario(self):
        run, player, enemy, _, _ = ball_tests.BallCombatTests().scenario()
        x, y, z = enemy.mover.position
        relocate(run, enemy, (x + 100, y, z))
        player.body_script.ult_seconds = 0
        return run, player, enemy

    def deploy(self, run, player, ticks=200):
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32)})
        for _ in range(ticks):
            run.step({})
        return list(run.match.combat.mines.entities.values())

    def isolated(self, run, player):
        mines = self.deploy(run, player)
        mine = min(mines, key=lambda m: m.entity)
        for other in mines:
            if other is not mine:
                run.match.combat.mines.remove(other)
        x, y, z = mine.position
        relocate(run, player, (x + 20, y, z))
        return mine

    def flying(self):
        run, player, enemy = self.scenario()
        ball.instance(player, 0x1517).write((expr.INSTANCE, 7389), 0)
        mines = self.deploy(run, player, ticks=20)
        mine = min(mines, key=lambda m: m.entity)
        for other in mines:
            if other is not mine:
                run.match.combat.mines.remove(other)
        self.assertFalse(mine.landed)
        return run, player, enemy, mine

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def test_actual_q_expands_three_reports_into_fifteen_unique_owned_creates(self):
        run, player, _ = self.scenario()
        mines = self.deploy(run, player, ticks=20)
        self.assertEqual(len(mines), 15)
        self.assertEqual(len({m.entity for m in mines}), 15)
        self.assertTrue(all(m.owner is player and m.source == player.body and m.health == 50 for m in mines))
        self.assertTrue(all(m.flight_lifetime == 999 and m.reach == 500 for m in mines))
        self.assertTrue(all(m.born_tick <= m.last_tick
                            and m.born_tick == m.body_script.primary().initialized_frame for m in mines))
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual({key[2] for key in run.match.combat.mines.seen}, {3, 8, 18})
        for viewer in run.players:
            creates = [item for item in viewer.client.entities
                       if item.op == world.OP_CREATE and item.entity in {m.entity for m in mines}]
            self.assertEqual(len(creates), 15)
            for item in creates:
                self.assertTrue(world.frame(run.frame, 0, entities=[item]))
        node = ball.instance(player, 0x1517).states[18]
        ids = set(run.match.combat.mines.entities)
        self.assertTrue(run.match.combat.mines.volley(player, node, 0, run.frame))
        self.assertEqual(set(run.match.combat.mines.entities), ids)

    def test_minefield_interrupts_reload_before_fill_and_preserves_reload_exit(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    run, player, _ = self.scenario()
                    weapon = ball.instance(player, 0x1514)
                    ammo = weapon.vars[53]
                    trigger = {player: (-32768, 0, 1)}
                    for _ in range(240 if automatic else 30):
                        run.step(trigger)
                        if automatic and ammo.value() == 0:
                            break
                    run.step({})
                    if not automatic:
                        run.step({player: (0, 0, 1024)})
                    wait = weapon.state(20 if filled else 21)
                    for _ in range(100):
                        if wait.active:
                            break
                        run.step({})
                    self.assertTrue(wait.active)
                    counter = wait.counter
                    initial_ammo = 80 if filled else (0 if automatic else 67)
                    self.assertEqual(ammo.value(), initial_ammo)

                    script = player.body_script
                    charge = script.component.entity_var(ULT_CHARGE)
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({player: (-32768, 0, 32)})
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(wait.active, filled)
                    mines = run.match.combat.mines
                    for _ in range(20):
                        run.step(trigger)
                        self.assertFalse(weapon.state(9).active)
                        self.assertEqual(ammo.value(), initial_ammo)
                    self.assertEqual(len(mines.entities), 15)
                    self.assertEqual({key[2] for key in mines.seen}, {3, 8, 18})

                    if automatic and not filled:
                        for _ in range(100):
                            if wait.active:
                                break
                            run.step(trigger)
                        self.assertTrue(wait.active)
                        self.assertNotEqual(wait.counter, counter)
                        self.assertEqual(ammo.value(), 0)
                    elif filled:
                        self.assertTrue(wait.active)
                        self.assertEqual(wait.counter, counter)

                    for _ in range(200):
                        run.step(trigger)
                        if weapon.state(9).active:
                            break
                    self.assertTrue(weapon.state(9).active)
                    self.assertFalse(weapon.state(20).active)
                    self.assertFalse(weapon.state(21).active)
                    self.assertEqual(ammo.value(), 79 if automatic or filled else 66)
                    self.assertEqual(len(mines.entities), 15)
                    self.assertEqual(charge.value(), 0)

    def test_ana_dart_cancels_unreleased_minefield_reports_and_keeps_live_mines(self):
        # Shift Q relative to the same dart impact to exercise each report boundary.
        report_order = (18, 3, 8)
        for cast_frame, reports, count in ((19, 0, 0), (17, 1, 1), (12, 2, 7), (0, 3, 15)):
            with self.subTest(reports=reports):
                run, player, ana = self.scenario()
                run.match.switch_hero(ana, heroes()[ANA])
                x, y, z = player.mover.position
                relocate(run, ana, (x, y, z + 6))
                for _ in range(60):
                    run.step({})
                fight = run.match.combat
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                angles = aim(fight._eye(ana), (x, y + 1, z))
                for frame in range(45):
                    run.step({player: (0, 0, 32 if frame == cast_frame else 0),
                              ana: (*angles, 8 if frame == 0 else 0)})
                    if fight.is_stunned(player):
                        break
                self.assertTrue(fight.is_stunned(player))
                self.assertIn(player.body, fight.sleeps)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(len(fight.mines.seen), reports)
                self.assertEqual({key[2] for key in fight.mines.seen}, set(report_order[:reports]))
                self.assertEqual(len(fight.mines.entities), count)
                created = set(fight.mines.entities)
                relocate(run, ana, (x + 100, y, z))
                for _ in range(200):
                    run.step({player: (0, 0, 32)})
                self.assertTrue(fight.is_stunned(player))
                self.assertEqual(len(fight.mines.seen), reports)
                self.assertEqual(set(fight.mines.entities), created)
                self.assertTrue(all(m.body_script.primary().states[28].active
                                    for m in fight.mines.entities.values()))
                self.assertTrue(all(m.health == 50 for m in fight.mines.entities.values()))
                if reports == 1:
                    mine, = fight.mines.entities.values()
                    self.assertEqual(mine.health, 50)
                    mx, my, mz = mine.position
                    relocate(run, ana, (mx, my, mz + 0.5))
                    run.step({})
                    self.assertTrue(fight.is_stunned(player))
                    self.assertTrue(mine.triggered)
                    self.assertEqual(fight.vitals_of(self.target(run, ana)).total, 70)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                _, deadline = fight.sleeps[player.body]
                run.frame = deadline
                run.match.update(deadline * combat.TICK, deadline, frames=False)
                run.step({player: (0, 0, 32)})
                self.assertFalse(fight.is_stunned(player))
                self.assertEqual(len(fight.mines.seen), reports)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_sleep_during_owner_command_gap_does_not_replay_canceled_minefield(self):
        run, player, ana = self.scenario()
        run.match.switch_hero(ana, heroes()[ANA])
        x, y, z = player.mover.position
        relocate(run, ana, (x, y, z + 6))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        script = player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        angles = aim(fight._eye(ana), (x, y + 1, z))
        for frame in range(30):
            run.step({player: (0, 0, 32 if frame == 17 else 0),
                      ana: (*angles, 8 if frame == 0 else 0)})
            if fight.mines.entities:
                break
        mine, = fight.mines.entities.values()
        original_reports = set(fight.mines.seen)
        self.assertEqual(len(original_reports), 1)
        self.assertFalse(fight.is_stunned(player))
        source_frame = script.frame
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if player.body in fight.sleeps:
                break
        self.assertIn(player.body, fight.sleeps)
        self.assertEqual(script.frame, source_frame)
        relocate(run, ana, (x + 100, y, z))

        for _ in range(450):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(script.frame, source_frame)
        self.assertTrue(mine.body_script.primary().states[28].active)
        self.assertEqual(mine.health, 50)
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(script.frame, run.frame)
        for _ in range(20):
            run.step({player: (0, 0, 32)})
        self.assertEqual(set(fight.mines.entities), {mine.entity})
        self.assertEqual(fight.mines.seen, original_reports)
        self.assertEqual(charge.value(), 0)

    def test_late_preimpact_minefield_stops_at_the_control_boundary(self):
        mccree = 0x02E0000000000042
        for hero, lead, catch_up, reports in (
            (ANA, 1, 'posed', ()), (ANA, 8, 'unposed', (18,)),
            (ANA, 12, 'unposed', (18, 3)), (ANA, 12, 'live', (18, 3)),
            (mccree, 8, 'posed', (18,)),
        ):
            with self.subTest(hero=hex(hero), lead=lead, catch_up=catch_up):
                run, player, caster = self.scenario()
                match = run.match
                match.switch_hero(caster, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, caster, (x, y, z + (6 if hero == ANA else 3)))
                for _ in range(60):
                    run.step({})
                fight = match.combat
                script = player.body_script
                charge = script.component.entity_var(ULT_CHARGE)
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                angles = aim(fight._eye(caster), (x, y + 1, z))
                frozen = script.frame
                if hero == mccree:
                    run.frame += 12  # Leave room for the delayed Q before the shorter projectile flight.
                cast = 8 if hero == ANA else 16
                for i in range(45):
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1],
                                                  buttons=cast if i == 0 else 0)])
                    if fight.is_stunned(player):
                        break
                self.assertTrue(fight.is_stunned(player))
                impact, deadline = run.frame, fight.timed_control_until(player.body)
                self.assertEqual(script.frame, frozen)
                self.assertGreater(impact - lead, frozen)
                relocate(run, caster, (x + 100, y, z))
                run.frame = impact + 15 if catch_up == 'live' else deadline
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                player.take_commands([Command(impact - lead, buttons=32)])
                self.assertEqual(charge.value(), 0)
                resume = run.frame if catch_up == 'unposed' else impact + 15
                player.take_commands([Command(resume)])
                self.assertEqual({key[2] for key in fight.mines.seen}, set(reports))
                self.assertEqual(len(fight.mines.entities), {0: 0, 1: 1, 2: 7}[len(reports)])
                self.assertTrue(all(mine.reported_frame < impact for mine in fight.mines.entities.values()))
                self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
                player.take_commands([Command(run.frame + 1)])
                self.assertEqual(len(fight.mines.seen), len(reports))
                self.assertEqual(charge.value(), 0)

    def test_flashbang_cancels_unreleased_minefield_reports_without_replay(self):
        report_order = (18, 3, 8)
        for flash_frame, reports, count in ((0, 0, 0), (3, 1, 1), (7, 2, 7), (12, 3, 15)):
            with self.subTest(reports=reports):
                run, player, enemy = self.scenario()
                run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
                x, y, z = player.mover.position
                relocate(run, enemy, (x + 3, y, z))
                for _ in range(60):
                    run.step({})
                fight = run.match.combat
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                angles = aim(fight._eye(enemy), (x, y + 1, z))
                for frame in range(30):
                    run.step({player: (0, 0, 32 if frame == 0 else 0),
                              enemy: (*angles, 16 if frame == flash_frame else 0)})
                    if player.body in fight.stuns:
                        break
                self.assertIn(player.body, fight.stuns)
                self.assertEqual(fight.vitals_of(self.target(run, player)).total, 580)
                self.assertEqual({key[2] for key in fight.mines.seen}, set(report_order[:reports]))
                self.assertEqual(len(fight.mines.entities), count)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                created = {mine.entity: mine.health for mine in fight.mines.entities.values()}
                damaged = max(0, count - 1)
                self.assertCountEqual(created.values(), [25] * damaged + [50] * (count - damaged))
                self.assertTrue(fight.stuns.keys().isdisjoint(created))
                relocate(run, enemy, (x + 100, y, z))
                while run.frame < fight.stuns[player.body] - 1:
                    run.step({player: (0, 0, 32)})
                    self.assertEqual(set(fight.mines.entities), set(created))
                run.step({})
                self.assertFalse(fight.is_stunned(player))
                for _ in range(200):
                    run.step({player: (0, 0, 32)})
                remaining = {mine.entity: mine.health for mine in fight.mines.entities.values()}
                self.assertEqual(remaining, created)
                self.assertEqual(len(fight.mines.seen), reports)
                self.assertTrue(all(mine.body_script.primary().states[28].active
                                    for mine in fight.mines.entities.values()))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_passive_charge_resumes_with_live_mines_after_q_and_sleep_interruption(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                run, player, enemy = self.scenario()
                run.match.switch_hero(enemy, heroes()[ANA])
                x, y, z = player.mover.position
                relocate(run, enemy, (x, y, z + 6) if interrupted else (x + 100, y, z))
                for _ in range(60):
                    run.step({})
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = None
                component = script.component
                charge = component.entity_var(ULT_CHARGE)
                lock = component.entity_var(842)
                self.assertIn(0x0B8B, {instance.graph.index for instance in component.instances.values()})
                script.set_ult_charge(component.entity_var(ULT_COST).value())
                angles = aim(fight._eye(enemy), (x, y + 1, z))
                for frame in range(12):
                    run.step({enemy: (*angles, 8 if interrupted and frame == 0 else 0)})
                run.step({player: (0, 0, 32), enemy: (*angles, 0)})
                self.assertEqual(charge.value(), 0)
                self.assertTrue(lock.value())
                for _ in range(90):
                    if not lock.value():
                        break
                    run.step({enemy: (*angles, 0)})
                    self.assertEqual(charge.value(), 0)
                self.assertFalse(lock.value())
                self.assertEqual(fight.is_stunned(player), interrupted)
                self.assertEqual(len(fight.mines.entities), 7 if interrupted else 15)
                deployed = set(fight.mines.entities)
                relocate(run, enemy, (x + 100, y, z))
                for _ in range(61):
                    run.step({})
                    self.assertEqual(charge.value(), 0)
                for _ in range(2):
                    run.step({})
                self.assertEqual(charge.value(), 5)
                self.assertEqual(fight.is_stunned(player), interrupted)
                self.assertEqual(set(fight.mines.entities), deployed)

    def test_deployed_mines_arm_and_hit_after_actual_rifle_kills_ball(self):
        run, player, enemy = self.scenario()
        x, y, z = player.mover.position
        relocate(run, enemy, (x, y, z + 6))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        own = fight.vitals_of(self.target(run, player))
        angles = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(500):
            run.step({enemy: (*angles, 1)})
            if own.total <= 40:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 40)
        deployed = {mine.entity for mine in self.deploy(run, player, ticks=20)}
        self.assertEqual(len(deployed), 15)
        for _ in range(200):
            run.step({enemy: (*angles, 1)})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(own.killer, enemy.body)
        self.assertEqual(set(fight.mines.entities), deployed)

        relocate(run, enemy, (x + 100, y, z))
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(set(fight.mines.entities), deployed)
        mine = next(iter(fight.mines.entities.values()))
        self.assertTrue(mine.body_script.primary().states[28].active)
        for other in list(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        mx, my, mz = mine.position
        relocate(run, enemy, (mx, my, mz + 0.5))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 70)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def partial_minefield_death(self, reports):
        run, player, enemy = self.scenario()
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 6, y, z))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        own = fight.vitals_of(self.target(run, player))
        angles = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(650):
            run.step({enemy: (*angles, 1)})
            if own.total <= 20:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 20)
        for _ in range(90):
            run.step({})
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32)})
        for _ in range(15):
            if len(fight.mines.seen) >= reports:
                break
            run.step({})
        mines = tuple(fight.mines.entities.values())
        self.assertEqual(len(fight.mines.seen), reports)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

        run.step({enemy: (*angles, 1)})
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(own.killer, enemy.body)
        self.assertEqual(len(fight.mines.seen), reports)
        relocate(run, enemy, (x + 100, y, z))
        return run, player, enemy, mines

    def test_actual_rifle_kill_preserves_only_released_minefield_reports(self):
        for reports, expected in ((0, 0), (1, 1), (2, 7), (3, 15)):
            with self.subTest(reports=reports):
                run, player, _, mines = self.partial_minefield_death(reports)
                self.assertEqual(len(mines), expected)
                fight = run.match.combat
                for _ in range(200):
                    run.step({player: (0, 0, 32)})
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(len(fight.mines.seen), reports)
                self.assertEqual(set(fight.mines.entities), {mine.entity for mine in mines})
                self.assertTrue(all(mine.body_script.primary().states[28].active and mine.health == 50
                                    for mine in mines))
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_partial_minefield_death_to_natural_replacement_minefield(self):
        run, player, _, mines = self.partial_minefield_death(1)
        fight = run.match.combat
        old_body = player.body
        old_script = player.body_script
        old_charge = old_script.component.entity_var(ULT_CHARGE)
        for _ in range(60):
            run.step({player: (0, 0, 32)})
            self.assertTrue(fight.is_dead(player))
            self.assertEqual(old_charge.value(), 0)
            self.assertEqual(set(fight.mines.entities), {mines[0].entity})
            self.assertEqual(len(fight.mines.seen), 1)

        for _ in range(650):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(fight.is_dead(player))
        self.assertFalse(fight.mines.entities)
        script = player.body_script
        self.assertIsNone(script.ult_seconds)
        component = script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual((charge.value(), cost), (0, 1540))
        self.assertFalse(component.entity_var(842).value())

        run.step({player: (0, 0, 32)})
        first_tick = None
        for frame in range(2, 21001):
            run.step({})
            value = charge.value()
            if value and first_tick is None:
                first_tick = frame
                self.assertEqual(value, 5)
            if value == cost:
                break
        self.assertIn(first_tick, range(1, 64))
        self.assertEqual(charge.value(), cost)
        self.assertFalse(fight.mines.entities)
        self.assertEqual(old_charge.value(), 0)

        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        self.assertTrue(component.entity_var(842).value())
        for _ in range(40):
            run.step({})
        self.assertEqual(len(fight.mines.entities), 15)
        self.assertTrue(all(mine.source == player.body and mine.owner is player
                            for mine in fight.mines.entities.values()))


    def test_hostile_minefield_resolves_manual_and_automatic_reload(self):
        for automatic in (False, True):
            for filled in (False, True):
                for lethal in (False, True):
                    with self.subTest(automatic=automatic, filled=filled, lethal=lethal):
                        self.check_hostile_mine_reload(automatic, filled, lethal)

    def check_hostile_mine_reload(self, automatic, filled, lethal):
        run, owner, player = self.scenario()
        fight = run.match.combat
        far = tuple(player.mover.position)
        self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
        relocate(run, player, far)
        mine = self.isolated(run, owner)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, player, (x + 8, y, z + 3))
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        weapon = ball.instance(player, 0x1514)
        phase = reload_tests.BallReloadTests().enter_reload(
            run, player, weapon, automatic=automatic, filled=filled,
        )
        ammo = weapon.vars[53]
        saved_ammo = ammo.value()
        own = fight.vitals_of(self.target(run, player))
        if lethal:
            own.now[combat.HEALTH_POOL] = 100
            own.now[combat.ARMOUR_POOL] = 0
            fight._sync_body_health(player, own)

        relocate(run, player, (x, y, z + 0.5))
        run.step({player: (0, 0, 1)})
        self.assertTrue(mine.triggered)
        self.assertEqual(own.total, 0 if lethal else 475)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(ammo.value(), saved_ammo)
        self.assertFalse(weapon.state(9).active)

        if lethal:
            self.assertEqual(own.killer, owner.body)
            self.assertTrue(script.component.owner_dead)
            for _ in range(20):
                run.step({player: (0, 0, 1)})
                self.assertEqual(ammo.value(), saved_ammo)
                self.assertFalse(weapon.state(9).active)
            for _ in range(650):
                run.step({})
                if player.body_script is not script:
                    break
            self.assertTrue(script.retired)
            self.assertEqual(ammo.value(), saved_ammo)
            weapon = ball.instance(player, 0x1514)
            self.assertEqual(weapon.vars[53].value(), 80)
            self.assertEqual(fight.vitals_of(self.target(run, player)).total, 600)
        else:
            self.assertTrue(phase.active)
            self.assertTrue(player.mover.airborne)
            self.assertGreater(player.mover.velocity[1], 0)
            self.assertFalse(fight.is_stunned(player))

        relocate(run, player, (x, y, z))
        relocate(run, owner, (x + 6, y, z))
        owner_vitals = fight.vitals_of(self.target(run, owner))
        before = owner_vitals.total
        angles = aim(fight._eye(player), (x + 6, y + 0.75, z))
        for _ in range(140):
            run.step({player: (*angles, 1)})
            if weapon.state(9).active:
                break
            self.assertEqual(owner_vitals.total, before)
        self.assertTrue(weapon.state(9).active)
        self.assertFalse(weapon.state(21).active or weapon.state(20).active)
        self.assertEqual((weapon.state(21).counter, weapon.state(20).counter), (0, 0) if lethal else (1, 1))
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(owner_vitals.total, before - 2.5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 2.5)



    def test_respawn_retires_armed_mine_and_clears_it_for_both_viewers(self):
        run, player, enemy, mines = self.partial_minefield_death(1)
        mine, = mines
        fight = run.match.combat
        source = player.body
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.is_dead(player))
        self.assertTrue(mine.body_script.primary().states[28].active)
        self.assertEqual(mine.health, 50)
        self.assertEqual(mine.source, source)
        self.assertIn(mine.entity, fight.mines.entities)
        viewers = [actor.client for actor in (player, enemy)]
        for viewer in viewers:
            self.assertEqual([item.op for item in viewer.entities
                              if item.entity == mine.entity].count(world.OP_CREATE), 1)
            viewer.entities.clear()

        for _ in range(650):
            run.step({})
            if player.body != source:
                break
            self.assertIn(mine.entity, fight.mines.entities)
        self.assertNotEqual(player.body, source)
        self.assertFalse(fight.is_dead(player))
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertFalse(fight.mines.seen)

        x, y, z = mine.position
        enemy_vitals = fight.vitals_of(self.target(run, enemy))
        previous_health = enemy_vitals.total
        relocate(run, enemy, (x, y, z + 0.5))
        for _ in range(8):
            run.step({})
        self.assertEqual(enemy_vitals.total, previous_health)
        self.assertFalse(mine.triggered)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for viewer in viewers:
            self.assertEqual([item.op for item in viewer.entities
                              if item.entity == mine.entity].count(world.OP_DESTROY), 1)
            self.assertFalse(any(item.entity == mine.entity and item.op == world.OP_CREATE
                                 for item in viewer.entities))

    def test_enemy_ball_roll_triggers_armed_mine_and_takes_damage(self):
        run, player, enemy = self.scenario()
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 100, y, z))
        mine = self.isolated(run, player)
        mx, my, mz = mine.position
        relocate(run, enemy, (mx, my, mz + 4))
        fight = run.match.combat
        health = fight.vitals_of(self.target(run, enemy))
        initial = health.total
        run.step({enemy: (-32768, 0, 8)})
        self.assertTrue(ball.rolling(enemy))
        self.assertFalse(mine.triggered)
        for _ in range(75):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            enemy.take_commands([Command(run.frame, yaw=-32768, forward=127, extra=0)])
            if mine.triggered:
                break
        self.assertTrue(ball.rolling(enemy))
        self.assertTrue(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(health.total, initial - 125)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_enemy_fireball_cannot_destroy_an_armed_mine_before_it_triggers(self):
        run, owner, enemy = self.scenario()
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = owner.mover.position
        relocate(run, enemy, (x + 100, y, z))
        mine = self.isolated(run, owner)
        self.assertTrue(mine.armed)
        mx, my, mz = mine.position
        relocate(run, enemy, (mx, my, mz - 6))
        prepare_swing(run, enemy)
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, enemy))
        self.assertEqual(vitals.total, 600)
        script = enemy.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        instance = ball.instance(enemy, 0x1203)
        for _ in range(100):
            search = instance.states.get(35)
            fireball = search is not None and search.active
            hold(run, enemy, 1)
            if mine.triggered:
                break
            self.assertEqual(mine.health, 50)
        self.assertTrue(fireball)
        self.assertTrue(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(vitals.total, 475)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_arming_after_contact_then_one_actual_130_hit_and_no_ultimate_charge(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        self.assertTrue(mine.landed)
        self.assertTrue(primary.states[28].active)
        self.assertEqual(primary.state(27).counter, 0)
        self.assertEqual(primary.state(5).counter, 0)
        self.assertTrue(18 <= primary.vars[6471].value() <= 20)
        point = mine.position
        relocate(run, enemy, (point[0], point[1], point[2] + 0.5))
        run.step({})
        fight = run.match.combat
        self.assertTrue(mine.triggered)
        self.assertFalse(primary.state(28).active)
        self.assertEqual(primary.state(27).counter, 1)
        self.assertEqual(primary.state(5).counter, 1)
        self.assertEqual(primary.vars[1193].value(), 1)
        self.assertEqual(primary.vars[375].value(), (expr.Entity(enemy.body),))
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 70)
        self.assertGreater(enemy.mover.velocity[1], 4)
        self.assertGreater(enemy.mover.velocity[2], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(30):
            run.step({})
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 70)

    def test_mine_damage_uses_the_second_search_output(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        radius = primary.graph.node(50).field('m_radius')
        evaluate = primary.evaluate

        def narrow_damage_search(config, *args, **kwargs):
            return 0.0 if config is radius else evaluate(config, *args, **kwargs)

        x, y, z = mine.position
        relocate(run, enemy, (x + 1, y, z))
        with patch.object(primary, 'evaluate', side_effect=narrow_damage_search):
            run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(primary.vars[1193].value(), 1)
        self.assertEqual(primary.vars[375].value(), ())
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 200)

    def test_one_mine_explosion_hits_each_eligible_player_once(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Left', 'Right', 'Ally', 'Outside'))
        player, left, right, ally, outside = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
        ally.team = player.team
        x, y, z = player.mover.position
        for actor in (left, right, ally, outside):
            if actor is not ally:
                actor.team = 1 - player.team
            relocate(run, actor, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        for actor, dx, dz in ((left, -1, 0), (right, 1, 0), (ally, 0, 1), (outside, 0, 4)):
            relocate(run, actor, (x + dx, y, z + dz))
        fight = run.match.combat
        vitals = [fight.vitals_of(self.target(run, actor)) for actor in (left, right, ally, outside)]
        self.assertEqual([v.total for v in vitals], [200, 200, 200, 200])
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual([v.total for v in vitals], [70, 70, 200, 200])
        self.assertLess(left.mover.velocity[0], -4)
        self.assertGreater(right.mover.velocity[0], 4)
        self.assertGreater(left.mover.velocity[1], 4)
        self.assertGreater(right.mover.velocity[1], 4)
        for _ in range(30):
            run.step({})
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual([v.total for v in vitals], [70, 70, 200, 200])
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_overlapping_enemy_mines_trigger_independently(self):
        run, player, enemy = self.scenario()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[ball.HERO]))
        point = tuple(player.mover.position)
        relocate(run, enemy, point)
        for _ in range(40):
            run.step({})
        for owner in (player, enemy):
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32), enemy: (0, 0, 32)})
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        mines = list(fight.mines.entities.values())
        own_mine, enemy_mine = (
            min((mine for mine in mines if mine.owner is owner), key=lambda mine: mine.entity)
            for owner in (player, enemy)
        )
        for mine in mines:
            if mine is not own_mine and mine is not enemy_mine:
                fight.mines.remove(mine)
        x, y, z = point
        relocate(run, player, (x - 20, y, z))
        relocate(run, enemy, (x + 20, y, z))
        for _ in range(200):
            run.step({})
        self.assertTrue(own_mine.armed and enemy_mine.armed)
        self.assertLess(math.dist(own_mine.position, enemy_mine.position), 0.1)
        self.assertEqual([own_mine.health, enemy_mine.health], [50, 50])
        self.assertFalse(own_mine.triggered or enemy_mine.triggered)

        x, y, z = own_mine.position
        relocate(run, enemy, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(own_mine.triggered)
        self.assertFalse(enemy_mine.triggered)
        self.assertEqual(enemy_mine.health, 50)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 475)
        for _ in range(30):
            run.step({})
        self.assertNotIn(own_mine.entity, fight.mines.entities)
        self.assertIs(fight.mines.entities.get(enemy_mine.entity), enemy_mine)
        self.assertTrue(enemy_mine.armed)
        self.assertEqual(enemy_mine.health, 50)

        x, y, z = enemy_mine.position
        relocate(run, player, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(enemy_mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, player)).total, 475)
        for owner in (player, enemy):
            self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_authored_mine_filter_short_circuits_component_key_and_routes_candidate_source(self):
        run, owner, enemy = self.scenario()
        mine = self.isolated(run, owner)
        primary = mine.body_script.primary()
        condition = primary.graph.node(52).field('m_filter')['m_condition']
        key_query, fallback_query = condition['m_configVars']
        self.assertEqual(key_query['m_88B0050A'], '0x0D600000000002CB')
        self.assertEqual(fallback_query['$'], 'STU_A58C1E63')
        owner_component = owner.body_script.component
        supplied_source = (expr.Entity(enemy.body),)
        producer = owner_component.play_loaded_graph(
            graph.graph(0x0636), source=owner.body, params=[(0x0A40000000000392, supplied_source)],
        )
        # Isolate the authored start binding from the unresolved 0636 initializer and effects.
        assignment, = (event for event in producer.queue if event.kind == runtime.ASSIGN)
        self.assertEqual(assignment.assignment, (expr.ENTITY, 8117, supplied_source))
        producer.queue.clear()
        producer.post(assignment)
        owner_component.run_now()
        self.assertEqual(owner_component.entity_var(8117).value(), supplied_source)
        key = 0x2CB
        present = entity_queries.Component76Keys(primary=frozenset({key}))
        fallback_key = entity_queries.Component76Keys(fallback=frozenset({key}))
        suppressed = entity_queries.Component76Keys(
            primary=frozenset({key}), fallback=frozenset({key}), suppressed=frozenset({key}),
        )
        cases = (
            (None, False, True, False),
            (present, False, False, True),
            (present, True, True, True),
            (fallback_key, False, False, True),
            (suppressed, False, True, False),
        )
        component = primary.component
        mine_world = component.world
        native = mine_world.native

        for keys, fallback, expected, called in cases:
            with self.subTest(keys=keys, fallback=fallback):
                def evaluate_native(cfg, instance, *, result=fallback):
                    if cfg is fallback_query:
                        self.assertIs(instance, primary)
                        self.assertEqual(instance.evaluate(cfg['m_target']), expr.Entity(mine.entity))
                        source = instance.evaluate(cfg['m_source'])
                        self.assertEqual(source, supplied_source)
                        self.assertEqual(expr.to_entity(source), enemy.body)
                        return result
                    return native(cfg, instance)

                with (patch.object(mine_world, 'component76_keys', return_value=keys) as lookup,
                      patch.object(mine_world, 'native', side_effect=evaluate_native) as query,
                      component.parameter_scope({8: expr.Entity(owner.body)})):
                    self.assertIs(primary.evaluate(condition), expected)
                lookup.assert_called_once_with(owner.body)
                self.assertEqual(query.call_count, int(called))


    def test_mine_blast_leaves_nearby_allied_and_emp_disabled_mines_independent(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Allied Ball', 'Enemy Ball', 'Sombra'))
        player, ally, enemy, sombra = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team = ally.team = sombra.team = 0
        enemy.team = 1
        x, y, z = player.mover.position
        for actor, offset in ((player, 0), (ally, 1.35), (enemy, 0)):
            self.assertTrue(match.switch_hero(actor, heroes()[ball.HERO]))
            relocate(run, actor, (x + offset, y, z))
        self.assertTrue(match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA]))
        relocate(run, sombra, (x + 30, y, z))
        for _ in range(60):
            run.step({})
        owners = (player, ally, enemy)
        for owner in owners:
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step(dict.fromkeys(owners, (0, 0, 32)))
        for _ in range(20):
            run.step({})
        fight = match.combat
        mines = tuple(fight.mines.entities.values())
        first, allied, disabled = (
            min((mine for mine in mines if mine.owner is owner), key=lambda mine: mine.entity)
            for owner in owners
        )
        retained = {first.entity, allied.entity, disabled.entity}
        for mine in mines:
            if mine.entity not in retained:
                fight.mines.remove(mine)
        for owner, offset in zip(owners, (-20, -30, 20), strict=True):
            relocate(run, owner, (x + offset, y, z))
        for _ in range(200):
            run.step({})
        self.assertTrue(all(mine.armed and mine.health == 50 for mine in (first, allied, disabled)))

        x, y, z = first.position
        radius = expr.to_float(first.body_script.primary().evaluate(
            first.body_script.primary().graph.node(52).field('m_radius'),
        ))
        self.assertLess(math.dist(first.position, allied.position), radius)
        self.assertLess(math.dist(first.position, disabled.position), 0.1)
        relocate(run, sombra, (x, y, z + 6))
        script = sombra.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        emp_tests.BallEmpTests().cast(run, sombra)
        self.assertTrue(first.armed and allied.armed)
        component = disabled.body_script.component
        for _ in range(5):
            run.step({})
        self.assertFalse(disabled.armed)
        effect, = (i for i in component.instances.values() if i.graph.index == sombra_emp.EFFECT)
        deadline = math.ceil((effect.start + 10000) / component.frame_ms)

        relocate(run, enemy, (x - 0.9, y, z))
        run.step({})
        self.assertTrue(first.triggered)
        selected = first.body_script.primary().vars[375].value()
        self.assertEqual(selected, (expr.Entity(enemy.body),))
        self.assertFalse(allied.triggered or disabled.triggered)
        self.assertEqual((allied.health, disabled.health), (50, 50))
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 475)

        ax, ay, az = allied.position
        relocate(run, enemy, (ax + 0.5, ay, az))
        run.step({})
        self.assertTrue(allied.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 345)
        self.assertEqual(disabled.health, 50)
        dx, dy, dz = disabled.position
        relocate(run, player, (dx, dy, dz + 0.5))
        run.frame = deadline - 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(disabled.triggered or disabled.armed)
        self.assertEqual(disabled.health, 50)
        self.assertEqual(fight.vitals_of(self.target(run, player)).total, 600)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(disabled.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, player)).total, 475)
        self.assertTrue(all(owner.body_script.component.entity_var(ULT_CHARGE).value() == 0
                            for owner in owners))


    def test_wraith_cannot_trigger_armed_mine_until_phase_expires(self):
        run, player, reaper = self.scenario()
        run.match.switch_hero(reaper, heroes()[REAPER])
        x, y, z = player.mover.position
        relocate(run, reaper, (x + 100, y, z))
        mine = self.isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        run.step({reaper: (0, 0, 8)})
        phase = phase_instance(reaper).states[5]
        self.assertTrue(phase.active)
        x, y, z = mine.position
        relocate(run, reaper, (x, y, z + 0.5))
        fight = run.match.combat
        for _ in range(40):
            run.step({})
        self.assertTrue(phase.active)
        self.assertFalse(mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, reaper)).total, 250)
        self.assertFalse(reaper.mover.airborne)
        for _ in range(160):
            run.step({})
            if mine.triggered:
                break
        self.assertFalse(phase.active)
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, reaper)).total, 120)
        self.assertGreater(reaper.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_armed_mine_triggers_on_phase_world_expiry_without_recipient_commands(self):
        for hero in (REAPER, phase_tests.MOIRA):
            with self.subTest(hero=hero):
                self.check_mine_at_phase_deadline(hero)

    def check_mine_at_phase_deadline(self, hero):
        run, player, enemy = self.scenario()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[hero]))
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 100, y, z))
        mine = self.isolated(run, player)
        run.step({enemy: (0, 0, 8)})
        script = enemy.body_script
        frozen = script.frame, script.component.now
        timer = phase_instance(enemy).state(3)
        deadline = (timer.start + timer.ms) // script.frame_ms
        x, y, z = mine.position
        relocate(run, enemy, (x, y, z + 0.5))
        match = run.match
        fight = match.combat
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        vitals = fight.vitals_of(self.target(run, enemy))
        before = vitals.total
        self.assertTrue(mine.armed)
        self.assertFalse(mine.triggered)
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertEqual((script.frame, script.component.now), frozen)
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, before - 130)
        self.assertGreater(enemy.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_armed_mine_excludes_fade_and_wraith_even_when_another_body_triggers(self):
        for soldier_triggers in (False, True):
            with self.subTest(soldier_triggers=soldier_triggers):
                self.check_phased_mine_recipients(soldier_triggers)

    def check_phased_mine_recipients(self, soldier_triggers):
        run = Run(KANEZAKA_DM, ('Ball', 'Moira', 'Reaper', 'Soldier'))
        player, moira, reaper, soldier = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, moira.team, reaper.team, soldier.team = 0, 1, 1, 1
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for actor, hero in ((player, ball.HERO), (moira, phase_tests.MOIRA), (reaper, REAPER)):
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
        x, y, z = player.mover.position
        for offset, actor in zip((100, 110, 120), (moira, reaper, soldier), strict=True):
            relocate(run, actor, (x + offset, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        self.assertTrue(mine.armed)
        run.step({moira: (0, 0, 8), reaper: (0, 0, 8)})
        fade = phase_instance(moira).states[4]
        wraith = phase_instance(reaper).states[5]
        self.assertTrue(fade.active)
        self.assertTrue(wraith.active)

        x, y, z = mine.position
        relocate(run, moira, (x + 1, y, z))
        relocate(run, reaper, (x - 1, y, z))
        fight = match.combat
        moira_hp, reaper_hp, soldier_hp = (
            fight.vitals_of(self.target(run, actor)) for actor in (moira, reaper, soldier)
        )
        for _ in range(10):
            run.step({})
            self.assertTrue(fade.active)
            self.assertTrue(wraith.active)
            self.assertFalse(mine.triggered)
        if soldier_triggers:
            relocate(run, soldier, (x, y, z + 1))
            run.step({})
        else:
            for _ in range(50):
                run.step({})
                if not fade.active:
                    break
                self.assertFalse(mine.triggered)
            self.assertFalse(fade.active)

        self.assertTrue(mine.triggered)
        self.assertEqual(fade.active, soldier_triggers)
        self.assertTrue(wraith.active)
        hit = soldier if soldier_triggers else moira
        self.assertEqual(primary.vars[375].value(), (expr.Entity(hit.body),))
        self.assertEqual(moira_hp.total, 200 if soldier_triggers else 70)
        self.assertEqual(reaper_hp.total, 250)
        self.assertEqual(soldier_hp.total, 70 if soldier_triggers else 200)
        self.assertGreater(hit.mover.velocity[1], 4)
        self.assertFalse(reaper.mover.airborne)
        if soldier_triggers:
            self.assertFalse(moira.mover.airborne)
        for _ in range(200):
            run.step({})
            if not wraith.active:
                break
        self.assertFalse(fade.active)
        self.assertFalse(wraith.active)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(reaper_hp.total, 250)
        self.assertEqual(moira_hp.total, 200 if soldier_triggers else 70)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_armed_mine_hits_through_active_deflect_and_grasp(self):
        for hero, graph_index, state, button in (
            (deflection.GENJI, 0x03A3, 10, 16),
            (deflection.SIGMA, 0x1D02, 1, 8),
        ):
            with self.subTest(hero=hero):
                run, player, enemy = self.scenario()
                run.match.switch_hero(enemy, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, enemy, (x + 100, y, z))
                mine = self.isolated(run, player)
                self.assertTrue(mine.body_script.primary().states[28].active)
                run.step({enemy: (-32768, 0, button)})
                defense = next(i for i in enemy.body_script.component.instances.values()
                               if i.graph.index == graph_index)
                fight = run.match.combat
                own = fight.vitals_of(self.target(run, player))
                vitals = fight.vitals_of(self.target(run, enemy))
                before = vitals.total
                x, y, z = mine.position
                relocate(run, enemy, (x, y, z + 0.5))
                run.step({enemy: (-32768, 0, 0)})

                self.assertTrue(defense.states[state].active)
                self.assertTrue(mine.triggered)
                self.assertEqual(vitals.total, before - 130)
                self.assertGreater(enemy.mover.velocity[1], 4)
                self.assertEqual(own.total, 600)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(enemy.client.messages(combat.HIT))
                if hero == deflection.SIGMA:
                    self.assertEqual(defense.vars[21312].value(), 0)

    def test_actual_emp_disables_armed_mine_and_natural_expiry_restores_trigger(self):
        run, player, sombra = self.scenario()
        distant = tuple(sombra.mover.position)
        run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
        relocate(run, sombra, distant)
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, sombra, (x, y, z + 6))
        script = sombra.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        emp_tests.BallEmpTests().cast(run, sombra)
        component = mine.body_script.component
        child, = (i for i in component.instances.values() if i.graph.index == sombra_emp.CHILD)
        self.assertEqual(child.source_entity(), sombra.body)
        for _ in range(5):
            run.step({})
        effect, = (i for i in component.instances.values() if i.graph.index == sombra_emp.EFFECT)
        self.assertEqual(effect.source_entity(), sombra.body)
        self.assertEqual(effect.vars[215].value(), 10)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)

        relocate(run, sombra, (x, y, z + 0.5))
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, sombra))
        deadline = math.ceil((effect.start + 10000) / component.frame_ms)
        run.frame = deadline - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(mine.triggered)
        self.assertFalse(mine.armed)
        self.assertTrue(child.teardown)
        self.assertFalse(effect.teardown)
        self.assertEqual(mine.health, 50)
        self.assertEqual(vitals.total, 200)

        run.frame = deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(effect.teardown)
        self.assertFalse(component.entity_var(8912).value())
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)
        self.assertGreater(sombra.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_overlapping_actual_emps_keep_mine_disabled_until_last_expiry(self):
        run = Run(KANEZAKA_DM, ('Ball', 'First Sombra', 'Second Sombra'))
        player, first, second = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, first.team, second.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        x, y, z = player.mover.position
        for caster in (first, second):
            run.match.switch_hero(caster, heroes()[sombra_emp.SOMBRA])
            relocate(run, caster, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, first, (x, y, z + 6))
        relocate(run, second, (x + 6, y, z))
        component = mine.body_script.component
        for caster in (first, second):
            caster.body_script.ult_seconds = 0
            caster.body_script.set_ult_charge(caster.body_script.component.entity_var(ULT_COST).value())
            emp_tests.BallEmpTests().cast(run, caster)
            for _ in range(5):
                run.step({})
            self.assertFalse(mine.armed)

        effects = {i.source_entity(): i for i in component.instances.values()
                   if i.graph.index == sombra_emp.EFFECT}
        self.assertEqual(set(effects), {first.body, second.body})
        first_effect, second_effect = effects[first.body], effects[second.body]
        self.assertEqual(first_effect.vars[215].value(), 10)
        self.assertEqual(second_effect.vars[215].value(), 10)
        first_deadline, second_deadline = (
            math.ceil((effect.start + 10000) / component.frame_ms)
            for effect in (first_effect, second_effect)
        )
        self.assertLess(first_deadline, second_deadline)
        relocate(run, second, (x, y, z + 0.5))
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(run, second))
        run.frame = first_deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(first_effect.teardown)
        self.assertFalse(second_effect.teardown)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(vitals.total, 200)

        run.frame = second_deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(second_effect.teardown)
        self.assertFalse(component.entity_var(8912).value())
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_enemy_rifle_destroys_emp_disabled_mine_before_expiry(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Sombra', 'Soldier'))
        player, sombra, soldier = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, sombra.team, soldier.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
        run.match.switch_hero(soldier, heroes()[SOLDIER])
        x, y, z = player.mover.position
        for enemy in (sombra, soldier):
            relocate(run, enemy, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)

        x, y, z = mine.position
        relocate(run, sombra, (x, y, z + 6))
        script = sombra.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        emp_tests.BallEmpTests().cast(run, sombra)
        for _ in range(5):
            run.step({})
        component = mine.body_script.component
        effect, = (i for i in component.instances.values() if i.graph.index == sombra_emp.EFFECT)
        deadline = math.ceil((effect.start + 10000) / component.frame_ms)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)

        fight = run.match.combat
        relocate(run, soldier, (x - 3, y, z))
        angles = aim(fight._eye(soldier), (x, y + 0.23, z))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(mine.health, 30)
        for _ in range(20):
            run.step({soldier: (*angles, 1)})
        self.assertEqual(mine.health, 0)
        for _ in range(90):
            run.step({})
        self.assertLess(run.frame, deadline)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertNotIn(mine.entity, fight.vitals)
        self.assertTrue(effect.teardown)
        self.assertFalse(component.instances)
        self.assertEqual(soldier.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

        relocate(run, soldier, (x, y, z + 0.5))
        vitals = fight.vitals_of(self.target(run, soldier))
        run.frame = deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(mine.triggered)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertFalse(component.instances)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for viewer in (player, sombra, soldier):
            self.assertEqual(sum(item.entity == mine.entity and item.op == world.OP_DESTROY
                                 for item in viewer.client.entities), 1)

    def test_actual_emp_during_flight_still_disables_mine_after_landing(self):
        run, player, sombra = self.scenario()
        run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
        x, y, z = player.mover.position
        relocate(run, sombra, (x + 6, y, z))
        for _ in range(40):
            run.step({})
        for owner in (player, sombra):
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        emp_tests.BallEmpTests().cast(run, sombra, {player: (0, 0, 32)})
        fight = run.match.combat
        self.assertEqual(len(fight.mines.entities), 15)
        mine = min(fight.mines.entities.values(), key=lambda item: item.entity)
        for other in tuple(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        component = mine.body_script.component
        self.assertFalse(mine.landed)
        self.assertTrue(any(i.graph.index == sombra_emp.CHILD for i in component.instances.values()))
        previous = mine.position
        run.step({})
        self.assertNotEqual(mine.position, previous)
        self.assertTrue(component.entity_var(8912).value())
        for _ in range(220):
            run.step({})
            if mine.landed:
                break
        self.assertTrue(mine.landed)
        self.assertTrue(component.entity_var(8912).value())
        self.assertFalse(mine.armed)
        x, y, z = mine.position
        relocate(run, sombra, (x, y, z + 0.5))
        for _ in range(120):
            run.step({})
        self.assertFalse(mine.armed)
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)
        self.assertEqual(fight.vitals_of(self.target(run, sombra)).total, 200)

    def test_actual_emp_leaves_allied_covered_and_out_of_range_mines_armed(self):
        for case in ('ally', 'wall', 'range'):
            with self.subTest(case=case):
                run, player, sombra = self.scenario()
                distant = tuple(sombra.mover.position)
                run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
                relocate(run, sombra, distant)
                mine = self.isolated(run, player)
                x, y, z = mine.position
                relocate(run, sombra, (x, y, z + (17 if case == 'range' else 6)))
                if case == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sombra.team = player.team
                if case == 'wall':
                    a, b = (x - 3, y, z + 3), (x + 3, y, z + 3)
                    c, d = (x + 3, y + 4, z + 3), (x - 3, y + 4, z + 3)
                    run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])
                script = sombra.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                emp_tests.BallEmpTests().cast(run, sombra)
                for _ in range(5):
                    run.step({})
                self.assertTrue(mine.armed)
                self.assertEqual(mine.health, 50)
                self.assertFalse(any(i.graph.index in (sombra_emp.CHILD, sombra_emp.EFFECT)
                                     for i in mine.body_script.component.instances.values()))

    def test_endpoint_mine_does_not_expire_adaptive_shield_before_earlier_rifle_pulses(self):
        for gap in (0, 45, 65):
            with self.subTest(gap=gap):
                self.check_endpoint_mine_with_expiring_shield(gap)

    def check_endpoint_mine_with_expiring_shield(self, gap):
        run = Run(KANEZAKA_DM, ('Mine owner', 'Target Ball', 'Ana'))
        owner, victim, ana = run.players
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        owner.team, victim.team, ana.team = 0, 1, 0
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for actor, hero in ((owner, ball.HERO), (victim, ball.HERO), (ana, ANA)):
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
        x, y, z = owner.mover.position
        relocate(run, victim, (x + 30, y, z))
        relocate(run, ana, (x + 40, y, z))
        for _ in range(40):
            run.step({})
        owner.body_script.ult_seconds = 0
        mine = self.isolated(run, owner)
        x, y, z = mine.position
        relocate(run, victim, (x + 10, y, z))
        relocate(run, ana, (x + 10, y, z + 6))
        run.step({victim: (0, 0, 16)})
        own = fight.vitals[victim.body]
        deadline = own.adaptive_until
        self.assertEqual(deadline, run.frame + 562)
        run.frame = deadline - 55
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        rifle = aim(fight._eye(ana), (x + 10, y + 0.8, z))
        while run.frame < deadline - 12:
            run.step({ana: (*rifle, 2)})
        run.step({ana: (*rifle, 3)})
        for _ in range(8):
            if fight.rifle_doses:
                break
            run.step({ana: (*rifle, 2)})
        dose, = fight.rifle_doses
        self.assertEqual(dose.recipient, victim.body)
        self.assertLess(run.frame, deadline)
        self.assertTrue(mine.armed)
        self.assertFalse(mine.triggered)
        self.assertGreater(own.temporary_shields, 0)
        shielded = ((deadline - 1) * combat.TICK - dose.start) * dose.amount / combat.ANA_RIFLE_DURATION
        endpoint = run.frame + (gap or 45)
        if not gap:
            for tick in range(run.frame + 1, endpoint):
                match.update(tick * combat.TICK, tick, frames=False)
        relocate(run, victim, (x, y, z + 0.5))
        run.frame = endpoint
        match.update(endpoint * combat.TICK, endpoint, frames=False)
        self.assertTrue(mine.triggered)
        self.assertEqual(own.temporary_shields, 0)
        self.assertAlmostEqual(own.total, 600 - (dose.amount - shielded) - 125)
        self.assertFalse(fight.rifle_doses)
        self.assertGreater(victim.mover.velocity[1], 4)

    def test_mine_damage_and_impulse_during_cryo_and_transcendence(self):
        for hero, cancel in ((barriers.MEI, False), (barriers.MEI, True), (phase_tests.ZENYATTA, False)):
            with self.subTest(hero=hex(hero), cancel=cancel):
                run, player, enemy = self.scenario()
                run.match.switch_hero(enemy, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, enemy, (x + 100, y, z))
                mine = self.isolated(run, player)
                self.assertTrue(mine.body_script.primary().states[28].active)
                phase = phase_tests.BallPhaseTests()
                if hero == barriers.MEI:
                    run.step({enemy: (-32768, 0, 8)})
                    _, immunity = phase.protection_instance(enemy)
                    self.assertTrue(immunity.active)
                    if cancel:
                        for _ in range(20):
                            run.step({})
                        run.step({enemy: (-32768, 0, 2)})
                        self.assertFalse(immunity.active)
                else:
                    immunity = phase.cast_transcendence(run, enemy)

                fight = run.match.combat
                vitals = fight.vitals_of(self.target(run, enemy))
                before = vitals.total
                x, y, z = mine.position
                relocate(run, enemy, (x, y, z + 0.5))
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(immunity.active, not cancel)
                self.assertEqual(vitals.total, before - (130 if cancel else 0))
                if hero == barriers.MEI and not cancel:
                    self.assertEqual(tuple(enemy.mover.velocity), (0, 0, 0))
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[1], 4)
                    self.assertGreater(enemy.mover.velocity[2], 4)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_live_cryo_stack_excludes_the_candidate_from_damage_search(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, player, mei = self.scenario()
                run.match.switch_hero(mei, heroes()[barriers.MEI])
                x, y, z = player.mover.position
                relocate(run, mei, (x + 100, y, z))
                mine = self.isolated(run, player)
                run.step({mei: (-32768, 0, 8)})
                stack = mei.body_script.component.entity_var(9588, create=False)
                self.assertTrue(expr.truthy(stack.value()))
                if cancel:
                    for _ in range(20):
                        run.step({})
                    run.step({mei: (-32768, 0, 2)})
                    self.assertFalse(expr.truthy(stack.value()))

                primary = mine.body_script.primary()
                mine_world = primary.component.world
                native = mine_world.native
                relation_calls = []
                relations = {
                    id(primary.graph.node(pos).field('m_filter')['m_condition']['m_configVars'][-1]): pos
                    for pos in (52, 50)
                }

                def evaluate_native(
                    config, instance, *, relations=relations, native=native, calls=relation_calls,
                ):
                    pos = relations.get(id(config))
                    if pos is None:
                        return native(config, instance)
                    calls.append(pos)
                    return True

                x, y, z = mine.position
                relocate(run, mei, (x, y, z + 0.5))
                keys = entity_queries.Component76Keys(primary=frozenset({0x2CB}))
                with (patch.object(mine_world, 'component76_keys', return_value=keys),
                      patch.object(mine_world, 'native', side_effect=evaluate_native)):
                    run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(relation_calls, [52, 50] if cancel else [52])
                self.assertEqual(primary.vars[375].value(), (expr.Entity(mei.body),) if cancel else ())

    def test_cryo_protection_expires_for_minefield_without_mei_commands(self):
        run, player, mei = self.scenario()
        run.match.switch_hero(mei, heroes()[barriers.MEI])
        x, y, z = player.mover.position
        relocate(run, mei, (x + 100, y, z))
        mine = self.isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        run.step({mei: (-32768, 0, 8)})
        _, immunity = phase_tests.BallPhaseTests().protection_instance(mei)
        source = mei.body_script
        frozen = source.frame, source.component.now
        deadline = (immunity.start + 4000) // source.frame_ms
        match = run.match
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        self.assertFalse(mine.triggered)
        x, y, z = mine.position
        relocate(run, mei, (x, y, z + 0.5))
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(immunity.active)
        self.assertTrue(mine.triggered)
        self.assertEqual(match.combat.vitals_of(self.target(run, mei)).total, 120)
        self.assertGreater(mei.mover.velocity[1], 4)
        self.assertGreater(mei.mover.velocity[2], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def cast_nano(self, run, pairs):
        fight = run.match.combat
        casts = {}
        for caster, recipient in pairs:
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            tx, ty, tz = recipient.mover.position
            casts[caster] = (*aim(fight._eye(caster), (tx, ty + 0.8, tz)), 32)
        run.step(casts)
        for _ in range(13):
            run.step({})
        for caster, recipient in pairs:
            self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
            self.assertGreater(fight.vitals[recipient.body].nano_until, run.frame)

    def test_actual_nano_and_minefield_keep_deployable_and_body_damage_separate(self):
        for case, loss in (('deploy', 125), ('armed', 65), ('armor', 60), ('expired', 125), ('bubble', 0)):
            with self.subTest(case=case):
                run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)),
                          ('Owner Ball', 'Target Ball', 'Ana', 'Enemy Ana', 'Zarya'))
                player, enemy, ana, enemy_ana, zarya = run.players
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                x, y, z = player.mover.position
                for actor, hero, team, dx, dz in (
                    (player, ball.HERO, 0, 0, 0), (enemy, ball.HERO, 1, 100, 0),
                    (ana, ANA, 0, -6, 0), (enemy_ana, ANA, 1, 106, 0),
                    (zarya, barriers.ZARYA, 1, 100, 6),
                ):
                    actor.team = team
                    self.assertTrue(match.switch_hero(actor, heroes()[hero]))
                    relocate(run, actor, (x + dx, y, z + dz))
                    actor.body_script.ult_seconds = 0
                    actor.body_script.set_ult_charge(0)
                for _ in range(40):
                    run.step({})
                fight = match.combat
                owner = fight.vitals_of(self.target(run, player))
                victim = fight.vitals_of(self.target(run, enemy))

                if case in ('deploy', 'armor'):
                    self.cast_nano(run, ((ana, player),))
                elif case == 'expired':
                    self.cast_nano(run, ((enemy_ana, enemy),))
                mine = self.isolated(run, player)
                self.assertTrue(mine.armed)
                self.assertFalse(mine.triggered)
                mx, my, mz = mine.position
                if case in ('armed', 'bubble'):
                    relocate(run, ana, (mx + 15, my, mz))
                    self.cast_nano(run, ((ana, player), (enemy_ana, enemy)))
                elif case == 'armor':
                    self.cast_nano(run, ((enemy_ana, enemy),))

                if case == 'expired':
                    run.frame = victim.nano_until
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertTrue(mine.armed)
                elif case == 'armed':
                    run.step({enemy: (0, 0, 16)})
                    self.assertGreater(victim.temporary_shields, 65)
                elif case == 'bubble':
                    run.step({zarya: (-32768, 0, 16)})
                    for _ in range(12):
                        run.step({zarya: (-32768, 0, 0)})
                    bubble, = barriers.projected_on(fight, enemy.body)

                self.assertEqual(owner.nano_until > run.frame, case != 'expired')
                self.assertEqual(victim.nano_until > run.frame, case in ('armed', 'armor', 'bubble'))
                before, shield = victim.total, victim.temporary_shields
                relocate(run, enemy, (mx, my, mz + 0.5))
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(victim.total, before - loss)
                self.assertEqual(victim.temporary_shields, shield - loss if case == 'armed' else 0)
                if case == 'bubble':
                    self.assertEqual(bubble.vitals.total, 70)
                    self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 26)
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[1], 4)
                    self.assertGreater(enemy.mover.velocity[2], 4)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_nano_rejects_an_armed_friendly_mine_as_a_target(self):
        run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)), ('Ball', 'Ana'))
        player, ana = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team = ana.team = 0
        self.assertTrue(match.switch_hero(player, heroes()[ball.HERO]))
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        ana.body_script.ult_seconds = 0
        for _ in range(40):
            run.step({})
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        x, y, z = mine.position
        relocate(run, ana, (x - 4, y, z))
        relocate(run, player, (x + 70, y, z))
        fight = match.combat
        script = ana.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        angles = aim(fight._eye(ana), (x, y + 0.23, z))
        for frame in range(18):
            run.step({ana: (*angles, 32 if frame == 0 else 0)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertEqual(mine.health, 50)
        self.assertFalse(mine.triggered)

        relocate(run, player, (x, y, z + 3))
        self.cast_nano(run, ((ana, player),))
        self.assertTrue(mine.armed)

    def test_transcendence_settles_final_heal_before_mine_at_world_expiry(self):
        for health in (128, 200):
            with self.subTest(health=health):
                self.check_final_transcendence_heal_before_mine(health)

    def check_final_transcendence_heal_before_mine(self, health):
        run, owner, zen = self.scenario()
        self.assertTrue(run.match.switch_hero(zen, heroes()[phase_tests.ZENYATTA]))
        x, y, z = owner.mover.position
        relocate(run, zen, (x + 100, y, z))
        mine = self.isolated(run, owner)
        immunity = phase_tests.BallPhaseTests().cast_transcendence(run, zen)
        source = zen.body_script
        frozen = source.frame, source.component.now
        lifetime = immunity.instance.state(3)
        self.assertEqual(lifetime.start % source.frame_ms, 0)
        deadline = (lifetime.start + 6000) // source.frame_ms
        match = run.match
        fight = match.combat
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        self.assertFalse(mine.triggered)
        vitals = fight.vitals_of(self.target(run, zen))
        vitals.damage(200 - health)
        fight._sync_body_health(zen, vitals)
        x, y, z = mine.position
        relocate(run, zen, (x, y, z + 0.5))
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(immunity.active)
        self.assertTrue(mine.triggered)
        final_heal = min(200 - health, 4.8)
        remaining = health + final_heal - 130
        self.assertAlmostEqual(vitals.total, remaining)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), final_heal)
        self.assertGreater(zen.mover.velocity[1], 4)
        match.update((deadline + 1) * combat.TICK, deadline + 1, frames=False)
        self.assertAlmostEqual(vitals.total, remaining)

    def test_transcendence_heals_mine_survivor_without_reviving_lethal_target(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Zenyatta', 'Survivor', 'Lethal target'))
        player, zen, survivor, lethal = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team = 0
        zen.team = survivor.team = lethal.team = 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(zen, heroes()[phase_tests.ZENYATTA])
        x, y, z = player.mover.position
        for actor in (zen, survivor, lethal):
            relocate(run, actor, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = zen.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.armed)
        immunity = phase_tests.BallPhaseTests().cast_transcendence(run, zen)

        fight = run.match.combat
        zen_vitals, survivor_vitals, lethal_vitals = (
            fight.vitals_of(self.target(run, actor)) for actor in (zen, survivor, lethal)
        )
        # Stage a target below the blast damage and move all three into the
        # radius before the next world tick can deliver any healing.
        lethal_vitals.damage(100)
        fight._sync_body_health(lethal, lethal_vitals)
        x, y, z = mine.position
        for actor, dx, dz in ((zen, 0, 1), (survivor, -1, 0), (lethal, 1, 0)):
            relocate(run, actor, (x + dx, y, z + dz))
        run.step({})

        self.assertTrue(mine.triggered)
        self.assertTrue(immunity.active)
        self.assertEqual(zen_vitals.total, 200)
        self.assertGreater(zen.mover.velocity[1], 4)
        self.assertAlmostEqual(survivor_vitals.total, 74.8)
        self.assertGreater(survivor.mover.velocity[1], 4)
        self.assertEqual(lethal_vitals.total, 0)
        self.assertTrue(fight.is_dead(lethal))
        self.assertEqual(lethal_vitals.killer, player.body)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(zen.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        for _ in range(40):
            run.step({})
        self.assertTrue(immunity.active)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(survivor_vitals.total, 200)
        self.assertEqual(lethal_vitals.total, 0)
        self.assertEqual(lethal.stats.value(stats.DEATHS), 1)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 130)

    def test_mine_triggers_across_winston_shell_without_damaging_dome(self):
        for mine_inside in (False, True):
            with self.subTest(mine_inside=mine_inside):
                self.check_mine_across_winston_shell(mine_inside)

    def check_mine_across_winston_shell(self, mine_inside):
        run = Run(KANEZAKA_DM, ('Ball', 'Target', 'Winston'))
        player, target, winston = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, target.team, winston.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(winston, heroes()[barriers.WINSTON])
        x, y, z = player.mover.position
        relocate(run, target, (x + 100, y, z))
        relocate(run, winston, (x + 110, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        mx, my, mz = mine.position
        relocate(run, winston, (mx, y, mz + (4.75 if mine_inside else 5.25)))
        run.step({winston: (0, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        self.assertIs(dome.parent, winston)
        self.assertEqual(dome.vitals.total, 700)
        self.assertEqual(math.dist(mine.position, dome.centre) < barriers.DOME_RADIUS, mine_inside)
        direction = -1 if mine_inside else 1
        relocate(run, target, (mx, my, mz + 0.5 * direction))
        target_inside = math.dist(target.mover.position, dome.centre) < barriers.DOME_RADIUS
        self.assertEqual(target_inside, not mine_inside)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals[target.body].total, 70)
        self.assertGreater(target.mover.velocity[1], 4)
        self.assertGreater(direction * target.mover.velocity[2], 4)
        self.assertEqual(dome.vitals.total, 700)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    @unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA_DIR'), 'prepared barrier mesh required')
    def test_mine_crosses_raised_reinhardt_mesh_but_spends_projected_barrier(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                self.check_mine_across_reinhardt_mesh(projected)

    def check_mine_across_reinhardt_mesh(self, projected):
        data = Path(os.environ['ORPHEUS_BARRIER_DATA_DIR'])
        run = Run(KANEZAKA_DM, ('Ball', 'Target', 'Reinhardt', 'Zarya'))
        player, target, rein, zarya = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, target.team, rein.team, zarya.team = 0, 1, 1, 1
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for actor, hero in ((player, ball.HERO), (rein, barriers.REINHARDT), (zarya, barriers.ZARYA)):
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
        x, y, z = player.mover.position
        for offset, actor in zip((100, 110, 120), (target, rein, zarya), strict=True):
            relocate(run, actor, (x + offset, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, target, (x, y, z + 4))
        relocate(run, rein, (x, y, z + 2.35))
        relocate(run, zarya, (x, y, z + 8))
        raised = {rein: (-32768, 0, 2)}
        fight = match.combat
        with patch.object(barrier_geometry, 'DATA_DIR', data):
            for _ in range(40):
                run.step(raised)
        shield, = fight.barriers.values()
        self.assertTrue(shield.mesh)
        self.assertEqual(shield.vitals.total, 1600)
        bubble = None
        if projected:
            run.step({**raised, zarya: (-32768, 0, 16)})
            for _ in range(12):
                run.step(raised)
            bubble, = barriers.projected_on(fight, target.body)
            self.assertEqual(bubble.vitals.total, 200)

        self.assertTrue(mine.armed)
        self.assertFalse(mine.triggered)
        relocate(run, target, (x, y, z + 1.6))
        recipient = self.target(run, target)
        health = fight.vitals_of(recipient).total
        shield_owner = fight.vitals_of(self.target(run, rein))
        shield_owner_health = shield_owner.total
        primary = mine.body_script.primary()
        centre = tuple(primary.evaluate(primary.graph.node(52).field('m_05BE62DD')))
        a, b = recipient.segment()
        delta = tuple((lo + hi) / 2 - start for lo, hi, start in zip(a, b, centre, strict=True))
        distance = math.hypot(*delta)
        hit = barriers.ray_hit(fight, player, centre, tuple(v / distance for v in delta), distance)
        self.assertIsNotNone(hit)
        self.assertIs(hit[1], shield)

        run.step(raised)
        self.assertTrue(mine.triggered)
        self.assertEqual(shield.vitals.total, 1600)
        self.assertEqual(shield_owner.total, shield_owner_health)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if bubble is None:
            self.assertEqual(fight.vitals_of(recipient).total, health - 130)
            self.assertGreater(target.mover.velocity[1], 4)
            self.assertGreater(target.mover.velocity[2], 4)
        else:
            self.assertEqual(bubble.vitals.total, 70)
            self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 26)
            self.assertEqual(fight.vitals_of(recipient).total, health)
            self.assertEqual(tuple(target.mover.velocity), (0, 0, 0))

    def test_zarya_bubble_consumes_mine_explosion_without_body_impulse(self):
        run, player, zarya = self.scenario()
        run.match.switch_hero(zarya, heroes()[barriers.ZARYA])
        x, y, z = player.mover.position
        relocate(run, zarya, (x + 100, y, z))
        mine = self.isolated(run, player)
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        bubble = next(iter(fight.barriers.values()))
        self.assertTrue(mine.body_script.primary().states[28].active)
        x, y, z = mine.position
        relocate(run, zarya, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(bubble.vitals.total, 70)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 26)
        self.assertEqual(fight.vitals_of(self.target(run, zarya)).total, 400)
        self.assertEqual(tuple(zarya.mover.velocity), (0, 0, 0))
        self.assertFalse(zarya.mover.airborne)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def projected_mine_scene(self, *, recipient_hero=None):
        run = Run(KANEZAKA_DM, ('Ball', 'Protected ally', 'Zarya'))
        player, ally, zarya = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, ally.team, zarya.team = 0, 1, 1
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[ball.HERO])
        match.switch_hero(zarya, heroes()[barriers.ZARYA])
        if recipient_hero is not None:
            self.assertTrue(match.switch_hero(ally, heroes()[recipient_hero]))
        x, y, z = player.mover.position
        relocate(run, ally, (x + 100, y, z))
        relocate(run, zarya, (x + 100, y, z + 5))
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, ally, (x, y, z + 6))
        relocate(run, zarya, (x, y, z + 10))
        for _ in range(40):
            run.step({})
        run.step({zarya: (-32768, 0, 16)})
        for _ in range(12):
            run.step({zarya: (-32768, 0, 0)})
        fight = match.combat
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, ally)
        self.assertEqual(bubble.vitals.total, 200)
        self.assertFalse(mine.triggered)
        self.assertTrue(mine.body_script.primary().states[28].active)
        return run, player, ally, zarya, mine, bubble

    def test_projected_zarya_bubble_consumes_armed_mine_and_credits_caster(self):
        run, player, ally, zarya, mine, bubble = self.projected_mine_scene()
        fight = run.match.combat
        x, y, z = mine.position
        relocate(run, ally, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(bubble.vitals.total, 70)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 26)
        self.assertEqual(fight.vitals_of(self.target(run, ally)).total, 200)
        self.assertEqual(fight.vitals_of(self.target(run, zarya)).total, 400)
        self.assertEqual(tuple(ally.mover.velocity), (0, 0, 0))
        self.assertFalse(ally.mover.airborne)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_projected_bubble_expiry_decides_mine_damage_without_caster_commands(self):
        for jump in (False, True):
            for before in (False, True):
                with self.subTest(jump=jump, before=before):
                    scene = self.projected_mine_scene(recipient_hero=ball.HERO)
                    self.check_bubble_expiry(scene, jump, before, full_health=600, blast=125)

    def test_personal_bubble_expiry_decides_mine_damage_without_caster_commands(self):
        for before in (False, True):
            with self.subTest(before=before):
                run, owner, zarya = self.scenario()
                self.assertTrue(run.match.switch_hero(zarya, heroes()[barriers.ZARYA]))
                x, y, z = owner.mover.position
                relocate(run, zarya, (x + 100, y, z))
                mine = self.isolated(run, owner)
                run.step({zarya: (0, 0, 8)})
                bubble, = run.match.combat.barriers.values()
                scene = run, owner, zarya, zarya, mine, bubble
                self.check_bubble_expiry(scene, True, before, full_health=400, blast=130)

    def check_bubble_expiry(self, scene, jump, before, *, full_health, blast):
        run, owner, victim, zarya, mine, bubble = scene
        match = run.match
        fight = match.combat
        source = zarya.body_script
        frozen = source.frame, source.component.now
        instance = bubble.source.instances[bubble.instance]
        duration = instance.vars[567].value()
        self.assertEqual(duration, 2)
        expires = (instance.state(bubble.state).start + round(duration * 1000)) // source.frame_ms
        endpoint = expires - int(before)
        if not jump:
            for tick in range(run.frame + 1, endpoint):
                match.update(tick * combat.TICK, tick, frames=False)
        x, y, z = mine.position
        relocate(run, victim, (x, y, z + 0.5))
        match.update(endpoint * combat.TICK, endpoint, frames=False)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(mine.triggered)
        self.assertEqual(bubble.vitals.total, 70 if before else 200)
        self.assertEqual(bubble.entity in fight.barriers, before)
        self.assertEqual(fight.vitals[victim.body].total, full_health if before else full_health - blast)
        self.assertAlmostEqual(expr.to_float(source.component.entity_var(87).value()), 26 if before else 0)
        if before:
            self.assertEqual(tuple(victim.mover.velocity), (0, 0, 0))
        else:
            self.assertGreater(victim.mover.velocity[1], 4)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_overlap_before_native_preparation_timer_does_not_damage(self):
        run, player, enemy = self.scenario()
        mines = self.deploy(run, player, ticks=20)
        mine = min(mines, key=lambda m: m.entity)
        for other in mines:
            if other is not mine:
                run.match.combat.mines.remove(other)
        x, ground, z = player.mover.position
        relocate(run, player, (x + 20, ground, z))
        relocate(run, enemy, (x, ground, z + 0.5))
        while not mine.landed:
            run.step({})
        landed = run.frame
        for _ in range(90):
            run.step({})
            self.assertFalse(mine.triggered)
            self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        while not mine.triggered and run.frame < landed + 100:
            run.step({})
        self.assertTrue(mine.triggered)
        self.assertGreaterEqual(run.frame - landed, math.ceil(1.5 / 0.016))

    def test_enemy_rifle_destroys_fifty_hp_without_hero_charge_or_elimination(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        target = (x, y + 0.23, z)
        angles = aim(run.match.combat._eye(enemy), target)
        run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 30)
        self.assertFalse(mine.triggered)
        for _ in range(20):
            run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 0)
        self.assertFalse(mine.triggered)
        for _ in range(90):
            run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertEqual(enemy.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 0)

    def test_owner_and_allied_player_cannot_trigger_or_damage_mines(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        point = mine.position
        relocate(run, player, (point[0], point[1], point[2] + 0.5))
        for _ in range(10):
            angles = aim(run.match.combat._eye(player), (point[0], point[1] + 0.23, point[2]))
            run.step({player: (*angles, 1)})
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, 'free_for_all': False},
        )
        enemy.team = player.team
        relocate(run, enemy, (point[0], point[1], point[2] - 0.5))
        for _ in range(10):
            angles = aim(run.match.combat._eye(enemy), (point[0], point[1] + 0.23, point[2]))
            run.step({enemy: (*angles, 1)})
        self.assertFalse(mine.triggered)
        self.assertEqual(mine.health, 50)

    def test_static_wall_and_outside_radius_block_trigger(self):
        for wall, distance in ((False, 3), (True, 1.2), (False, 1.2)):
            with self.subTest(wall=wall, distance=distance):
                run, player, enemy = self.scenario()
                mine = self.isolated(run, player)
                x, y, z = mine.position
                if wall:
                    old = run.match.collision_world
                    triangles = [tuple(old.data[i:i + 9]) for i in range(0, len(old.data), 13)]
                    front = z + 0.5
                    triangles += [
                        (x - 5, y, front, x + 5, y, front, x + 5, y + 5, front),
                        (x - 5, y, front, x + 5, y + 5, front, x - 5, y + 5, front),
                    ]
                    collision = World(triangles)
                    run.match.collision_world = collision
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    player.mover.use(world=collision)
                relocate(run, enemy, (x, y, z + distance))
                run.step({})
                blocked = wall or distance == 3
                self.assertEqual(mine.triggered, not blocked)
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total,
                                 200 if blocked else 70)

    def test_ball_cannon_cannot_crit_mine_or_gain_charge_from_destroying_it(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        enemy.body_script.ult_seconds = 0
        enemy.body_script.set_ult_charge(0)
        angles = aim(run.match.combat._eye(enemy), (x, y + 0.23, z))
        run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 45)
        for _ in range(40):
            run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 0)
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(enemy.stats.value(stats.ELIMINATIONS), 0)

    def test_healing_does_not_repair_mine_or_count_it_for_adaptive_shield(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        angles = aim(run.match.combat._eye(enemy), (x, y + 0.23, z))
        run.step({enemy: (*angles, 1)})
        self.assertEqual(mine.health, 30)
        run.match.combat.heal_area(player, mine.position, 5, 100)
        self.assertEqual(mine.health, 30)
        # The enemy Ball is in shield range of this deployable, but its creator
        # is far away; a mine must not count as an extra enemy hero/NPC.
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        relocate(run, enemy, (x - 3, y, z))
        for _ in range(40):
            run.step({})
        run.step({enemy: (0, 0, 16)})
        vitals = run.match.combat.vitals_of(self.target(run, enemy))
        self.assertEqual(vitals.temporary_shields, 100)

    def test_rewound_shot_before_mine_creation_cannot_hit_later_entity(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, enemy, (x - 3, y, z))
        angles = aim(run.match.combat._eye(enemy), (x, y + 0.23, z))
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        enemy.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1, extra=5000)])
        self.assertEqual(mine.health, 50)
        for _ in range(8):
            run.step({})
        for _ in range(5):
            run.step({enemy: (*angles, 1)})
            if mine.health < 50:
                break
        self.assertEqual(mine.health, 30)

    def test_authored_lifetime_ends_with_native_fade_and_destruction_on_tick_gap(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        run.frame += 1600
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertFalse(mine.triggered)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        self.assertTrue(any(item.entity == mine.entity and item.op == world.OP_DESTROY
                            for item in player.client.entities))

    def test_creator_lifetime_zero_fraction_and_double_are_values_not_keys(self):
        for scalar in (0, 0.5, 2):
            with self.subTest(scalar=scalar):
                run, player, _ = self.scenario()
                player.body_script.component.set(13032, scalar)
                mines = self.deploy(run, player, ticks=20)
                self.assertEqual(len(mines), 15)
                for mine in mines:
                    duration = mine.body_script.primary().vars[6471].value()
                    self.assertGreaterEqual(duration, 18 * scalar)
                    self.assertLessEqual(duration, 20 * scalar)
                if scalar == 0:
                    run.frame += 300
                    run.step({})
                    self.assertFalse(run.match.combat.mines.entities)

    def test_creator_infinite_flag_overrides_lifetime_and_survives_normal_deadline(self):
        run, player, _ = self.scenario()
        player.body_script.component.set(13032, 0)
        player.body_script.component.set(13033, True)
        mines = self.deploy(run, player)
        self.assertEqual(len(mines), 15)
        self.assertTrue(all(m.body_script.primary().vars[6471].value() == 9999 for m in mines))
        run.frame += 1600
        run.step({})
        self.assertEqual(set(run.match.combat.mines.entities), {m.entity for m in mines})

    def test_armed_mine_reads_live_creator_knockback_zero_half_and_default_damage(self):
        for scalar in (0, 0.5, 1):
            with self.subTest(scalar=scalar):
                run, player, enemy = self.scenario()
                mine = self.isolated(run, player)
                # Change after deployment and arming: this is a creator member,
                # not a copied initializer or a numeric ruleset identifier.
                player.body_script.component.set(16349, scalar)
                x, y, z = mine.position
                relocate(run, enemy, (x, y, z + 0.5))
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 70)
                if scalar == 0:
                    self.assertEqual(enemy.mover.velocity, (0, 0, 0))
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[1], 5 * scalar - 0.4)
                    self.assertLessEqual(enemy.mover.velocity[1], 5 * scalar)
                    self.assertGreater(enemy.mover.velocity[2], 5 * scalar - 0.4)
                    self.assertLessEqual(enemy.mover.velocity[2], 5 * scalar)

    def test_native_source_query_rejects_missing_or_replaced_creator_script(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        cfg = primary.graph.node(33).field('m_condition')['m_value']['m_configVars'][0]
        self.assertEqual(cfg['$'], 'STU_2B2E2F6D')
        source_world = primary.component.world
        player.body_script.component.set(13032, 0.5)
        enemy.body_script.component.set(13032, 2)
        self.assertEqual(primary.evaluate(cfg), expr.Entity(mine.source))
        self.assertEqual(source_world.entity_var(mine.source, 13032), 0.5)

        original = player.body_script
        try:
            # Explicit namespace absence/replacement fixtures. Normal hero
            # retirement already destroys owned mines in the bounded bridge;
            # these isolate the native query's missing-selected-entity branch.
            for replacement in (None, enemy.body_script):
                with self.subTest(replacement=replacement is not None):
                    player.body_script = replacement
                    self.assertEqual(primary.evaluate(cfg), expr.Entity(0))
                    self.assertIsNone(source_world.entity_var(mine.source, 13032))
                    self.assertIsNone(source_world.ruleset_for_entity(mine.source, 16349))
                    self.assertFalse(source_world.has_script_entity(mine.source))
        finally:
            player.body_script = original
        self.assertEqual(primary.evaluate(cfg), expr.Entity(mine.source))
        self.assertEqual(source_world.entity_var(mine.source, 13032), 0.5)

    def test_native_zero_negative_and_zero_displacement_gravity_launches(self):
        for gravity in (0, -5, 625):
            with self.subTest(gravity=gravity):
                run, player, _ = self.scenario()
                ball.instance(player, 0x1517).write((expr.INSTANCE, 7389), gravity)
                mines = self.deploy(run, player, ticks=20)
                self.assertEqual(len(mines), 15)
                first = min(mines, key=lambda m: m.entity)
                if gravity == 0:
                    self.assertEqual(first.velocity, (0, 10, 0))
                    self.assertFalse(first.landed)
                elif gravity < 0:
                    self.assertGreater(first.velocity[1], 10)
                    self.assertFalse(first.landed)
                else:
                    # 625*float32(.016) rounds to10. The central mine has a
                    # zero-displacement first step, then falls on later ticks.
                    # That step must not normalize a zero collision direction.
                    self.assertTrue(first.landed)
                self.assertTrue(all(math.isfinite(p) for m in mines for p in m.position))

    def test_actual_zero_gravity_cast_clips_at_authored_500_range_without_arming(self):
        run, player, enemy, mine = self.flying()
        run.frame += 4000
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertEqual(mine.travelled, 500)
        self.assertEqual(mine.velocity, (0, 0, 0))
        self.assertFalse(mine.landed or mine.triggered)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        self.assertEqual(sum(item.entity == mine.entity and item.op == world.OP_DESTROY
                             for item in player.client.entities), 1)

    def test_budget_clips_sweep_before_wall_outside_remaining_range(self):
        run, _, _, mine = self.flying()
        # Explicit physical-state fixture after an actual cast. Unclipped .16m
        # movement would hit the wall at .12m, beyond the .1m remaining budget.
        mine.position, mine.velocity = (0, 10, 0), (0, 0, 10)
        mine.reach, mine.travelled = expr.f32(.1), 0
        collision = World([
            (-5, 0, .12, 5, 0, .12, 5, 20, .12),
            (-5, 0, .12, 5, 20, .12, -5, 20, .12),
        ])
        run.match.collision_world = collision
        run.match.collision_provider = StaticCollisionProvider(collision)
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertFalse(mine.landed or mine.triggered)
        self.assertAlmostEqual(mine.position[2], .1, places=6)
        # Captured original D72C80 for this .1/.16 boundary rounds one ULP
        # below the configured reach; it does not force the counter to reach.
        self.assertEqual(mine.travelled, .09999999403953552)

    def test_flight_time_uses_world_birth_not_future_command_report_and_strict_boundary(self):
        run, _, _, mine = self.flying()
        # Shortened lifetime is an explicit engine-state fixture; authored Q
        # supplies999, while this isolates the native terminal time branch.
        mine.born_tick = run.frame
        mine.reported_frame += 1000000
        mine.flight_lifetime = expr.f32(2 * ball_mines.TICK)
        mine.position, mine.velocity = (0, 10, 0), (0, 0, 0)
        mine.travelled = 0
        for _ in range(2):
            run.step({})
            self.assertIn(mine.entity, run.match.combat.mines.entities)
        run.step({})
        self.assertNotIn(mine.entity, run.match.combat.mines.entities)
        self.assertEqual(mine.travelled, 0)
        self.assertFalse(mine.landed or mine.triggered)

    def test_landed_bridge_uses_graph_lifetime_after_flight_stops(self):
        run, player, _ = self.scenario()
        mine = self.isolated(run, player)
        mine.flight_lifetime = ball_mines.FLIGHT_EPSILON
        run.step({})
        self.assertIn(mine.entity, run.match.combat.mines.entities)
        self.assertTrue(mine.landed)
        self.assertTrue(mine.body_script.primary().states[28].active)

    def test_second_actual_cast_replaces_old_pet_limit_and_body_retirement_cleans_up(self):
        run, player, _ = self.scenario()
        old = {m.entity for m in self.deploy(run, player)}
        current = {m.entity for m in self.deploy(run, player)}
        self.assertEqual(len(current), 15)
        self.assertFalse(old & current)
        run.match.switch_hero(player, player.hero)
        self.assertFalse(run.match.combat.mines.entities)
        self.assertFalse(run.match.combat.mines.seen)

    def test_switch_during_partial_minefield_retires_only_the_old_owner_mines(self):
        run, player, enemy = self.scenario()
        run.match.switch_hero(enemy, heroes()[ball.HERO])
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 100, y, z))
        for _ in range(40):
            run.step({})
        self.deploy(run, enemy)
        fight = run.match.combat
        enemy_mines = set(fight.mines.entities)
        enemy_reports = set(fight.mines.seen)
        self.assertEqual(len(enemy_mines), 15)
        self.assertEqual(len(enemy_reports), 3)

        source = player.body
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32)})
        for _ in range(10):
            if len(fight.mines.entities) > len(enemy_mines):
                break
            run.step({})
        mine_id, = set(fight.mines.entities) - enemy_mines
        self.assertEqual(fight.mines.entities[mine_id].source, source)
        self.assertEqual(len(fight.mines.seen - enemy_reports), 1)
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        self.assertNotEqual(player.body, source)
        for _ in range(40):
            run.step({})
        self.assertEqual(set(fight.mines.entities), enemy_mines)
        self.assertEqual(fight.mines.seen, enemy_reports)
        for viewer in (player, enemy):
            ops = [record.op for record in viewer.client.entities if record.entity == mine_id]
            self.assertEqual(ops.count(world.OP_CREATE), 1)
            self.assertEqual(ops.count(world.OP_DESTROY), 1)
            self.assertFalse(any(record.op == world.OP_DESTROY and record.entity in enemy_mines
                                 for record in viewer.client.entities))

        self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
        for _ in range(40):
            run.step({})
        self.deploy(run, player, ticks=20)
        fresh = set(fight.mines.entities) - enemy_mines
        self.assertEqual(len(fresh), 15)
        self.assertNotIn(mine_id, fresh)
        self.assertTrue(all(fight.mines.entities[entity].source == player.body for entity in fresh))
        self.assertEqual(len(fight.mines.seen - enemy_reports), 3)

    def test_live_mine_script_and_health_are_causal_snapshot_state(self):
        run, player, _ = self.scenario()
        mine = self.isolated(run, player)

        def digest():
            stored = server_snapshot._Graph({
                'test-owner': player,
                **{f'client-{p.slot}': p.client for p in run.players},
            }, None)
            root = stored.value(mine)
            return server_snapshot.snapshot_digest({
                'schema': server_snapshot.SCHEMA, 'scope': server_snapshot.SCOPE,
                'restorable': False, 'roots': {'mine': root}, 'nodes': stored.finish(), 'assets': [],
            })

        before = digest()
        mine.health = 49
        mine.body_script.set_health(49, 50)
        self.assertNotEqual(digest(), before)
        before = digest()
        mine.born_tick += 1
        self.assertNotEqual(digest(), before)
        before = digest()
        mine.flight_lifetime = 998
        self.assertNotEqual(digest(), before)

    def test_real_client_tracks_mine_creates_and_full_server_snapshot_contains_them(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, peers, players = case.scene()
        player = players[0]
        self.assertTrue(session.apply('switch_hero', {'account': player.account_lo, 'hero': ball.HERO}))
        for tick in range(8):
            now = 100.96 + tick * 0.016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        script = player.body_script
        peers[0].commands(script.frame + 1, 40, 0, now=101.1)
        # This is a direct snapshot/transport test, not a sealed journal replay:
        # charge is supplied explicitly, outside the recording root interface.
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        peers[0].commands(script.frame + 1, 30, 32, now=101.1)
        mines = player.match.combat.mines.entities
        self.assertEqual(len(mines), 15)
        self.assertTrue(set(mines) <= set(player.client._bot_scripts))
        self.assertTrue(all(m.last_tick == player.match.tick for m in mines.values()))
        self.assertTrue(any(m.reported_frame > m.last_tick for m in mines.values()))
        session.tick(105.0, missed=249)
        peers[0].drain()
        peers[0].send(now=105.0)
        self.assertTrue(all(m.landed for m in mines.values()))
        self.assertTrue(any(m.body_script.primary().states.get(28)
                            and m.body_script.primary().states[28].active for m in mines.values()))
        before = server_snapshot.snapshot_digest(case.capture(session))
        mine = next(iter(mines.values()))
        mine.health = 49
        mine.body_script.set_health(49, 50)
        self.assertNotEqual(before, server_snapshot.snapshot_digest(case.capture(session)))

    def test_replacement_client_receives_existing_mines_once(self):
        run, player, enemy = self.scenario()
        mine = self.isolated(run, player)
        enemy.client = FakeClient(enemy)
        run.step({})
        creates = [item for item in enemy.client.entities
                   if item.entity == mine.entity and item.op == world.OP_CREATE]
        self.assertEqual(len(creates), 1)
        run.step({})
        self.assertEqual(sum(item.entity == mine.entity and item.op == world.OP_CREATE
                             for item in enemy.client.entities), 1)


if __name__ == '__main__':
    unittest.main()
