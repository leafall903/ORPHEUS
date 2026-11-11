"""104319 Barrier Projector interactions with actual Mei and Winston input."""

import math
import unittest
from dataclasses import replace
from unittest import mock

import test_mei_combat as fixtures
import test_server_snapshot as snapshot_fixtures
from test_game_combat import in_world
from test_server_journal import pick

from ow174.game import combat, winston_barrier
from ow174.game.content import heroes
from ow174.game.script import expr, runtime
from ow174.game.server_snapshot import snapshot_digest


class MeiWinstonBarrierTests(unittest.TestCase):
    def scene(self):
        fixture = fixtures.MeiCombatTests()
        run, mei, winston, _ = fixture.duel()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        x, y, z = mei.mover.position
        fight.walls.place_player(winston, (x, y, z + 8.))
        for _ in range(25):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is winston)
        instance = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFA)
        run.step({winston: (-32768, 0, 16)})
        self.assertFalse(fight.winston_barriers.actors)
        for _ in range(8):
            if fight.winston_barriers.actors:
                break
            self.idle(run)
        dome, = fight.winston_barriers.actors.values()
        self.assertEqual(dome.health, 700.)
        self.assertEqual(instance.find_var(expr.INSTANCE, 38).value(), expr.Entity(dome.body))
        self.assertEqual(instance.states[2].cur, 13.)
        return fixture, run, mei, winston, target, dome, instance

    @staticmethod
    def idle(run):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)

    def test_icicle_and_spray_contact_shell_then_breaking_icicle_stops(self):
        fixture, run, mei, winston, target, dome, instance = self.scene()
        fight = run.match.combat
        pool = fight.vitals_of(target)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(dome.health, 625.)
        fixture.fire(run, mei, target, 1, .9, frames=1)
        for _ in range(30):
            run.step({})
        self.assertEqual(dome.health, 622.25)
        self.assertEqual(pool.total, 500.)
        self.assertIsNone(fight.frost(winston))
        self.assertFalse(mei.client.messages(combat.HIT))

        fight.winston_barriers.damage(dome, dome.health - 5.)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(dome.health, 0.)
        self.assertFalse(fight.winston_barriers.actors)
        self.assertEqual(instance.find_var(expr.INSTANCE, 38).value(), expr.Entity(0))
        self.assertEqual(pool.total, 500.)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(pool.total, 430.)
        self.assertEqual(len(mei.client.messages(combat.HIT)), 1)

    def test_static_contact_wins_and_melee_can_touch_a_clear_shell(self):
        fixture, run, mei, _winston, target, dome, _instance = self.scene()
        fight = run.match.combat
        fixtures.static_wall(run, mei, distance=1.)
        fixture.fire(run, mei, target, 2, .9)
        fixture.fire(run, mei, target, 0x800, .9, frames=25)
        self.assertEqual(dome.health, 700.)
        x, y, z = mei.mover.position
        fight.walls.place_player(mei, (x, y, z + 1.7))
        for _ in range(25):
            run.step({})
        fixture.fire(run, mei, target, 0x800, .9, frames=25)
        self.assertEqual(dome.health, 670.)
        self.assertEqual(fight.vitals_of(target).total, 500.)

    def test_icicle_reaches_enemy_inside_same_dome_or_through_friendly_shell(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                fixture, run, mei, winston, target, dome, _instance = self.scene()
                fight = run.match.combat
                x, y, z = mei.mover.position
                if allied:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mei.team = winston.team
                    enemy = run.match.add_player(17, "Enemy", winston.hero.guid, 1 - mei.team, False)
                    in_world(run.match, enemy)
                    run.players.append(enemy)
                    self.assertTrue(run.match.switch_hero(enemy, winston.hero))
                    fight.walls.place_player(enemy, (x, y, z + 10.))
                    target = next(t for t in fight._targets() if t.owner is enemy)
                else:
                    fight.walls.place_player(mei, (x, y, z + 4.))
                for _ in range(10):
                    run.step({})
                fixture.fire(run, mei, target, 2, .9)
                self.assertEqual(dome.health, 700.)
                self.assertEqual(fight.vitals_of(target).total, 430.)

    def test_idle_expiry_clears_slot_without_shortening_cooldown(self):
        _fixture, run, _mei, winston, _target, dome, instance = self.scene()
        fight = run.match.combat
        start = dome.component.frame
        for _ in range(580):
            if not fight.winston_barriers.actors:
                break
            self.idle(run)
        self.assertFalse(fight.winston_barriers.actors)
        self.assertIn(run.match.tick - start, range(561, 565))
        self.assertEqual(instance.find_var(expr.INSTANCE, 38).value(), expr.Entity(0))
        self.assertGreater(instance.states[2].remaining(), 3.9)
        self.assertLess(instance.states[2].remaining(), 4.1)
        run.step({winston: (0, 0, 16)})
        for _ in range(8):
            run.step({})
        self.assertFalse(fight.winston_barriers.actors)

    def test_blizzard_can_cross_the_dome_in_flight_or_expose_from_outside(self):
        for lands_inside, ground_range in ((False, 2.), (True, 8.)):
            with self.subTest(lands_inside=lands_inside):
                _fixture, run, mei, winston, target, dome, _instance = self.scene()
                fight = run.match.combat
                pool = fight.vitals_of(target)
                x, y, z = mei.mover.position
                angle = fixtures.aim(fight._eye(mei), (x, y, z + ground_range))
                mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())

                was_inside_in_flight = False
                for frame in range(240):
                    run.step({mei: (*angle, 32 if frame == 0 else 0)})
                    for flight in fight.blizzard.flights:
                        self.assertEqual(pool.total, 500.)
                        distance = math.dist(flight.position, dome.centre)
                        was_inside_in_flight |= distance < winston_barrier.RADIUS
                    if fight.frozen(winston):
                        break

                area, = fight.blizzard.areas.values()
                centre = (area.position[0], area.position[1] + .5, area.position[2])
                destination = fight._eye(winston)
                reach = math.dist(centre, destination)
                direction = tuple((b - a) / reach for a, b in zip(centre, destination, strict=True))
                crossing = dome.ray(centre, direction)
                self.assertEqual(was_inside_in_flight, lands_inside)
                self.assertEqual(math.dist(centre, dome.centre) < winston_barrier.RADIUS, lands_inside)
                self.assertIsNotNone(crossing)
                self.assertEqual(crossing < reach, not lands_inside)
                self.assertTrue(fight.frozen(winston))
                self.assertLess(pool.total, 500.)
                self.assertIs(fight.winston_barriers.actors[dome.body], dome)
                self.assertEqual(dome.health, 700.)

    def test_actual_dome_pool_and_clock_are_captured_without_evaluation(self):
        fixture = snapshot_fixtures.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner = players[1]
        peers[1].message(21616, pick(owner.entity, winston_barrier.HERO), now=100.8)
        self.assertEqual(owner.hero.guid, winston_barrier.HERO)
        session.tick(100.960)
        frame = owner.body_script.frame + 1
        peers[1].commands(frame, 1, 16, now=100.960)
        self.assertEqual(owner.body_script.frame, frame)
        for at in range(1, 12):
            if owner.match.combat.winston_barriers.actors:
                break
            session.tick(100.960 + at * .016)
        dome, = owner.match.combat.winston_barriers.actors.values()
        self.assertEqual(dome.health, 700.)
        with (
            mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM evaluated")),
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("variable evaluated")),
        ):
            before = snapshot_digest(fixture.capture(session))
            dome.health -= 20.
            changed = snapshot_digest(fixture.capture(session))
            self.assertNotEqual(changed, before)
            dome.health += 20.
            dome.component.frame += 1
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
            dome.component.frame -= 1
            self.assertEqual(snapshot_digest(fixture.capture(session)), before)
