"""Actual build 104319 Piledriver commands and Roadhog interactions."""

import unittest
from dataclasses import replace

import test_game_roadhog_defenses as fixtures
from test_game_combat import aim
from test_game_mover import box

from ow174.game import piledriver, stats
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
from ow174.game.content import heroes
from ow174.game.mover import FELL
from ow174.game.script import nodes


class RoadhogPiledriverTests(unittest.TestCase):
    def scene(self, distance=1.5, *, wall=None):
        run, hog, ball, other = fixtures.RoadhogDefenseTests().session("Wrecking Ball", distance=distance)
        other.mover.position[0] += 25
        other.mover.body = None
        if wall is not None:
            y = hog.mover.position[1]
            run.match.collision_world = World(
                box(-30, 30, y - 1, y, -30, 55) + box(-30, 30, y, y + 12, wall - .2, wall),
            )
        for _ in range(45):
            run.step({})
        return run, hog, ball, other

    def lift(self, run, ball, height):
        ball.mover.position[1] += height
        ball.mover.body = None
        ball.mover.flags_word |= FELL
        for _ in range(2):
            run.step({})

    def start(self, run, ball):
        run.step({ball: (0, 0, CROUCH)})
        return piledriver.instance_of(ball)

    def land(self, run, ball, instance):
        for _ in range(112):
            contact = instance.states.get(7)
            if contact is not None and contact.counter:
                return
            run.step({ball: (0, 0, 0)})
        self.fail("actual Piledriver did not land")

    def health(self, run, actor):
        return fixtures.RoadhogDefenseTests().health(run, actor)

    def test_ground_and_low_height_refuse_without_cooldown(self):
        for height in (0, 2):
            with self.subTest(height=height):
                run, hog, ball, _ = self.scene()
                if height:
                    self.lift(run, ball, height)
                instance = self.start(run, ball)
                for _ in range(20):
                    run.step({ball: (0, 0, CROUCH)})
                self.assertEqual(self.health(run, hog), 600)
                self.assertEqual(instance.state(24).remaining(), 0)
                self.assertNotIn(18, instance.states)

    def test_windup_hovers_and_ground_contact_damages_once_with_distance_falloff(self):
        for distance in (1.5, 6):
            with self.subTest(distance=distance):
                run, hog, ball, _ = self.scene(distance)
                fight = run.match.combat
                self.lift(run, ball, 8)
                instance = self.start(run, ball)
                self.assertTrue(instance.state(18).active)
                self.assertAlmostEqual(instance.state(24).remaining(), 10, delta=.02)
                hover = tuple(ball.mover.position)
                for _ in range(39):
                    run.step({})
                self.assertEqual(tuple(ball.mover.position), hover)
                self.assertEqual(self.health(run, hog), 600)
                self.land(run, ball, instance)
                self.assertAlmostEqual(ball.mover.position[1], 1, delta=.01)
                damage = 600 - self.health(run, hog)
                if distance == 1.5:
                    self.assertEqual(damage, 100)
                else:
                    self.assertAlmostEqual(damage, 53.220516, places=5)
                self.assertGreater(fight.impulses.velocity(hog.body)[1], 0)
                self.assertFalse(fight.is_stunned(hog))
                for _ in range(80):
                    run.step({})
                self.assertEqual(instance.state(7).counter, 1)
                self.assertEqual(self.health(run, hog), 600 - damage)

    def test_wall_team_and_range_reject_landing_contact(self):
        for mode in ("wall", "team", "range"):
            with self.subTest(mode=mode):
                run, hog, ball, _ = self.scene(12 if mode == "range" else 5,
                                              wall=2.5 if mode == "wall" else None)
                fight = run.match.combat
                if mode == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = ball.team = 0
                self.lift(run, ball, 8)
                instance = self.start(run, ball)
                self.land(run, ball, instance)
                self.assertEqual(self.health(run, hog), 600)
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertNotIn(hog.body, fight.pile_air_locks)
                self.assertGreater(instance.state(24).remaining(), 0)

    def test_breather_halves_central_hit_and_keeps_healing(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        self.lift(run, ball, 8)
        instance = self.start(run, ball)
        for _ in range(37):
            run.step({})
        run.step({hog: (0, 0, 16)})
        self.land(run, ball, instance)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 50)
        self.assertIn(hog.body, fight.impulses.live)
        self.assertFalse(fight.is_stunned(hog))
        before = pool.total
        for _ in range(13):
            run.step({})
        self.assertGreater(pool.total, before)

    def test_hook_cancels_windup_without_landing_or_refunding_cooldown(self):
        run, hog, ball, _ = self.scene()
        fight = run.match.combat
        self.lift(run, ball, 8)
        instance = self.start(run, ball)
        self.assertTrue(instance.state(18).active)
        look = aim(fight._eye(hog), fight._eye(ball))
        run.step({hog: (*look, 8)})
        for _ in range(55):
            run.step({hog: (*look, 0)})
            if fight.hooks.controlled(ball.body):
                break
        self.assertTrue(fight.hooks.controlled(ball.body))
        self.assertEqual(self.health(run, ball), 19970)
        self.assertFalse(instance.state(18).active)
        for _ in range(110):
            run.step({})
        self.assertEqual(self.health(run, hog), 600)
        self.assertEqual(instance.state(7).counter, 0)
        self.assertGreater(instance.state(24).remaining(), 0)

    def test_lift_locks_steering_but_whole_hog_keeps_hitting(self):
        run, hog, ball, victim = self.scene()
        fight = run.match.combat
        x, y, z = hog.mover.position
        ball.mover.position[:] = (x, y, z - 1.5)
        victim.mover.position[:] = (x, y, z + 13)
        ball.mover.body = victim.mover.body = None
        self.lift(run, ball, 8)
        instance = self.start(run, ball)
        for _ in range(65):
            run.step({})
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        ultimate, = script.ultimates
        look = aim(fight._eye(hog), (x, y + 1.2, z + 13))
        run.step({hog: (*look, 32)})
        self.land(run, ball, instance)
        start, end = fight.pile_air_locks[hog.body]
        self.assertEqual(end - start, 32)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.is_stunned(hog))
        before = self.health(run, victim)
        for _ in range(14):
            at = run.frame + 1
            run.frame = at
            run.match.update(at * .016, at)
            look = aim(fight._eye(hog), (x, y + 1.2, z + 13))
            hog.take_commands([Command(at, yaw=look[0], pitch=look[1], right=127)])
            ball.take_commands([Command(at)])
            victim.take_commands([Command(at)])
            self.assertEqual(hog.mover.throttles, (0, 0))
        for _ in range(35):
            look = aim(fight._eye(hog), (x, y + 1.2, z + 13))
            run.step({hog: (*look, 0)})
            if self.health(run, victim) < before:
                break
        self.assertLess(self.health(run, victim), before)
        for _ in range(35):
            run.step({})
        self.assertNotIn(hog.body, fight.pile_air_locks)

    def test_piledriver_air_lock_preserves_buffered_quick_melee(self):
        run, hog, ball, victim = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, ball.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        ball.mover.position[:] = (x, y, z - 1.5)
        victim.mover.position[:] = (x, y, z + 1.5)
        ball.mover.body = victim.mover.body = None
        self.lift(run, ball, 8)
        instance = self.start(run, ball)
        for _ in range(24):
            run.step({})

        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        melee = (0, 0, 0x800)
        before = self.health(run, victim)
        run.step({hog: melee})
        first = run.frame
        self.assertEqual(self.health(run, victim), before - 30)
        run.step({})
        run.step({hog: melee})
        self.assertNotIn(hog.body, fight.pile_air_locks)
        while run.frame < first + 55:
            run.step({hog: melee})
        self.assertEqual(instance.state(7).counter, 1)
        self.assertEqual(self.health(run, hog), 500)
        start, end = fight.pile_air_locks[hog.body]
        self.assertGreater(start, first + 2)
        self.assertLess(first + 62, end)
        self.assertFalse(fight.is_stunned(hog))

        # Keep the target grounded and in reach of the lifted Roadhog.
        moved_x, _, moved_z = hog.mover.position
        victim.mover.position[:] = (moved_x + 1, y, moved_z)
        victim.mover.body = None
        target = (moved_x + 1, y + 1.5, moved_z)
        while run.frame < first + 61:
            run.step({hog: (*aim(fight._eye(hog), target), 0x800)})
        self.assertEqual(self.health(run, victim), before - 30)

        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        for actor in run.players:
            actor.client.queue_entities(run.match.remote_movements(actor))
        yaw, pitch = aim(fight._eye(hog), target)
        hog.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=0x800, right=127)])
        ball.take_commands([Command(run.frame)])
        victim.take_commands([Command(run.frame)])
        self.assertEqual(hog.mover.throttles, (0, 0))
        self.assertTrue(piledriver.steering_locked(fight, hog, run.frame))
        self.assertGreater(hog.mover.position[1], y)
        self.assertEqual(self.health(run, victim), before - 60)
        for _ in range(65):
            run.step({hog: (*aim(fight._eye(hog), target), 0x800)})
        self.assertNotIn(hog.body, fight.pile_air_locks)
        self.assertEqual(self.health(run, victim), before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_projected_barrier_blocks_lift_and_air_lock(self):
        run, hog, ball, support = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        ball.team = 1
        zarya = next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 10, y, z)
        support.mover.body = None
        self.lift(run, ball, 8)
        instance = self.start(run, ball)
        for _ in range(38):
            run.step({})
        look = aim(fight._eye(support), (x, y + 1.2, z))
        run.step({support: (*look, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.land(run, ball, instance)
        self.assertEqual(self.health(run, hog), 600)
        self.assertEqual(bubble.health, 100)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertNotIn(hog.body, fight.pile_air_locks)

    def test_early_repress_refused_then_fresh_high_cast_after_cooldown(self):
        run, hog, ball, _ = self.scene()
        self.lift(run, ball, 8)
        instance = self.start(run, ball)
        self.land(run, ball, instance)
        for _ in range(100):
            run.step({})
        self.lift(run, ball, 8)
        run.step({ball: (0, 0, CROUCH)})
        for _ in range(60):
            run.step({})
        self.assertEqual(instance.state(18).counter, 1)
        self.assertEqual(self.health(run, hog), 500)
        for _ in range(680):
            run.step({})
            if instance.state(24).remaining() <= 0:
                break
        self.assertEqual(instance.state(24).remaining(), 0)
        ball.mover.position[2] = 1.5
        hog.mover.position[2] = 0
        hog.mover.body = None
        self.lift(run, ball, 8)
        self.start(run, ball)
        for _ in range(100):
            run.step({})
        self.assertEqual(instance.state(7).counter, 2)
        self.assertEqual(self.health(run, hog), 400)
