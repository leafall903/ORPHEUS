"""Authored Winston casts -> actual damage-path barrier intersections.

Static combat evidence only. Replicated actor/graph rendering is still open.
"""

import math
import unittest

from test_game_combat import Run
from test_game_mover import box, wall

from ow174.game import combat, winston
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


def active_bubbles(fight):
    return {actor.source_body: actor for actor in fight.barriers.world.actors.values()
            if not actor.retired and actor.component is not None}


def expires(actor):
    hosted = next((i for i in actor.component.instances.values() if i.graph.index == 0xFC), None)
    duration = (hosted.evaluate(hosted.state(11).node.field('m_EB0FC261')) if hosted is not None
                else actor.source_component.entity_var(567).value())
    return actor.born_tick * combat.TICK + expr.to_float(duration)


class BubbleGeometryTests(unittest.TestCase):
    def setUp(self):
        self.bubble = winston.Bubble(None, 1, (0, 0, 0), 700, 100)

    def test_entry_exit_miss_and_surface_directions(self):
        b = self.bubble
        self.assertEqual(b.ray((0, 0, 10), (0, 0, -1)), 5)
        self.assertEqual(b.ray((0, 0, 0), (0, 0, 1)), 5)
        self.assertIsNone(b.ray((0, 0, 10), (0, 0, 1)))
        self.assertIsNone(b.ray((6, 0, 10), (0, 0, -1)))
        self.assertEqual(b.ray((0, 0, 5), (0, 0, -1)), 0)
        self.assertIsNone(b.ray((0, 0, 5), (0, 0, 1)))
        self.assertIsNone(b.ray((0, 0, 5), (1, 0, 0)))


class BarrierTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=('Winston', 'Enemy', 'Ally'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.enemy, self.ally = self.run.players
        self.match.switch_hero(self.owner, heroes()[winston.HERO])
        self.owner.team = self.ally.team = 0
        self.enemy.team = 1
        self.owner.mover.position[:] = [0, 1, 0]
        self.enemy.mover.position[:] = [0, 1, 10]
        self.ally.mover.position[:] = [20, 1, 0]
        self.fight = self.match.combat
        self.fight.now = self.run.frame * combat.TICK

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets()
                                           if t.owner is player)).total

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.velocity = (0, 0, 0)
        # Explicit fixture teleport must also discard the cached collision body;
        # changing feet alone is overwritten from that body's old position.
        player.mover.body = None

    def cast(self):
        self.run.step({self.owner: (0, 0, 16)})
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        for _ in range(3):
            self.run.step({})
        return active_bubbles(self.fight)[self.owner.body]

    def fire(self, player=None, *, yaw=-32768, button=1):
        self.run.step({player or self.enemy: (yaw, 0, button)})

    def test_deployment_has_original_health_radius_lifetime_and_cooldown(self):
        bubble = self.cast()
        self.assertEqual(bubble.health, 700)
        self.assertEqual(bubble.radius, 5)
        self.assertAlmostEqual(expires(bubble) - self.fight.now, 9)
        self.assertEqual(bubble.point, (0, expr.f32(1.1), 0))
        ins = next(i for i in self.owner.body_script.component.instances.values()
                   if i.graph.index == 0xFA)
        self.assertGreater(ins.states[2].remaining(), 12.9)

    def test_idle_deployment_preserves_release_time_and_does_not_replay(self):
        self.run.step({self.owner: (0, 0, 16)})
        accepted = self.run.frame
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        self.run.frame = accepted + 30
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        bubble = active_bubbles(self.fight)[self.owner.body]
        expected_expiry = (accepted + 3) * combat.TICK + 9
        self.assertAlmostEqual(expires(bubble), expected_expiry)
        self.assertEqual(bubble.health, 700)
        self.run.step({})
        self.assertIs(active_bubbles(self.fight)[self.owner.body], bubble)

        self.run.frame = math.ceil(expected_expiry / combat.TICK)
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        self.run.step({})
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))

    def test_enemy_actual_rifle_hits_shield_instead_of_winston(self):
        bubble = self.cast()
        self.fire()
        self.assertEqual(bubble.health, 680)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.enemy.client.messages(combat.HIT), [])

    def test_aborted_deployment_wait_does_not_create_a_sphere(self):
        self.run.step({self.owner: (0, 0, 16)})
        accepted = self.run.frame
        script = self.owner.body_script
        barrier = next(i for i in script.component.instances.values() if i.graph.index == 0xFA)
        deployment = barrier.states[3]
        ability = barrier.states[2]
        self.assertTrue(deployment.active)
        # Cancel the scope and its Wait after real input admits the cast.
        ability.stop()
        deployment.end(False)
        script.component.run_now()
        self.assertNotIn(1, barrier.states)
        self.assertEqual(ability.remaining(), 0)
        # A separate cooldown write must not stand in for the finish branch.
        barrier.write((0, 61), 13.0)
        self.assertGreater(ability.remaining(), 12.9)
        for _ in range(5):
            self.run.step({})
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        self.fire()
        self.assertEqual(self.health(self.owner), 465)

        self.run.frame = accepted + math.ceil(13 / combat.TICK) + 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        self.assertEqual(self.cast().health, 700)

    def test_accepted_deployment_releases_through_primal_activation(self):
        for rage_offset in (0, 1, 3):
            with self.subTest(rage_offset=rage_offset):
                self.setUp()
                script = self.owner.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                barrier = next(i for i in script.component.instances.values() if i.graph.index == 0xFA)
                for offset in range(4):
                    buttons = (16 if offset == 0 else 0) | (32 if offset == rage_offset else 0)
                    self.run.step({self.owner: (0, 0, buttons)})
                    self.assertEqual(self.owner.body in active_bubbles(self.fight), offset == 3)
                self.assertTrue(winston.primal_active(script.component))
                deployment = barrier.states[3]
                self.assertEqual(deployment.ended_at - deployment.start, 50)
                self.assertEqual(deployment.counter, 1)
                bubble = active_bubbles(self.fight)[self.owner.body]
                self.assertAlmostEqual(expires(bubble) - self.fight.now, 9)
                self.fire()
                self.assertEqual(bubble.health, 680)
                self.assertEqual(self.health(self.owner), 1000)

    def test_new_barrier_press_during_primal_cannot_deploy_on_expiry(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.owner: (0, 0, 32)})
        self.assertTrue(winston.primal_active(script.component))
        self.run.step({self.owner: (0, 0, 16)})
        barrier = next(i for i in script.component.instances.values() if i.graph.index == 0xFA)
        self.assertNotIn(3, barrier.states)
        self.assertEqual(barrier.states[2].remaining(), 0)

        self.run.frame += 625
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(winston.primal_active(script.component))
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        self.run.step({})
        self.assertNotIn(3, barrier.states)
        self.assertEqual(self.cast().health, 700)

    def test_hitscan_damages_shield_even_without_body_behind_it(self):
        bubble = self.cast()
        self.move(self.owner, [20, 1, 0])
        self.fire()
        self.assertEqual(bubble.health, 680)
        self.assertEqual(self.health(self.owner), 500)

    def test_quick_melee_contacts_hostile_surface_and_body_once(self):
        bubble = self.cast()
        self.move(self.owner, (0, 1, 4.2))
        self.move(self.enemy, (0, 1, 6))
        self.fire(button=0x800)
        for _ in range(6):
            self.fire(button=0x800)
        self.assertEqual(bubble.health, 670)
        self.assertEqual(self.health(self.owner), 475)

    def test_aiming_away_misses_shield(self):
        bubble = self.cast()
        self.fire(yaw=0)
        self.assertEqual(bubble.health, 700)

    def test_static_wall_and_shield_resolve_in_contact_order(self):
        for button, shield_damage in ((1, 20), (2, 120)):
            for wall_z in (3, 7):
                with self.subTest(button=button, wall_z=wall_z):
                    self.setUp()
                    self.match.collision_world = World(
                        box(-30, 30, -1, 1, -30, 30) + wall(-8, wall_z, 8, wall_z)
                    )
                    bubble = self.cast()
                    self.fire(button=button)
                    for _ in range(30):
                        self.run.step({})
                    self.assertEqual(bubble.health, 700 - shield_damage if wall_z == 3 else 700)
                    self.assertEqual(self.health(self.owner), 500)
                    self.assertEqual(self.fight.rockets, [])

    def test_friendly_rifle_and_helix_pass_out_through_winston_barrier(self):
        for button in (1, 2):
            with self.subTest(button=button):
                self.setUp()
                bubble = self.cast()
                self.move(self.ally, (0, 1, 1))
                self.fire(self.ally, yaw=0, button=button)
                for _ in range(30):
                    self.run.step({})
                self.assertEqual(bubble.health, 700)
                self.assertEqual(self.health(self.owner), 500)
                self.assertLess(self.health(self.enemy), 200)
                self.assertEqual(self.fight.rockets, [])

    def test_breaking_shot_has_no_health_spill_next_shot_reaches_body(self):
        bubble = self.cast()
        bubble.health = 5
        self.fire()
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(self.owner), 500)
        for _ in range(40):
            self.run.step({})
        self.fire()
        self.assertLess(self.health(self.owner), 500)

    def test_own_tesla_can_fire_out_of_friendly_shield(self):
        bubble = self.cast()
        self.move(self.enemy, [0, 1, 7])
        self.fire(self.owner, yaw=0)
        self.assertEqual(bubble.health, 700)
        self.assertAlmostEqual(self.health(self.enemy), 199.04)

    def test_enemy_inside_can_hit_inside_and_shots_crossing_exit_are_blocked(self):
        bubble = self.cast()
        self.move(self.enemy, [0, 1, 3])
        self.fire()
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.health(self.owner), 465)  # inherited head test: 40 crit, minus five armour
        self.move(self.owner, [0, 1, -8])
        for _ in range(40):
            self.run.step({})
        before = self.health(self.owner)
        self.fire()
        self.assertEqual(bubble.health, 680)
        self.assertEqual(self.health(self.owner), before)

    def test_tesla_hits_shield_once_per_frame_for_multiple_targets_behind_it(self):
        self.match.switch_hero(self.enemy, heroes()[winston.HERO])
        self.move(self.enemy, [0, 1, 7])
        self.move(self.ally, [.5, 1, 0])
        bubble = self.cast()
        self.fire()
        self.assertEqual(bubble.health, expr.f32(699.04))
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.health(self.ally), 200)

    def test_helix_is_consumed_and_splash_cannot_cross_even_a_broken_shield(self):
        bubble = self.cast()
        bubble.health = 5
        self.fire(button=2)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.rockets, [])

    def test_helix_applies_direct_and_explosion_damage_to_shield(self):
        bubble = self.cast()
        self.fire(button=2)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(bubble.health, 580)  # actual 40 direct + 80 explosion
        self.assertEqual(self.health(self.owner), 500)

    def test_idle_barrier_deployment_intercepts_helix_already_in_flight(self):
        for deploy in (False, True):
            with self.subTest(deploy=deploy):
                self.setUp()
                self.move(self.enemy, (0, 1, 20))
                self.fire(button=2)
                for _ in range(30):
                    if self.fight.rockets:
                        break
                    self.run.step({})
                self.assertEqual(len(self.fight.rockets), 1)
                self.run.step({self.owner: (0, 0, 16)} if deploy else {})
                self.assertEqual(len(self.fight.rockets), 1)
                self.assertNotIn(self.owner.body, active_bubbles(self.fight))

                self.run.frame += 45
                self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
                self.assertEqual(self.fight.rockets, [])
                if deploy:
                    bubble = active_bubbles(self.fight)[self.owner.body]
                    self.assertEqual(bubble.health, 580)
                    self.assertEqual(self.health(self.owner), 500)
                else:
                    self.assertEqual(self.health(self.owner), 390)

    def test_lethal_rifle_hit_during_pending_deployment_prevents_barrier(self):
        self.move(self.enemy, (0, 1, 3))
        target = next(t for t in self.fight._targets() if t.owner is self.owner)
        self.fight.vitals_of(target).damage(490)
        instance = next(i for i in self.owner.body_script.component.instances.values()
                        if i.graph.index == 0xFA)
        self.run.step({self.owner: (0, 0, 16), self.enemy: (-32768, 0, 1)})
        old = self.owner.body
        self.assertEqual(instance.state(3).counter, 1)
        self.assertTrue(self.fight.is_dead(self.owner))
        self.assertEqual(self.fight.vitals[old].killer, self.enemy.body)
        self.run.frame += 30
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertEqual(self.owner.body, old)
        self.assertNotIn(old, active_bubbles(self.fight))

    def test_breaking_tesla_slice_does_not_spill_to_other_targets_or_give_charge(self):
        self.match.switch_hero(self.enemy, heroes()[winston.HERO])
        self.enemy.body_script.ult_seconds = 0
        self.move(self.enemy, [0, 1, 7])
        self.move(self.ally, [.5, 1, 0])
        bubble = self.cast()
        bubble.health = .2
        self.fire()
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.health(self.ally), 200)
        self.assertEqual(self.enemy.body_script.component.entity_var(342).value(), 0)

    def test_deployed_shield_persists_after_owner_death_and_cannot_recast_dead(self):
        bubble = self.cast()
        target = next(t for t in self.fight._targets() if t.owner is self.owner)
        self.fight._damage(self.enemy, target, 1000, False, None, self.run.frame)
        self.fire()
        self.assertEqual(bubble.health, 680)
        self.run.step({self.owner: (0, 0, 16)})
        self.assertIs(active_bubbles(self.fight)[self.owner.body], bubble)

    def test_expiry_removes_shield_without_requiring_an_owner_command(self):
        bubble = self.cast()
        frame = self.run.frame + 570
        self.match.update(frame * combat.TICK, frame, frames=False)
        self.assertGreaterEqual(self.fight.now, expires(bubble))
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))

    def test_stale_click_cannot_refresh_barrier_ready_recast_can(self):
        original = self.cast()
        for _ in range(100):
            self.run.step({})
        self.run.step({self.owner: (0, 0, 16)})
        for _ in range(730):
            self.run.step({})
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        replacement = self.cast()
        self.assertIsNot(replacement, original)
        self.assertEqual(replacement.health, 700)

    def test_body_retirement_drops_shield_and_activation_guard(self):
        self.cast()
        body = self.owner.body
        self.fight.retire_player_body(self.owner)
        self.assertNotIn(body, active_bubbles(self.fight))
        self.assertNotIn(body, self.fight.barriers.seen)

    def test_server_cooldown_write_and_repeated_command_cannot_refill_shield(self):
        bubble = self.cast()
        bubble.health = 123
        instance = next(i for i in self.owner.body_script.component.instances.values()
                        if i.graph.index == 0xFA)
        instance.write((0, 61), 13.0)
        self.run.step({})
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.owner, Command(frame, buttons=16))
        self.assertIs(active_bubbles(self.fight)[self.owner.body], bubble)
        self.assertEqual(bubble.health, 123)

    def test_old_short_press_expires_and_fresh_post_cooldown_press_deploys(self):
        first = self.cast()
        instance = next(i for i in self.owner.body_script.component.instances.values()
                        if i.graph.index == 0xFA)
        ability = instance.states[2]
        self.run.step({self.owner: (0, 0, 16)})
        for _ in range(830):
            self.run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertNotIn(self.owner.body, active_bubbles(self.fight))
        self.run.step({self.owner: (0, 0, 16)})
        for _ in range(4):
            self.run.step({})
        self.assertIsNot(active_bubbles(self.fight)[self.owner.body], first)

    def test_near_expiry_short_buffer_and_continuous_hold_wait_for_actual_cooldown(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.setUp()
                first = self.cast()
                instance = next(i for i in self.owner.body_script.component.instances.values()
                                if i.graph.index == 0xFA)
                ability = instance.states[2]
                while ability.remaining() > (.08 if not held else 12):
                    self.run.step({})
                remaining = ability.remaining()
                self.assertGreater(remaining, 0)
                self.run.step({self.owner: (0, 0, 16)})
                for frame in range(math.ceil(remaining / combat.TICK) + 6):
                    self.run.step({self.owner: (0, 0, 16)} if held else {})
                    if (frame + 2) * combat.TICK < remaining - .001:
                        self.assertIn(active_bubbles(self.fight).get(self.owner.body), (None, first))
                second = active_bubbles(self.fight)[self.owner.body]
                self.assertIsNot(second, first)
                for _ in range(30):
                    self.run.step({self.owner: (0, 0, 16)} if held else {})
                self.assertIs(active_bubbles(self.fight)[self.owner.body], second)


if __name__ == '__main__':
    unittest.main()
