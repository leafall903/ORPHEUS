"""Actual Deadeye lock, release and Tracer defenses."""

import unittest
from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_mccree import McCreeArena

from ow174.game import combat, deadeye, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.script import expr, graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerMcCreeDeadeyeTests(McCreeArena):
    @classmethod
    def setUpClass(cls):
        if graph.graph(deadeye.TARGET_EFFECT) is None:
            raise unittest.SkipTest("verified 104319 Deadeye target-effect graph required")

    def setUp(self):
        super().setUp()
        self.script = self.mccree.body_script
        self.script.ult_seconds = 0
        self.root = next(i for i in self.script.component.instances.values()
                         if i.graph.index == deadeye.ULTIMATE)
        self.query = self.root.state(self.root.graph.node(26).state)
        self.visible = self.root.state(self.root.graph.node(86).state)
        self.effects = self.root.state(self.root.graph.node(11).state)
        self.place_tracer(8, offset=3)
        for _ in range(6):
            self.run.step({})

    def step(self, *, buttons=0, tracer_buttons=0, tracer_yaw=0):
        self.run.step({self.mccree: (0, 0, buttons), self.tracer: (tracer_yaw, 0, tracer_buttons)})

    def start(self, *, charged=True):
        self.script.set_ult_charge(self.script.component.entity_var(ULT_COST).value() if charged else 0)
        self.step(buttons=32)

    def child(self):
        number = self.effects.children.get(self.tracer.body)
        return self.script.component.instances.get(number)

    def charge(self):
        return self.child().vars[476].value()

    def wall(self):
        x, y, z = self.mccree.mover.position
        world = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                      + box(x - 8, x + 8, y, y + 5, z + 4, z + 4.2))
        self.match.collision_provider = StaticCollisionProvider(world)
        self.match.collision_world = world
        for actor in self.run.players:
            actor.mover.use(world=world)

    def test_off_crosshair_actual_release_hits_after_charging_target(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        self.start(charged=False)
        for _ in range(18):
            self.step()
        self.assertFalse(self.effects.children)
        self.start()
        self.assertEqual(self.script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(125):
            self.step()
        self.assertEqual(self.query.targets, (self.tracer.body,))
        self.assertEqual(self.visible.targets, (self.tracer.body,))
        child = self.child()
        self.assertEqual(child.vars[536].value(), expr.Entity(self.tracer.body))
        charge = self.charge()
        self.assertGreater(charge, 100)
        self.step(buttons=FIRE)
        for _ in range(40):
            if self.health.total < 1000:
                break
            self.step()
        self.assertAlmostEqual(1000 - self.health.total, charge, delta=4)
        self.assertEqual(len(self.hits()), 1)
        self.assertEqual(self.weapon.vars[53].value(), 5)
        for _ in range(50):
            self.step()
        self.assertFalse(self.effects.children)

    def test_wall_pauses_charge_and_prevents_release_damage(self):
        self.start()
        for _ in range(65):
            self.step()
        child = self.child()
        charge = self.charge()
        self.assertGreater(charge, 0)
        self.wall()
        for _ in range(24):
            self.step()
        self.assertIs(self.child(), child)
        self.assertEqual(self.visible.targets, ())
        self.assertEqual(self.charge(), charge)
        self.step(buttons=FIRE)
        for _ in range(35):
            self.step()
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())

    def test_blink_out_of_cone_and_reentry_restart_target_effect(self):
        self.place_tracer(5)
        self.step()
        self.start()
        for _ in range(65):
            self.step()
        original = self.child()
        self.assertIsNotNone(original)
        self.step(tracer_buttons=8, tracer_yaw=16384)
        self.step()
        self.assertEqual(self.query.targets, ())
        self.step()
        self.assertIsNone(self.child())
        for _ in range(12):
            self.step()
        self.step(tracer_buttons=8, tracer_yaw=-16384)
        for _ in range(6):
            self.step()
            if self.child() is not None:
                break
        self.assertIsNotNone(self.child())
        self.assertIsNot(self.child(), original)
        self.assertEqual(self.health.total, 150)

    def test_recall_clears_lock_and_blocks_actual_release(self):
        self.start()
        for _ in range(105):
            self.step()
        self.assertIsNotNone(self.child())
        self.step(tracer_buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.step(buttons=FIRE)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
        self.assertFalse(self.effects.children)

    def test_ally_never_acquires_target_effect(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.mccree.team
        self.start()
        for _ in range(100):
            self.step()
        self.assertFalse(self.effects.children)
        self.assertEqual(self.query.targets, ())
        self.assertEqual(self.visible.targets, ())
        self.step(buttons=FIRE)
        for _ in range(30):
            self.step()
        self.assertEqual(self.health.total, 150)


    def test_lethal_release_kills_tracer_once(self):
        self.start()
        for _ in range(180):
            self.step()
        self.assertGreater(self.charge(), 150)
        self.step(buttons=FIRE)
        for _ in range(55):
            self.step()
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.mccree.body)
        self.assertEqual([hit["+0x4"] for hit in self.hits()], [150])
        self.assertEqual(len(self.mccree.client.messages(combat.KILL_NOTICE)), 1)
        self.assertFalse(self.effects.children)

    def test_cancel_and_natural_end_retire_children_without_firing(self):
        for ending in ("cancel", "expiry"):
            with self.subTest(ending=ending):
                self.setUp()
                self.start()
                for _ in range(85):
                    self.step()
                self.assertIsNotNone(self.child())
                if ending == "cancel":
                    self.step(buttons=2)
                for _ in range(410):
                    self.step()
                self.assertFalse(self.effects.children)
                self.assertFalse(self.root.state(0).flags & self.root.state(0).RUNNING)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())
                self.assertEqual(self.weapon.vars[53].value(), 6)
                self.assertEqual(self.script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_winston_barrier_stops_release_before_tracer(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = winston.team = 0
        self.mccree.team = 1
        x, y, z = self.mccree.mover.position
        winston.mover.position[:] = (x + 3.8, y, z + 8)
        winston.mover.body = None
        self.run.step({})
        self.run.step({winston: (0, 0, 16)})
        for _ in range(7):
            self.run.step({})
        bubble = self.fight.winston_barriers.actors[winston.body]
        self.start()
        for _ in range(125):
            self.step()
        self.assertIsNotNone(self.child())
        self.step(buttons=FIRE)
        for _ in range(55):
            self.step()
        self.assertLess(bubble.health, 700)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())

    def test_tracer_lethal_fire_during_lock_prevents_release(self):
        self.start()
        for _ in range(80):
            self.step()
        self.assertIsNotNone(self.child())
        self.source_health.now[combat.HEALTH_POOL] = 6
        self.run.step({self.tracer: (*self.aim_at(self.tracer, self.mccree), FIRE)})
        self.assertTrue(self.source_health.dead)
        self.assertEqual(self.source_health.killer, self.tracer.body)
        for _ in range(55):
            self.step(buttons=FIRE)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
        self.assertFalse(self.effects.children)


    def test_actual_flashbang_interrupts_lock_and_leaves_tracer_unhurt(self):
        caster, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=deadeye.HERO)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        caster.team = self.tracer.team = 0
        self.mccree.team = 1
        x, y, z = self.mccree.mover.position
        caster.mover.position[:] = (x - 2, y, z)
        caster.mover.body = None
        self.run.step({})
        self.start()
        for _ in range(90):
            self.step()
        self.assertIsNotNone(self.child())
        facing = aim(self.fight._eye(caster), (x, y + 1, z))
        self.run.step({caster: (*facing, 16)})
        for _ in range(30):
            if self.fight.is_stunned(self.mccree):
                break
            self.step()
        self.assertTrue(self.fight.is_stunned(self.mccree))
        self.assertFalse(self.effects.children)
        self.assertFalse(self.root.state(0).flags & self.root.state(0).RUNNING)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.script.component.entity_var(ULT_CHARGE).value(), 0)


    def test_release_range_is_shorter_than_lock_range(self):
        self.place_tracer(150)
        for _ in range(3):
            self.step()
        self.start()
        for _ in range(125):
            self.step()
        self.assertEqual(self.query.targets, (self.tracer.body,))
        self.assertEqual(self.visible.targets, (self.tracer.body,))
        self.assertGreater(self.charge(), 100)
        self.step(buttons=FIRE)
        for _ in range(50):
            self.step()
        self.assertEqual(self.weapon.vars[53].value(), 5)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())


    def test_body_replacement_destroys_existing_lock_without_firing(self):
        self.start()
        for _ in range(90):
            self.step()
        child = self.child()
        self.assertIsNotNone(child)
        old = self.mccree.body
        self.assertTrue(self.match.switch_hero(self.mccree, self.mccree.hero))
        self.assertNotEqual(self.mccree.body, old)
        self.assertTrue(self.script.retired)
        self.assertFalse(self.effects.children)
        self.assertNotIn(child.id, self.script.component.instances)
        for _ in range(24):
            self.step()
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
