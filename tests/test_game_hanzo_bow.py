"""Actual bow draw/cancel/damage and distance/lifetime boundaries."""

import math
import unittest
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import aim

from ow174.game import bodytrace, combat, movelog
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.server_snapshot import snapshot_digest


class HanzoBowTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        return hero_tests.HanzoCombatTests().arena()

    @staticmethod
    def body_aim(fight, hero, victim):
        x, y, z = victim.mover.position
        return aim(fight._eye(hero), (x, y + 1, z))

    def test_charge_curve_changes_speed_and_actual_body_damage_and_caps(self):
        previous = 0
        # Source00B9: charge rate1.375, damage25..125, speed25+85*charge.
        # The command runs through frame end, leaving16*N-1ms before release.
        # Bound the small native float32/ChaseVar epsilon accumulation separately.
        for held in (1, 5, 20, 40, 50, 90):
            with self.subTest(held=held):
                run, hero, victim = self.arena()
                fight = run.match.combat
                yaw, pitch = self.body_aim(fight, hero, victim)
                for _ in range(held):
                    run.step({hero: (yaw, pitch, 1)})
                self.assertFalse(fight.rockets)
                run.step({hero: (yaw, pitch, 0)})
                arrow, = fight.rockets
                amount, speed = arrow.direct.amount, arrow.speed
                charge = min(1, 1.375 * (held * 16 - 1) / 1000)
                self.assertAlmostEqual(amount, 25 + 100 * charge, delta=0.12)
                self.assertAlmostEqual(speed, 25 + 85 * charge, delta=0.11)
                self.assertGreaterEqual(amount, previous)
                previous = amount
                for _ in range(35):
                    run.step({})
                self.assertAlmostEqual(fight.vitals[victim.body].total, 200 - amount)
                self.assertEqual(len(hero.client.messages(combat.HIT)), 1)

    def test_secondary_and_reload_cancel_draw_without_damage_and_can_draw_again(self):
        for button in (2, 0x400):
            with self.subTest(button=button):
                run, hero, victim = self.arena()
                fight = run.match.combat
                yaw, pitch = self.body_aim(fight, hero, victim)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
                    for _ in range(20):
                        run.step({hero: (yaw, pitch, 1)})
                    for _ in range(15):
                        run.step({hero: (yaw, pitch, 1 | button)})
                    run.step({})
                    for _ in range(20):
                        run.step({})
                    self.assertFalse(fire.called)
                    target = next(t for t in fight._targets() if t.owner is victim)
                    self.assertEqual(fight.vitals_of(target).total, 200)
                    for _ in range(50):
                        run.step({hero: (yaw, pitch, 1)})
                    run.step({hero: (yaw, pitch, 0)})
                    for _ in range(10):
                        run.step({})
                self.assertEqual(len(fire.call_args_list), 1)
                self.assertEqual(fight.vitals[victim.body].total, 75)

    def test_cancel_is_a_draw_edge_and_does_not_gate_the_next_primary_press(self):
        primary = 1
        for cancel in (2, 0x400):
            both = primary | cancel
            cases = (
                ("cancel held before draw", ((10, cancel), (70, both), (1, cancel)), 1),
                ("simultaneous start", ((70, both), (1, cancel)), 1),
                ("cancel released while primary stays held", ((20, primary), (8, both), (70, primary)), 0),
                ("fresh primary while cancel stays held", ((20, primary), (8, both), (5, cancel),
                                                           (70, both), (1, cancel)), 1),
                ("cancel and release in the same command", ((70, primary), (1, cancel)), 1),
            )
            for case, commands, expected in cases:
                with self.subTest(cancel=cancel, case=case):
                    run, hero, victim = self.arena()
                    fight = run.match.combat
                    yaw, pitch = self.body_aim(fight, hero, victim)
                    with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                        for count, buttons in commands:
                            for _ in range(count):
                                run.step({hero: (yaw, pitch, buttons)})
                        for _ in range(15):
                            run.step({hero: (yaw, pitch, 0)})
                    self.assertEqual(fired.call_count, expected)
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [125] * expected)
                    target = next(t for t in fight._targets() if t.owner is victim)
                    self.assertEqual(fight.vitals_of(target).total, 200 - 125 * expected)

    def test_duplicate_release_command_deals_damage_once(self):
        run, hero, victim = self.arena()
        fight = run.match.combat
        yaw, pitch = self.body_aim(fight, hero, victim)
        for _ in range(50):
            run.step({hero: (yaw, pitch, 1)})
        run.step({hero: (yaw, pitch, 0)})
        command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=0)
        hero.take_commands([command, command, command])
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals[victim.body].total, 75)
        self.assertEqual(len(hero.client.messages(combat.HIT)), 1)

    def empty_flight(self, pitch=0, *, held=70):
        run, hero, victim = self.arena()
        for _ in range(held):
            run.step({hero: (0, pitch, 1)})
        run.step({hero: (0, pitch, 0)})
        fight = run.match.combat
        arrow, = fight.rockets
        victim.spawned = False
        fight.match.collision_world = World([], name="declared empty projectile range fixture")
        return run, fight, arrow

    @staticmethod
    def finish(run, fight, arrow, *, skipped=False):
        distance = 0.0
        position = arrow.position
        # First admission remains one tick; a subsequent skipped update catches up.
        for offset in ([1, 2000] if skipped else range(1, 2001)):
            tick = run.frame + offset
            fight.match.tick = tick
            fight.update(tick * combat.TICK, tick, frames=False)
            distance += math.dist(position, arrow.position)
            position = arrow.position
            if arrow not in fight.rockets:
                break
        return distance

    def test_gravity_does_not_extend_authored1000m_path_budget(self):
        run, fight, arrow = self.empty_flight()
        distance = self.finish(run, fight, arrow)
        self.assertFalse(fight.rockets)
        self.assertLessEqual(distance, 1000.000001)
        self.assertAlmostEqual(distance, 1000, places=6)
        self.assertAlmostEqual(arrow.distance_flown, 1000, places=6)
        self.assertLess(arrow.flown, 20)

    def test_vertical_arrow_obeys20s_lifetime_when_distance_budget_is_not_exhausted(self):
        run, fight, arrow = self.empty_flight(pitch=-16384)
        distance = self.finish(run, fight, arrow)
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(arrow.flown, 20)
        self.assertLess(distance, 1000)
        self.assertGreater(distance, 990)

    def test_range_endpoint_agrees_under_contiguous_and_skipped_ticks(self):
        results = []
        for skipped in (False, True):
            run, fight, arrow = self.empty_flight()
            self.finish(run, fight, arrow, skipped=skipped)
            self.assertFalse(fight.rockets)
            results.append((arrow.position, arrow.flown, arrow.distance_flown))
        self.assertAlmostEqual(results[0][1], results[1][1])
        self.assertAlmostEqual(results[0][2], results[1][2])
        for a, b in zip(results[0][0], results[1][0], strict=True):
            self.assertAlmostEqual(a, b)

    def test_full_draw_can_hit_before_path_limit_but_not_beyond_it(self):
        baseline, fight, arrow = self.empty_flight()
        self.finish(baseline, fight, arrow)
        endpoint = arrow.position
        tangent = (0, -arrow.gravity * arrow.flown, arrow.speed)
        speed = math.hypot(*tangent)
        tangent = tuple(component / speed for component in tangent)

        for offset, dealt in ((-2, 250), (2, 0)):
            for skipped in (False, True):
                with self.subTest(offset=offset, skipped=skipped):
                    run, fight, arrow = self.empty_flight()
                    hero, victim = run.players
                    x, y, z = (point + offset * direction
                               for point, direction in zip(endpoint, tangent, strict=True))
                    victim.mover.position[:] = (x, y - 1, z)
                    victim.mover.body = None
                    victim.spawned = True
                    pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 500)
                    self.finish(run, fight, arrow, skipped=skipped)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(pool.total, 500 - dealt)
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [dealt] if dealt else [])
                    self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))

    def test_vertical_arrow_hits_before_lifetime_limit_but_not_after_it(self):
        for launch, amount in ((self.empty_flight, 125), (self.empty_storm_flight, 70)):
            with self.subTest(launch=launch.__name__):
                self.verify_descending_lifetime_contact(launch, amount)

    def verify_descending_lifetime_contact(self, launch, amount):
        baseline, fight, arrow = launch(pitch=-16384)
        self.finish(baseline, fight, arrow, skipped=True)
        self.assertAlmostEqual(arrow.flown, 20)
        endpoint = arrow.position

        for y_offset, dealt in ((2, 2 * amount), (-2, 0)):
            with self.subTest(y_offset=y_offset):
                run, fight, arrow = launch(pitch=-16384)
                hero, victim = run.players
                # Admit the target on the descending leg only. A vertical arrow
                # traverses these heights once on ascent as well.
                for offset in (1, 1188):
                    tick = run.frame + offset
                    fight.match.tick = tick
                    fight.update(tick * combat.TICK, tick, frames=False)
                run.frame = tick
                self.assertGreater(arrow.flown, 19)
                self.assertLess(arrow.flown, 20)
                self.assertTrue(arrow in fight.rockets)
                x, y, z = endpoint
                victim.mover.position[:] = (x, y + y_offset - 1, z)
                victim.mover.body = None
                victim.spawned = True
                pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 500)
                charge = hero.body_script.component.entity_var(combat.ULT_CHARGE)
                before = charge.value()
                self.finish(run, fight, arrow)
                self.assertFalse(fight.rockets)
                self.assertLess(arrow.distance_flown, 1000)
                self.assertEqual(pool.total, 500 - dealt)
                self.assertEqual(charge.value(), before + dealt)
                hits = hero.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits],
                                 [dealt] if dealt else [])
                self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))

    def empty_storm_flight(self, pitch=0):
        run, hero, victim = self.arena()
        fight = run.match.combat
        victim.spawned = False
        fight.match.collision_world = World([], name="empty Storm Arrow expiry fixture")
        run.step({hero: (0, pitch, 16)})
        run.step({})
        for _ in range(40):
            run.step({hero: (0, pitch, 1)})
            if fight.rockets:
                break
        arrow, = fight.rockets
        return run, fight, arrow

    def test_actual_storm_arrow_range_and_lifetime_terminate_independently(self):
        for pitch in (0, -16384):
            with self.subTest(pitch=pitch):
                run, fight, arrow = self.empty_storm_flight(pitch)
                hero = run.players[0]
                self.assertEqual(arrow.shot.definition, 0x040000000000270F)
                self.assertEqual(arrow.direct.amount, 70)
                self.assertAlmostEqual(arrow.speed, 110)
                self.assertAlmostEqual(arrow.gravity, 9.8, places=5)
                self.assertEqual(arrow.left, 20)
                self.assertEqual(arrow.range_left, 1000)
                self.finish(run, fight, arrow, skipped=True)
                self.assertFalse(fight.rockets)
                self.assertFalse(hero.client.messages(combat.HIT))
                if pitch:
                    self.assertAlmostEqual(arrow.flown, 20)
                    self.assertLess(arrow.distance_flown, 1000)
                else:
                    self.assertLess(arrow.flown, 20)
                    self.assertAlmostEqual(arrow.distance_flown, 1000)

    def test_storm_arrow_hits_before_path_limit_but_not_beyond_it(self):
        baseline, fight, arrow = self.empty_storm_flight()
        launch_speed = arrow.speed
        self.finish(baseline, fight, arrow, skipped=True)
        endpoint = arrow.position
        tangent = (0, -arrow.gravity * arrow.flown, launch_speed)
        speed = math.hypot(*tangent)
        tangent = tuple(component / speed for component in tangent)

        for offset, dealt in ((-2, 140), (2, 0)):
            with self.subTest(offset=offset):
                run, fight, arrow = self.empty_storm_flight()
                hero, victim = run.players
                x, y, z = (point + offset * direction
                           for point, direction in zip(endpoint, tangent, strict=True))
                victim.mover.position[:] = (x, y - 1, z)
                victim.mover.body = None
                victim.spawned = True
                pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 500)
                charge = hero.body_script.component.entity_var(combat.ULT_CHARGE)
                before = charge.value()
                self.finish(run, fight, arrow, skipped=True)
                self.assertFalse(fight.rockets)
                self.assertEqual(pool.total, 500 - dealt)
                self.assertEqual(charge.value(), before + dealt)
                hits = hero.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [dealt] if dealt else [])
                self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))

    def test_stored_state_distinguishes_flight_ledger_and_ricochet_budget(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        fight = player.match.combat
        arrow = combat.Rocket(player, player.team, fight._eye(player), (0, 0, 1),
                              110, 20, None, None, gravity=9.8, range_left=1000)
        fight.rockets.append(arrow)
        initial = snapshot_digest(case.capture(session))
        arrow.range_left = 999
        remaining = snapshot_digest(case.capture(session))
        self.assertNotEqual(initial, remaining)
        arrow.distance_flown = 1
        travelled = snapshot_digest(case.capture(session))
        self.assertNotEqual(remaining, travelled)
        arrow.storm_ricochets = 1
        self.assertNotEqual(travelled, snapshot_digest(case.capture(session)))
