"""Meteor Strike against Widowmaker through accepted body commands."""

import unittest

from test_game_mover import box
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import combat, doomfist_meteor, server_snapshot
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class _MeteorMatch:
    def setUp(self):
        super().setUp()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.b, heroes()[doomfist_meteor.HERO]))
        self.place(5)

    def place(self, distance):
        self.a.mover.position[:] = (0, 0, distance)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None

    @property
    def charge(self):
        return self.b.body_script.component.entity_var(342).value()

    def ready(self):
        start = self.run.frame
        while self.charge < 1680:
            self.assertLess(self.run.frame - start, 3800)
            first = self.run.frame + 1
            self.run.frame += 32
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            frames = range(first, self.run.frame + 1)
            for player in self.run.players:
                player.take_commands([Command(frame) for frame in frames])
        self.assertEqual(self.charge, 1680)

    def settle(self, frames):
        for _ in range(frames):
            self.run.step({})

    def select(self):
        self.run.step({self.b: (0, 0, 32)})
        self.settle(70)
        cast = self.fight.meteors.casts[self.b.body]
        self.assertTrue(self.fight.meteors.protected(self.b))
        self.assertFalse(cast.impacted)
        return cast


class WidowmakerMeteorTests(_MeteorMatch, _WidowmakerMatch, unittest.TestCase):
    def test_charge_gate_falloff_knockback_cost_end_and_rifle_rejection(self):
        self.run.step({self.b: (0, 0, 32)})
        self.settle(1)
        self.assertNotIn(self.b.body, self.fight.meteors.casts)
        self.ready()
        cast = self.select()
        self.press(FIRE, frames=10)
        self.assertEqual(self.health(self.b), 250)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        weapon = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0xD0A)
        ammo = weapon.vars[53].value()
        self.run.step({self.b: (0, 0, FIRE)})
        for _ in range(90):
            if cast.impacted:
                break
            self.settle(1)
        self.assertTrue(cast.impacted)
        self.assertAlmostEqual(self.health(self.a), 67.5)
        self.assertTrue(self.a.mover.external_impulse)
        vitals = self.fight.vitals_of(self.target(self.b))
        self.assertAlmostEqual(sum(pool.now for pool in vitals.temporary_shields.values()), 75)
        self.settle(65)
        self.assertTrue(cast.finished)
        self.assertFalse(self.fight.meteors.protected(self.b))
        self.assertLess(self.charge, 60)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(len(self.b.client.messages(combat.HIT)), 1)
        before = self.health(self.b)
        self.press(FIRE)
        self.assertLess(self.health(self.b), before)
        self.run.step({self.b: (0, 0, 32)})
        self.settle(5)
        self.assertIs(self.fight.meteors.casts[self.b.body], cast)

    def test_wall_blocks_timeout_impact_and_still_costs_charge(self):
        self.ready()
        world = World(box(-20, 20, -1, 0, -20, 20) + box(-8, 8, 0, 6, 1, 2))
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        cast = self.select()
        for _ in range(400):
            if cast.finished:
                break
            self.settle(1)
        self.assertTrue(cast.finished)
        self.assertTrue(cast.impacted)
        self.assertEqual(cast.hit, ())
        self.assertEqual(self.health(self.a), 175)
        self.assertLess(self.charge, 60)
        self.assertEqual(self.health(self.b), 250)

    def test_world_clock_resolves_each_preimpact_phase_without_caster_poses(self):
        for phase in ("startup", "selection", "descent"):
            with self.subTest(phase=phase):
                self.setUp()
                self.ready()
                if phase == "startup":
                    self.run.step({self.b: (0, 0, 32)})
                    cast = self.fight.meteors.casts[self.b.body]
                else:
                    cast = self.select()
                    if phase == "descent":
                        self.run.step({self.b: (0, 0, FIRE)})
                        self.assertTrue(cast.committed)
                body_frame = self.b.body_script.frame
                self.assertIsNotNone(cast.impact_at)
                last_before_impact = cast.impact_at
                expected = {"startup": 6, "selection": 4.88, "descent": 1}
                self.assertAlmostEqual(last_before_impact - self.fight.now, expected[phase], delta=.08)
                while (self.run.frame + 1) * combat.TICK < last_before_impact:
                    self.run.frame += 1
                    tick = self.run.frame
                    self.match.update(1000 + tick * combat.TICK, tick)
                    self.a.take_commands([Command(tick)])
                self.assertTrue(self.fight.meteors.protected(self.b))
                self.assertEqual(self.health(self.a), 175)
                for _ in range(40):
                    self.run.frame += 1
                    tick = self.run.frame
                    self.match.update(1000 + tick * combat.TICK, tick)
                    self.a.take_commands([Command(tick)])
                self.assertEqual(self.b.body_script.frame, body_frame)
                self.assertTrue(cast.impacted)
                self.assertFalse(self.fight.meteors.protected(self.b))
                self.assertAlmostEqual(self.health(self.a), 67.5)
                for _ in range(15):
                    self.run.frame += 1
                    tick = self.run.frame
                    self.match.update(1000 + tick * combat.TICK, tick)
                    for player in self.run.players:
                        player.take_commands([Command(tick)])
                self.assertTrue(cast.finished)
                self.assertLess(self.charge, 60)
                self.assertEqual(len(self.b.client.messages(combat.HIT)), 1)
                self.assertAlmostEqual(self.health(self.a), 67.5)

    def test_selection_state_serializes_and_retires_on_body_replacement(self):
        self.ready()
        cast = self.select()
        graph = server_snapshot._Graph({'combat': self.fight}, server_snapshot.WorldRegistry())
        graph.value(self.fight.meteors)
        graph.finish()
        old = self.b.body
        self.assertTrue(self.match.switch_hero(self.b, self.a.hero))
        self.settle(400)
        self.assertNotIn(old, self.fight.meteors.casts)
        self.assertFalse(cast.impacted)
        self.assertEqual(self.health(self.a), 175)



class WidowmakerMeteorBarrierTests(_MeteorMatch, _ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_absorbs_meteor_without_knockback_or_shield_grant(self):
        self.c.mover.position[:] = (-9, 0, 5)
        self.c.mover.body = None
        self.ready()
        cast = self.select()
        self.activate()
        self.run.step({self.b: (0, 0, FIRE)})
        for _ in range(90):
            if cast.impacted:
                break
            self.settle(1)
        self.assertTrue(cast.impacted)
        self.assertEqual(cast.hit, ())
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.a.mover.external_impulse)
        self.assertFalse(self.fight.vitals_of(self.target(self.b)).temporary_shields)
        self.assertAlmostEqual(self.fight.zarya_barriers.projected[self.c.body].remaining, 92.5)
