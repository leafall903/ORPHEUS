"""Actual Combat Roll movement and Tracer interactions."""

import math
from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_mccree import McCreeArena

from ow174.game import combat, mccree_roll
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE


class TracerMcCreeRollTests(McCreeArena):
    def setUp(self):
        super().setUp()
        self.roll_graph = next(i for i in self.mccree.body_script.component.instances.values()
                               if i.graph.index == mccree_roll.ROLL)
        self.ability = next(state for state in self.roll_graph.states.values() if state.node.pos == 2)

    def roll(self, *, yaw=16384):
        self.run.step({self.mccree: (yaw, 0, 8)})

    def test_roll_refills_and_leaves_tracer_line_but_fresh_aim_hits(self):
        x, _, _ = self.mccree.mover.position
        target_aim = self.aim_at(self.tracer, self.mccree)
        self.run.step({self.mccree: (*self.aim_at(self.mccree, self.tracer), FIRE)})
        for _ in range(40):
            self.run.step({})
        self.assertEqual(self.weapon.vars[53].value(), 5)
        self.roll()
        self.assertAlmostEqual(self.mccree.mover.position[0], x)
        self.assertTrue(self.mccree.body_script.world.velocity_requests)
        self.run.step({})
        self.assertAlmostEqual(self.mccree.mover.velocity[0], 15, places=2)
        for _ in range(38):
            self.run.step({})
        self.assertGreater(self.mccree.mover.position[0] - x, 5.8)
        self.assertLess(self.mccree.mover.position[0] - x, 6.5)
        self.assertEqual(self.weapon.vars[53].value(), 6)
        self.run.step({self.tracer: (*target_aim, FIRE)})
        self.assertEqual(self.source_health.total, 225)
        for _ in range(12):
            self.run.step({})
        self.run.step({self.tracer: (*self.aim_at(self.tracer, self.mccree), FIRE),
                       self.mccree: (*self.aim_at(self.mccree, self.tracer), FIRE)})
        self.assertEqual(self.source_health.total, 213)
        self.assertEqual(self.health.total, 10)
        self.assertEqual(self.weapon.vars[53].value(), 5)

    def test_wall_clips_roll_and_cooldown_requires_a_later_press(self):
        x, y, z = self.mccree.mover.position
        world = World(box(x - 12, x + 12, y - 2, y, z - 12, z + 12)
                      + box(x + 2, x + 2.2, y, y + 4, z - 4, z + 4))
        self.match.collision_provider = StaticCollisionProvider(world)
        self.match.collision_world = world
        for actor in self.run.players:
            actor.mover.use(world=world)
        self.roll()
        for _ in range(40):
            self.run.step({})
        moved = self.mccree.mover.position[0] - x
        self.assertGreater(moved, 1)
        self.assertLessEqual(moved, 2 - self.mccree.mover.data.stand_radius + 0.001)
        remaining = self.ability.remaining()
        self.assertGreater(remaining, 1)
        self.roll()
        self.assertFalse(self.mccree.body_script.world.velocity_requests)
        for _ in range(math.ceil(remaining / combat.TICK) + 5):
            self.run.step({})
        self.assertEqual(self.ability.remaining(), 0)
        self.roll(yaw=-16384)
        self.assertTrue(self.mccree.body_script.world.velocity_requests)
        self.run.step({})
        self.assertLess(self.mccree.mover.velocity[0], -14.9)

    def test_flashbang_interrupts_actual_roll(self):
        caster, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=mccree_roll.HERO)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        caster.team = self.tracer.team = 0
        self.mccree.team = 1
        x, y, z = self.mccree.mover.position
        caster.mover.position[:] = (x + 2, y, z)
        caster.mover.body = None
        self.run.step({})
        self.roll()
        self.assertTrue(self.mccree.body_script.world.velocity_requests)
        facing = aim(self.fight._eye(caster), (x + 0.5, y + 1, z))
        self.run.step({caster: (*facing, 16)})
        for _ in range(22):
            if self.fight.is_stunned(self.mccree):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.mccree))
        self.assertFalse(self.ability.flags & self.ability.RUNNING)
        self.assertFalse(self.mccree.body_script.world.velocity_requests)
        stopped = tuple(self.mccree.mover.position)
        for _ in range(20):
            self.run.step({})
        self.assertEqual(tuple(self.mccree.mover.position), stopped)
        self.assertLess(stopped[0] - x, 5)


    def test_tracer_kill_before_next_frame_clears_pending_roll(self):
        self.source_health.now[combat.HEALTH_POOL] = 6
        start = tuple(self.mccree.mover.position)
        facing = self.aim_at(self.tracer, self.mccree)
        self.run.step({self.mccree: (16384, 0, 8), self.tracer: (*facing, FIRE)})
        self.assertTrue(self.source_health.dead)
        self.assertEqual(self.source_health.killer, self.tracer.body)
        self.assertFalse(self.mccree.body_script.world.velocity_requests)
        for _ in range(40):
            self.run.step({})
        self.assertEqual(tuple(self.mccree.mover.position), start)

    def test_held_fan_uses_six_refilled_rounds_after_roll(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        self.run.step({self.mccree: (*self.aim_at(self.mccree, self.tracer), FIRE)})
        for _ in range(40):
            self.run.step({})
        self.assertEqual(self.weapon.vars[53].value(), 5)
        self.roll()
        for _ in range(22):
            self.run.step({self.mccree: (*self.aim_at(self.mccree, self.tracer), 2)})
            self.assertEqual(self.health.total, 930)
        for _ in range(110):
            self.run.step({self.mccree: (*self.aim_at(self.mccree, self.tracer), 2)})
            if self.weapon.vars[53].value() == 0:
                break
        self.assertEqual(self.weapon.vars[53].value(), 0)
        self.assertEqual(len(self.weapon.state(15).shot_spread), 6)
        self.assertLess(self.health.total, 930)
