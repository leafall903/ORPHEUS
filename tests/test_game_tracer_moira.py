"""Actual Moira F76 interactions with Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import moira, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import expr


class MoiraArena(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.moira, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.moira, heroes()[moira.HERO]))
        x, y, z = self.moira.mover.position
        self.place(self.tracer, (x, y, z + 3))
        for _ in range(24):
            self.run.step({})
        self.health = self.vitals(self.tracer)
        self.moira_health = self.vitals(self.moira)
        self.weapon = next(i for i in self.moira.body_script.component.instances.values()
                           if i.graph.index == moira.GRASP)
        self.search = self.weapon.state(moira.DAMAGE_SEARCH)

    @staticmethod
    def place(actor, position):
        actor.mover.position[:] = position
        actor.mover.body = None

    def vitals(self, actor):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is actor))

    def cast(self, *, yaw=0, tracer_buttons=0, buttons=2):
        self.run.step({self.moira: (yaw, 0, buttons), self.tracer: (0, 0, tracer_buttons)})

    def fuel(self):
        return self.weapon.vars[87].value()


class TracerMoiraTests(MoiraArena):
    def test_actual_damage_publishes_target_heals_moira_and_refills_fuel(self):
        self.moira_health.damage(80)
        self.weapon.write((expr.INSTANCE, 87), 10.0)
        for _ in range(20):
            self.cast()
        self.assertTrue(self.search.active)
        self.assertEqual(self.search.targets, (self.tracer.body,))
        self.assertEqual(self.weapon.vars[375].value(), (expr.Entity(self.tracer.body),))
        self.assertAlmostEqual(self.health.total, 134, places=4)
        self.assertAlmostEqual(self.moira_health.total, 127.68, places=4)
        self.assertGreaterEqual(self.fuel(), 70)
        before = self.health.total, self.moira_health.total
        for _ in range(10):
            self.cast(yaw=16384)
        self.assertEqual((self.health.total, self.moira_health.total), before)
        self.assertFalse(self.search.targets)
        self.cast()
        self.assertEqual(self.search.targets, (self.tracer.body,))
        self.run.step({})
        self.assertFalse(self.search.active)
        self.assertFalse(self.search.targets)
        self.assertEqual(self.weapon.vars[375].value(), 0)

    def test_only_actual_grasp_damage_adds_three_fuel_above_passive_recovery(self):
        fuels = []
        for yaw in (0, 16384):
            self.setUp()
            self.weapon.write((expr.INSTANCE, 87), 10.0)
            self.cast(yaw=yaw)
            fuels.append(self.fuel())
        self.assertAlmostEqual(fuels[0] - fuels[1], 3, places=4)

    def test_grasp_respects_team_range_and_static_wall(self):
        for reason in ("team", "range", "wall"):
            with self.subTest(reason=reason):
                self.setUp()
                x, y, z = self.moira.mover.position
                if reason == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.tracer.team = self.moira.team
                elif reason == "range":
                    self.place(self.tracer, (x, y, z + 21))
                    self.run.step({})
                else:
                    world = World(box(x - 25, x + 25, y - 2, y, z - 25, z + 25)
                                  + box(x - 4, x + 4, y, y + 4, z + 1, z + 1.2))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                self.moira_health.damage(80)
                for _ in range(20):
                    self.cast()
                self.assertEqual(self.health.total, 150)
                self.assertEqual(self.moira_health.total, 120)
                self.assertFalse(self.search.targets)

    def test_recall_breaks_grasp_and_restores_damage_before_reacquisition(self):
        for _ in range(12):
            self.cast()
        self.assertAlmostEqual(self.health.total, 140.4, places=4)
        self.cast(tracer_buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertFalse(self.search.targets)
        while tracer.recalling(self.tracer):
            self.cast()
        self.assertGreater(self.health.total, 148)
        self.cast()
        self.assertEqual(self.search.targets, (self.tracer.body,))
        self.assertLess(self.health.total, 150)

    def test_grasp_hits_winston_barrier_then_reacquires_tracer_after_break(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        winston.team = self.tracer.team = 0
        self.moira.team = 1
        x, y, z = self.moira.mover.position
        self.place(self.tracer, (x, y, z + 6))
        self.place(winston, (x + 1, y, z + 6))
        for _ in range(8):
            self.run.step({})
        self.run.step({winston: (0, 0, 16)})
        for _ in range(6):
            self.run.step({})
        bubble = self.fight.winston_barriers.actors[winston.body]
        self.moira_health.damage(80)
        self.weapon.write((expr.INSTANCE, 87), 10.0)
        for _ in range(10):
            self.cast()
        self.assertAlmostEqual(bubble.health, 692, places=4)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.moira_health.total, 120)
        bubble.damage(bubble.health - 0.5)
        self.cast()
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health.total, 150)
        self.cast()
        self.assertAlmostEqual(self.health.total, 149.2, places=4)
        self.assertGreater(self.moira_health.total, 120)

    def test_lethal_tracer_fire_ends_grasp(self):
        self.cast()
        self.assertLess(self.health.total, 150)
        self.moira_health.damage(194)
        x, y, z = self.moira.mover.position
        before = self.health.total
        facing = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        self.run.step({self.moira: (0, 0, 2), self.tracer: (*facing, FIRE)})
        self.assertTrue(self.moira_health.dead)
        for _ in range(40):
            self.cast()
        self.assertEqual(self.health.total, before)


    def test_single_spray_packet_heals_then_tail_expires_after_release(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.moira.team
        self.health.damage(100)
        for _ in range(25):
            self.cast(buttons=FIRE)
            if self.fight.rockets:
                break
        packet, = self.fight.rockets
        self.assertIsNotNone(packet.spray)
        self.assertEqual(self.fuel(), 97.5)
        for _ in range(15):
            self.run.step({})
            if self.health.total > 50:
                break
        self.assertEqual(self.health.total, 53.5)
        effect = self.fight.moira_hots[self.tracer.body]
        self.assertEqual(effect.expires - effect.begins, 2)
        for _ in range(150):
            self.run.step({})
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.moira_hots)
        self.assertAlmostEqual(self.health.total, 88.5, places=4)

    def test_empty_spray_fuel_recovers_and_allows_new_primary_input(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.moira.team
        self.health.damage(100)
        self.weapon.write((expr.INSTANCE, 87), 0.0)
        for _ in range(55):
            self.cast(buttons=FIRE)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.health.total, 50)
        for _ in range(150):
            self.run.step({})
        self.assertGreater(self.fuel(), 5)
        for _ in range(30):
            self.cast(buttons=FIRE)
        self.assertGreater(self.health.total, 50)

    def test_spray_respects_team_range_and_static_wall(self):
        for gate in ("enemy", "range", "wall"):
            with self.subTest(gate=gate):
                self.setUp()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                if gate != "enemy":
                    self.tracer.team = self.moira.team
                    x, y, z = self.moira.mover.position
                    if gate == "range":
                        self.place(self.tracer, (x, y, z + 17))
                        self.run.step({})
                    else:
                        world = World(box(x - 25, x + 25, y - 2, y, z - 25, z + 25)
                                      + box(x - 4, x + 4, y, y + 4, z + 1, z + 1.2))
                        self.match.collision_provider = StaticCollisionProvider(world)
                        self.match.collision_world = world
                        for actor in self.run.players:
                            actor.mover.use(world=world)
                self.health.damage(100)
                for _ in range(40):
                    self.cast(buttons=FIRE)
                self.assertLess(self.fuel(), 100)
                self.assertEqual(self.health.total, 50)
                self.assertFalse(self.fight.moira_hots)
