"""Actual Q hits, resource force evaluation and reconstructed swept external motion."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_game_roadhog_graviton as graviton_tests
import test_game_roadhog_trap as trap_tests
from test_game_combat import aim, in_world

from ow174.game import combat, defenses, knockback, lucio_barrier, reinhardt_charge, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class RoadhogKnockbackTests(unittest.TestCase):
    step = phases.RoadhogDefenseTests.step
    health = phases.RoadhogDefenseTests.health

    def session(self, distance=4, name='Reaper'):
        run, hog, target, rear = phases.RoadhogDefenseTests().session(name, distance=distance)
        rear.mover.position[0] = 30
        rear.mover.body = None
        for _ in range(30):
            self.step(run, hog, target)
        hog.body_script.set_ult_charge(2240)
        return run, hog, target

    def first(self, run, hog, target):
        self.step(run, hog, target, fire=32)
        for _ in range(50):
            if target.body in run.match.combat.impulses.live:
                return run.match.combat.impulses.live[target.body]
            self.step(run, hog, target)
        self.fail('actual Q did not apply an external impulse')

    def stop(self, hog):
        for ability in hog.body_script.ultimates:
            ability.stop()
        hog.body_script.component.run_now()

    def test_authored_force_and_rings_evaluate_without_invented_scale_default(self):
        run, hog, target = self.session()
        self.first(run, hog, target)
        state = next(s for i in hog.body_script.component.instances.values()
                     if i.graph.index == 0x1C6 for s in i.states.values() if s.node.pos == 33)
        spec = knockback.evaluate(state, state.node.field('m_6396149F')['m_modifyHealth'])
        self.assertEqual((spec.force, spec.cap, spec.lift_cap), (14, 10, 14))
        self.assertAlmostEqual(spec.lift, 1.8)
        self.assertEqual(spec.scale(5), (1, 1, 1))
        self.assertEqual(spec.scale(20), (1, 1, 1))
        self.assertAlmostEqual(spec.scale(60)[0], .55)
        self.assertEqual(spec.scale(101), (0, 0, 0))
        state.instance.write((1, 10619), .5)
        changed = knockback.evaluate(state, state.node.field('m_6396149F')['m_modifyHealth'])
        self.assertEqual(changed.force, 7)
        self.assertEqual(changed.cap, 5)

    def test_one_volley_aggregates_pellets_caps_velocity_moves_then_damps_after_release(self):
        run, hog, target = self.session()
        fight = run.match.combat
        with patch.object(fight, '_shoot_pellet', wraps=fight._shoot_pellet) as rays:
            motion = self.first(run, hog, target)
        self.assertEqual(rays.call_count, 16)
        self.assertEqual(len(fight.impulses.seen), 1)
        self.assertGreater(len(hog.client.messages(combat.HIT)), 1)
        self.assertAlmostEqual(math.hypot(motion.velocity[0], motion.velocity[2]), 10)
        self.assertAlmostEqual(motion.velocity[1], 1.8)
        self.assertLess(self.health(run, target), 20000)
        position = tuple(target.mover.position)
        self.stop(hog)
        self.step(run, hog, target)
        self.assertGreater(target.mover.position[2], position[2]+.1)
        self.assertGreater(target.mover.position[1], position[1])
        self.assertLess(math.hypot(*motion.velocity[::2]), 10)
        pose = target.movement()
        self.assertGreater(pose.velocity[2], target.mover.velocity[2])
        after = tuple(target.mover.position)
        fight.update(fight.now, fight.tick, frames=False)
        self.assertEqual(tuple(target.mover.position), after)
        for _ in range(100):
            self.step(run, hog, target)
        self.assertNotIn(target.body, fight.impulses.live)
        self.assertLess(target.mover.position[2]-position[2], 5)

    def test_real_collision_wall_blocks_displacement_after_body_hit(self):
        from test_game_mover import FLOOR, wall

        run, hog, target = self.session()
        y = target.mover.position[1]
        run.match.collision_world = World(FLOOR + wall(-10, 5, 10, 5, y-5, y+10))
        target.mover.world = run.match.collision_world
        target.mover.body = None
        self.first(run, hog, target)
        self.stop(hog)
        for _ in range(100):
            self.step(run, hog, target)
        self.assertGreater(target.mover.position[2], 4)
        self.assertLess(target.mover.position[2], 4.6)
        self.assertLess(self.health(run, target), 20000)

    def test_miss_wall_and_real_fortify_do_not_apply_motion(self):
        for mode in ('miss', 'wall', 'fortify'):
            with self.subTest(mode=mode):
                run, hog, target = self.session(name='Orisa' if mode == 'fortify' else 'Reaper')
                if mode == 'fortify':
                    self.step(run, hog, target, cast=8)
                elif mode == 'wall':
                    y = target.mover.position[1]
                    run.match.collision_world = World([
                        (-10, y-5, 2, 10, y-5, 2, 10, y+10, 2),
                        (-10, y-5, 2, 10, y+10, 2, -10, y+10, 2),
                    ])
                if mode == 'miss':
                    run.step({hog: (16384, 0, 32)})
                    for _ in range(50):
                        run.step({hog: (16384, 0, 0)})
                else:
                    self.step(run, hog, target, fire=32)
                    for _ in range(50):
                        self.step(run, hog, target)
                self.assertFalse(run.match.combat.impulses.live)
                self.assertAlmostEqual(target.mover.position[2], 4)

    def test_whole_hog_force_resumes_after_fortify_expires(self):
        run, hog, orisa = self.session(name="Orisa")
        fight = run.match.combat
        start = tuple(orisa.mover.position)
        self.step(run, hog, orisa, cast=8)
        self.assertTrue(defenses.control_immune(orisa))
        self.step(run, hog, orisa, fire=32)
        for _ in range(320):
            if not defenses.control_immune(orisa):
                break
            self.assertNotIn(orisa.body, fight.impulses.live)
            self.assertEqual(tuple(orisa.mover.position), start)
            self.step(run, hog, orisa)
        self.assertFalse(defenses.control_immune(orisa))
        fortified_health = self.health(run, orisa)
        self.assertLess(fortified_health, 20000)
        for _ in range(40):
            self.step(run, hog, orisa)
            if orisa.body in fight.impulses.live:
                break
        self.assertIn(orisa.body, fight.impulses.live)
        self.assertLess(self.health(run, orisa), fortified_health)
        self.step(run, hog, orisa)
        self.assertGreater(orisa.mover.position[2], start[2])

    def test_recipient_death_and_replacement_retire_motion(self):
        for mode in ('death', 'replacement'):
            with self.subTest(mode=mode):
                run, hog, target = self.session()
                self.first(run, hog, target)
                self.stop(hog)
                old = target.body
                if mode == 'replacement':
                    run.match.switch_hero(target, heroes()[0x02E0000000000040])
                else:
                    victim = next(t for t in run.match.combat._targets() if t.owner is target)
                    run.match.combat._damage(hog, victim, 50000, False, None, run.frame)
                self.step(run, hog, target)
                self.assertNotIn(old, run.match.combat.impulses.live)

    def test_world_gap_and_continuous_external_integration_agree_without_new_commands(self):
        outcomes = []
        for gap in (False, True):
            run, hog, target = self.session()
            self.first(run, hog, target)
            self.stop(hog)
            fight = run.match.combat
            start = fight.tick
            ticks = (start+60,) if gap else range(start+1, start+61)
            for tick in ticks:
                fight.update(tick*.016, tick, frames=False)
            outcomes.append((tuple(target.mover.position), tuple(fight.impulses.live)))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_far_body_hit_uses_authored_force_ring_at_impact_distance(self):
        run, hog, target = self.session(distance=30)
        motion = self.first(run, hog, target)
        self.assertGreater(math.hypot(motion.velocity[0], motion.velocity[2]), 8)
        self.assertLess(math.hypot(motion.velocity[0], motion.velocity[2]), 10)

    def test_repeated_real_q_stays_capped_and_owner_death_emits_no_new_impulses(self):
        run, hog, target = self.session()
        self.first(run, hog, target)
        fight = run.match.combat
        for _ in range(70):
            self.step(run, hog, target)
            for motion in fight.impulses.live.values():
                self.assertLessEqual(math.hypot(motion.velocity[0], motion.velocity[2]), 10.00001)
                self.assertLessEqual(motion.velocity[1], 14)
        self.assertGreater(len(fight.impulses.seen), 1)
        before = set(fight.impulses.seen)
        own = next(t for t in fight._targets() if t.owner is hog)
        fight._damage(target, own, 50000, False, None, run.frame)
        for _ in range(50):
            self.step(run, hog, target)
            self.assertTrue(set(fight.impulses.seen).issubset(before))
        self.assertNotIn(target.body, fight.impulses.live)

    def test_actual_whole_hog_damage_does_not_override_live_hook_pull(self):
        run, shooter, target, hooker = phases.RoadhogDefenseTests().session("Reaper", distance=4)
        match = run.match
        fight = match.combat
        match.switch_hero(hooker, heroes()[0x02E0000000000040])
        y = shooter.mover.position[1]
        hooker.mover.position[:] = (0, y, -14)
        shooter.mover.position[:] = (3, y, 0)
        shooter.mover.body = hooker.mover.body = None
        match.collision_world = flat_world(y)

        def angles(actor):
            x, y, z = target.mover.position
            return aim(fight._eye(actor), (x, y + 1, z))

        for _ in range(5):
            run.step({})
        run.step({hooker: (*angles(hooker), 8)})
        for _ in range(55):
            run.step({hooker: (*angles(hooker), 0)})
            if fight.hooks.controlled(target.body):
                break
        self.assertTrue(fight.hooks.controlled(target.body))
        before = self.health(run, target)
        shooter.body_script.set_ult_charge(2240)
        run.step({shooter: (*angles(shooter), 32)})
        for _ in range(55):
            run.step({shooter: (*angles(shooter), 0)})
            if self.health(run, target) < before:
                break
        self.assertLess(self.health(run, target), before)
        self.assertTrue(fight.hooks.controlled(target.body))
        self.assertNotIn(target.body, fight.impulses.live)

    def test_actual_hook_clears_existing_whole_hog_motion_at_contact(self):
        run, shooter, target, hooker = phases.RoadhogDefenseTests().session("Reaper", distance=4)
        match = run.match
        fight = match.combat
        match.switch_hero(hooker, heroes()[0x02E0000000000040])
        y = shooter.mover.position[1]
        hooker.mover.position[:] = (-3, y, 0)
        hooker.mover.body = None
        match.collision_world = flat_world(y)
        shooter.body_script.set_ult_charge(2240)

        self.first(run, shooter, target)
        self.assertNotEqual(fight.impulses.velocity(target.body), (0, 0, 0))

        for frame in range(55):
            x, y, z = target.mover.position
            angles = aim(fight._eye(hooker), (x, y + 1, z))
            run.step({hooker: (*angles, 8 if frame == 0 else 0)})
            if fight.hooks.controlled(target.body):
                break

        self.assertTrue(fight.hooks.controlled(target.body))
        self.assertNotIn(target.body, fight.impulses.live)
        self.assertEqual(fight.impulses.velocity(target.body), (0, 0, 0))

    def trap_scene(self):
        fixture = trap_tests.RoadhogTrapTests()
        run, target, junkrat, shooter, trap = fixture.scene()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        target.team, junkrat.team, shooter.team = 0, 1, 1
        self.assertTrue(match.switch_hero(shooter, target.hero))
        x, y, z = trap.flight.position
        fixture.place(shooter, (x + 4, y, z))
        fixture.place(junkrat, (x - 20, y, z))
        fight = match.combat
        fight.vitals[target.body] = combat.Vitals(target.body, 20000)
        for _ in range(30):
            run.step({})
        shooter.body_script.set_ult_charge(2240)
        return run, shooter, target, trap

    def test_whole_hog_damages_a_rooted_roadhog_and_pushes_after_trap_expiry(self):
        run, shooter, target, trap = self.trap_scene()
        fight = run.match.combat
        trap_tests.RoadhogTrapTests().place(target, trap.flight.position)
        run.step({})
        self.assertEqual(trap.victim, target.body)
        self.assertEqual(self.health(run, target), 19920)
        anchor = tuple(target.mover.position)
        deadline = trap.release_tick
        while fight.tick < deadline - 66:
            self.step(run, shooter, target)
        self.step(run, shooter, target, fire=32)
        while fight.tick < deadline - 1:
            self.step(run, shooter, target)
            self.assertTrue(fight.traps.rooted(target.body, fight.tick))
            self.assertEqual(tuple(target.mover.position), anchor)
            self.assertNotIn(target.body, fight.impulses.live)
        self.assertLess(self.health(run, target), 19920)
        damage = shooter.stats.value(stats.HERO_DAMAGE)
        for _ in range(18):
            self.step(run, shooter, target)
            if shooter.stats.value(stats.HERO_DAMAGE) > damage:
                self.assertIn(target.body, fight.impulses.live)
                break
        self.assertFalse(fight.traps.rooted(target.body, fight.tick))
        self.assertIn(target.body, fight.impulses.live)
        self.step(run, shooter, target)
        self.assertLess(target.mover.position[0], anchor[0])
        ultimate, = shooter.body_script.ultimates
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)

    def test_trap_release_around_whole_hog_end_does_not_replay_force(self):
        duration = self.whole_hog_duration_in_trap_scene()
        for release_offset in (-90, -18, 8):
            with self.subTest(release_offset=release_offset):
                self.check_trap_release_at_whole_hog_end(duration, release_offset)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_npc_trap_release_around_whole_hog_end_does_not_replay_force(self):
        import test_game_roadhog_npcs as actual

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        duration = self.whole_hog_duration_in_trap_scene()
        for definition in fixture.definitions.values():
            for release_offset in (-90, -18, 8):
                with self.subTest(npc=definition.name, release_offset=release_offset):
                    self.check_trap_release_at_whole_hog_end(duration, release_offset, definition=definition)

    def whole_hog_duration_in_trap_scene(self):
        run, shooter, _, _ = self.trap_scene()
        shooter.body_script.ult_seconds = 0
        ultimate, = shooter.body_script.ultimates
        run.step({shooter: (0, -16384, 32)})
        first = run.frame
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        for _ in range(500):
            run.step({shooter: (0, -16384, 0)})
            if not ultimate.flags & nodes.Ability.RUNNING:
                break
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        return run.frame - first

    def check_trap_release_at_whole_hog_end(self, duration, release_offset, *, definition=None):
        from ow174.game import npc

        run, shooter, target, trap = self.trap_scene()
        fight = run.match.combat
        if definition is not None:
            target.team = shooter.team
        shooter.body_script.ult_seconds = 0
        ultimate, = shooter.body_script.ultimates
        run.step({shooter: (0, -16384, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        end = run.frame + duration
        release = end + release_offset
        while run.frame < release - 189:
            run.step({shooter: (0, -16384, 0)})
        self.assertIsNone(trap.victim)
        if definition is None:
            trap_tests.RoadhogTrapTests().place(target, trap.flight.position)
        else:
            target = npc.spawn(run.match, definition, trap.flight.position, team=0)
            fight.vitals[target.entity] = combat.Vitals(target.entity, 20000)
        body = combat._source_entity(target)

        def position():
            return tuple(target.mover.position) if definition is None else target.position

        def step():
            x, y, z = position()
            look = aim(fight._eye(shooter), (x, y + 1, z))
            run.step({shooter: (*look, 0)})

        step()
        self.assertEqual(trap.victim, body)
        self.assertEqual(trap.release_tick, release)
        anchor = position()
        damage_at_capture = shooter.stats.value(stats.HERO_DAMAGE)
        damage_before_release = None
        damage_at_end = None
        first_force = None

        while run.frame < max(end, release) + 20:
            before = shooter.stats.value(stats.HERO_DAMAGE)
            step()
            after = shooter.stats.value(stats.HERO_DAMAGE)
            if run.frame == release:
                damage_before_release = before
                self.assertEqual(bool(ultimate.flags & nodes.Ability.RUNNING), release < end)
            if run.frame < release:
                self.assertTrue(fight.traps.rooted(body, fight.tick))
                self.assertEqual(position(), anchor)
                self.assertNotIn(body, fight.impulses.live)
            elif first_force is None and body in fight.impulses.live:
                first_force = run.frame
                self.assertGreater(after, before)
                self.assertLessEqual(first_force, end)
            if run.frame == end:
                self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                damage_at_end = after
            elif run.frame > end:
                self.assertEqual(after, damage_at_end)
        self.assertGreater(damage_at_end, damage_at_capture)
        self.assertFalse(fight.traps.rooted(body, fight.tick))
        if release_offset == -90:
            self.assertIsNotNone(first_force)
            self.assertGreaterEqual(first_force, release)
            self.assertLess(position()[0], anchor[0])
        else:
            self.assertEqual(damage_at_end, damage_before_release)
            self.assertIsNone(first_force)
            self.assertEqual(position(), anchor)
        for _ in range(100):
            step()
            self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), damage_at_end)
        self.assertNotIn(body, fight.impulses.live)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_steel_trap_clears_existing_whole_hog_impulse_on_capture(self):
        run, shooter, target, trap = self.trap_scene()
        fight = run.match.combat
        place = trap_tests.RoadhogTrapTests().place
        x, y, z = trap.flight.position
        place(target, (x + 3, y, z + 4))
        self.first(run, shooter, target)
        self.assertIsNone(trap.victim)
        self.assertNotEqual(fight.impulses.velocity(target.body), (0, 0, 0))
        place(target, trap.flight.position)
        self.step(run, shooter, target)
        self.assertEqual(trap.victim, target.body)
        self.assertNotIn(target.body, fight.impulses.live)
        anchor = tuple(target.mover.position)
        for _ in range(25):
            self.step(run, shooter, target)
            x, y, z = target.mover.position
            self.assertEqual((x, z), (anchor[0], anchor[2]))
            self.assertLessEqual(y, anchor[1])  # Capture clears the impulse; ordinary gravity can settle.
            self.assertNotIn(target.body, fight.impulses.live)

    def test_steel_trap_denies_soundwave_displacement_until_release(self):
        run, lucio, target, trap = self.trap_scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        place = trap_tests.RoadhogTrapTests().place
        x, y, z = trap.flight.position
        place(lucio, (x + 2.5, y, z))
        for _ in range(35):
            run.step({})
        place(target, trap.flight.position)
        run.step({})
        self.assertEqual(trap.victim, target.body)
        anchor = tuple(target.mover.position)
        look = aim(fight._eye(lucio), (x, y + 1.2, z))
        run.step({lucio: (*look, 2)})
        self.assertEqual(self.health(run, target), 19895)
        self.assertNotIn(target.body, fight.impulses.live)
        for _ in range(300):
            run.step({lucio: (*look, 0)})
            self.assertEqual(tuple(target.mover.position), anchor)
        self.assertFalse(fight.traps.rooted(target.body, fight.tick))
        run.step({lucio: (*look, 2)})
        self.assertEqual(self.health(run, target), 19870)
        self.assertIn(target.body, fight.impulses.live)
        run.step({})
        self.assertLess(target.mover.position[0], anchor[0])

    def test_whole_hog_damages_a_graviton_target_and_pushes_after_expiry(self):
        run, target, zarya, shooter = phases.RoadhogDefenseTests().session("Zarya", distance=5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        target.team, zarya.team, shooter.team = 0, 1, 1
        self.assertTrue(match.switch_hero(shooter, target.hero))
        x, y, z = target.mover.position
        shooter.mover.position[:] = (x + 7, y, z)
        shooter.mover.body = None
        fight.vitals[target.body] = combat.Vitals(target.body, 20000)
        for _ in range(30):
            run.step({})
        field = graviton_tests.RoadhogGravitonTests().launch(run, zarya, (x + 2, y, z))
        while fight.tick < field.end_tick - 66:
            run.step({})
        self.assertIn(target.body, fight.gravitons.pulled)
        anchor = tuple(target.mover.position)
        shooter.body_script.set_ult_charge(2240)
        self.step(run, shooter, target, fire=32)
        while fight.tick < field.end_tick - 1:
            self.step(run, shooter, target)
            self.assertIn(target.body, fight.gravitons.pulled)
            self.assertEqual(tuple(target.mover.position), anchor)
            self.assertNotIn(target.body, fight.impulses.live)
        self.assertGreater(shooter.stats.value(stats.HERO_DAMAGE), 0)
        damage = shooter.stats.value(stats.HERO_DAMAGE)
        for _ in range(18):
            self.step(run, shooter, target)
            if shooter.stats.value(stats.HERO_DAMAGE) > damage:
                self.assertIn(target.body, fight.impulses.live)
                break
        self.assertNotIn(target.body, fight.gravitons.pulled)
        self.assertIn(target.body, fight.impulses.live)
        self.step(run, shooter, target)
        self.assertLess(target.mover.position[0], anchor[0])

    def graviton_tail_scene(self, definition=None):
        from ow174.game import npc

        run, receiver, zarya, shooter = phases.RoadhogDefenseTests().session("Zarya", distance=5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        receiver.team, zarya.team, shooter.team = 0, 1, 1
        self.assertTrue(match.switch_hero(shooter, receiver.hero))
        x, y, z = receiver.mover.position
        zarya.mover.position[:] = (x + 2, y, z + 5)
        shooter.mover.position[:] = (x + 7, y, z)
        zarya.mover.body = shooter.mover.body = None
        if definition is not None:
            receiver.team = shooter.team
            receiver = npc.spawn(match, definition, (x, y, z), team=0)
        body = combat._source_entity(receiver)
        fight.vitals[body] = combat.Vitals(body, 20000)
        shooter.body_script.ult_seconds = 0
        for _ in range(30):
            run.step({})
        return run, receiver, zarya, shooter, (x + 2, y, z)

    def test_graviton_release_at_whole_hog_tail_requires_a_fresh_hit(self):
        self.check_graviton_release_windows((None,))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_npc_graviton_release_at_whole_hog_tail_requires_a_fresh_hit(self):
        import test_game_roadhog_npcs as actual

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        self.check_graviton_release_windows(fixture.definitions.values())

    def check_graviton_release_windows(self, definitions):
        duration = self.whole_hog_duration_in_trap_scene()
        run, _, zarya, _, landing = self.graviton_tail_scene()
        before = run.frame
        field = graviton_tests.RoadhogGravitonTests().launch(run, zarya, landing)
        until_release = field.end_tick - before
        for definition in definitions:
            name = "player" if definition is None else definition.name
            for offset in (-90, -18, 8):
                with self.subTest(target=name, offset=offset):
                    self.check_graviton_release_at_whole_hog_tail(duration, until_release, offset, definition)

    def check_graviton_release_at_whole_hog_tail(self, duration, until_release, offset, definition):
        run, receiver, zarya, shooter, landing = self.graviton_tail_scene(definition)
        fight = run.match.combat
        body = combat._source_entity(receiver)
        ultimate, = shooter.body_script.ultimates
        shooter.body_script.set_ult_charge(shooter.body_script.component.entity_var(338).value())
        run.step({shooter: (0, -16384, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        end = run.frame + duration
        release = end + offset
        away = {shooter: (0, -16384, 0)}
        while run.frame < release - until_release:
            run.step(away)
        field = graviton_tests.RoadhogGravitonTests().launch(run, zarya, landing, held=away)
        self.assertEqual(field.end_tick, release)

        def position():
            return tuple(receiver.mover.position) if definition is None else receiver.position

        def step():
            x, y, z = position()
            look = aim(fight._eye(shooter), (x, y + 1, z))
            run.step({shooter: (*look, 0)})

        damage_before_release = None
        damage_at_end = None
        first_force = None
        while run.frame < max(end, release) + 25:
            before = shooter.stats.value(stats.HERO_DAMAGE)
            step()
            after = shooter.stats.value(stats.HERO_DAMAGE)
            if run.frame == release:
                damage_before_release = before
                self.assertGreater(before, 0)
                self.assertNotIn(body, fight.gravitons.pulled)
                self.assertEqual(bool(ultimate.flags & nodes.Ability.RUNNING), release < end)
            if run.frame < release:
                self.assertNotIn(body, fight.impulses.live)
            elif first_force is None and body in fight.impulses.live:
                first_force = run.frame
                self.assertGreater(after, before)
                self.assertLessEqual(first_force, end)
            if run.frame == release - 1:
                self.assertIn(body, fight.gravitons.pulled)
            if run.frame == end:
                self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                damage_at_end = after
            elif run.frame > end:
                self.assertEqual(after, damage_at_end)
        if offset == -90:
            self.assertIsNotNone(first_force)
            self.assertGreater(damage_at_end, damage_before_release)
        else:
            self.assertIsNone(first_force)
            self.assertEqual(damage_at_end, damage_before_release)
        for _ in range(100):
            step()
            self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), damage_at_end)
        self.assertNotIn(body, fight.impulses.live)
        self.assertFalse(fight.gravitons.fields)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_whole_hog_force_and_earthshatter_input_lock_coexist(self):
        for force_first in (False, True):
            with self.subTest(force_first=force_first):
                self.check_force_earthshatter_overlap(force_first)

    def check_force_earthshatter_overlap(self, force_first):
        run, target, rein, shooter = phases.RoadhogDefenseTests().session("Reinhardt", distance=8)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        target.team, rein.team, shooter.team = 0, 1, 1
        self.assertTrue(match.switch_hero(shooter, target.hero))
        y = target.mover.position[1]
        shooter.mover.position[:] = (0, y, -4)
        shooter.mover.body = None
        fight.vitals[target.body] = combat.Vitals(target.body, 20000)
        for _ in range(30):
            run.step({})
        for player in (rein, shooter):
            script = player.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        away = (16384, 0)
        if force_first:
            run.step({shooter: (*away, 32)})
            for _ in range(35):
                run.step({shooter: (*away, 0)})
        run.step({rein: (-32768, 0, 32), shooter: (*away, 0)})
        wave, = rein.body_script.ultimates
        wave = wave.instance.state(wave.instance.graph.node(1).state)
        if force_first:
            for _ in range(60):
                if wave.active:
                    break
                run.step({shooter: (*away, 0), rein: (-32768, 0, 0)})
            self.assertTrue(wave.active)
        had_force = False
        for _ in range(70):
            angles = away
            if force_first and not had_force:
                x, y, z = target.mover.position
                angles = aim(fight._eye(shooter), (x, y + 1, z))
            run.step({shooter: (*angles, 0), rein: (-32768, 0, 0)})
            if fight.is_stunned(target):
                break
            had_force |= target.body in fight.impulses.live
        self.assertTrue(fight.is_stunned(target))
        self.assertEqual(had_force, force_first)
        self.assertEqual(rein.stats.value(stats.HERO_DAMAGE), 50)
        deadline = fight.stuns[target.body]
        if not force_first:
            x, y, z = target.mover.position
            angles = aim(fight._eye(shooter), (x, y + 1, z))
            run.step({shooter: (*angles, 32)})
            for _ in range(65):
                x, y, z = target.mover.position
                angles = aim(fight._eye(shooter), (x, y + 1, z))
                run.step({shooter: (*angles, 0)})
                if target.body in fight.impulses.live:
                    break
        self.assertIn(target.body, fight.impulses.live)
        self.assertGreater(shooter.stats.value(stats.HERO_DAMAGE), 0)
        before = target.mover.position[2]
        weapon = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        for _ in range(4):
            run.step({target: (0, 0, 1 | 8 | 16), shooter: (*away, 0)})
            self.assertEqual(fight.stuns[target.body], deadline)
            self.assertFalse(target.body_script.component.held)
        self.assertGreater(target.mover.position[2], before)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertNotIn(target.body, fight.hooks.flights)

    def test_earthshatter_cancels_buffered_melee_during_enemy_whole_hog(self):
        run, hog, rein, shooter = phases.RoadhogDefenseTests().session("Reinhardt", distance=8)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, rein.team, shooter.team = 0, 1, 1
        self.assertTrue(match.switch_hero(shooter, hog.hero))
        victim = match.add_player(4, "Melee target", SOLDIER, 3, False)
        in_world(match, victim)
        run.players.append(victim)
        self.assertTrue(match.switch_hero(victim, heroes()[SOLDIER]))
        victim.team = 1
        x, y, z = hog.mover.position
        for actor, position in ((rein, (x + 8, y, z)), (shooter, (x, y, z - 4)),
                                (victim, (x - 1.5, y, z))):
            actor.mover.position[:] = position
            actor.mover.body = None
        fight.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        for _ in range(30):
            run.step({})
        for player in (rein, shooter):
            script = player.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        run.step({shooter: (0, 0, 32)})
        for _ in range(7):
            run.step({})

        held = {hog: (-16384, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = self.health(run, victim)
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        hit = melee.instance.state(4)
        self.assertEqual(self.health(run, victim), before - 30)
        self.assertEqual(hit.counter, 1)
        self.assertTrue(melee.presses)
        self.assertNotIn(hog.body, fight.impulses.live)

        force_before_stun = False
        for offset in range(3, 62):
            x, y, z = hog.mover.position
            centre = (x, y + 1, z)
            run.step({**held, shooter: (*aim(fight._eye(shooter), centre), 0),
                      rein: (*aim(fight._eye(rein), centre), 32 if offset == 3 else 0)})
            if fight.is_stunned(hog):
                break
            if hog.body in fight.impulses.live:
                force_before_stun = True
                self.assertTrue(melee.presses)
        self.assertTrue(force_before_stun)
        self.assertTrue(fight.is_stunned(hog))
        self.assertFalse(melee.presses)
        self.assertEqual(rein.stats.value(stats.HERO_DAMAGE), 50)
        self.assertIn(hog.body, fight.impulses.live)
        self.assertTrue(shooter.body_script.ultimates[0].flags & nodes.Ability.RUNNING)
        before_z = hog.mover.position[2]
        while run.frame < first + 63:
            x, y, z = hog.mover.position
            run.step({**held, shooter: (*aim(fight._eye(shooter), (x, y + 1, z)), 0)})
            self.assertTrue(fight.is_stunned(hog))
        self.assertGreater(hog.mover.position[2], before_z)
        self.assertEqual(hit.counter, 1)
        for _ in range(440):
            if not (fight.is_stunned(hog) or hog.body in fight.impulses.live
                    or shooter.body_script.ultimates[0].flags & nodes.Ability.RUNNING):
                break
            run.step({shooter: (-32768, 0, 0)})
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertFalse(shooter.body_script.ultimates[0].flags & nodes.Ability.RUNNING)

        x, y, z = hog.mover.position
        victim.mover.position[:] = (x - 1.5, y, z)
        victim.mover.body = None
        for _ in range(8):
            run.step({})
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, victim), before - 30)
        run.step(held)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(self.health(run, victim), before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual five NPC graphs required')
    def test_steel_trap_denies_whole_hog_displacement_for_all_five_npcs(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        for definition in fixture.definitions.values():
            with self.subTest(npc=definition.name):
                run, shooter, _, trap = self.trap_scene()
                fight = run.match.combat
                bot = npc.spawn(run.match, definition, trap.flight.position, team=0)
                pool = combat.Vitals(bot.entity, 2000)
                fight.vitals[bot.entity] = pool
                run.step({})
                self.assertEqual(trap.victim, bot.entity)
                self.assertEqual(pool.total, 1920)
                x, y, z = anchor = tuple(bot.position)
                look = aim(fight._eye(shooter), (x, y + 1, z))
                run.step({shooter: (*look, 32)})
                for _ in range(38):
                    run.step({shooter: (*look, 0)})
                    self.assertNotIn(bot.entity, fight.impulses.live)
                    self.assertEqual(tuple(bot.position), anchor)
                self.assertTrue(fight.traps.rooted(bot.entity, fight.tick))
                self.assertLess(pool.total, 1920)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual Heavy Assault graph required')
    def test_whole_hog_hits_a_heavy_charge_victim_without_external_motion(self):
        import test_game_roadhog_npcs as actual
        from test_game_combat import in_world

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        run, hog, heavy, _, server = fixture().heavy_charge_scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = 0
        shooter = match.add_player(8, "Whole Hog", hog.hero.guid, heavy.team, False)
        in_world(match, shooter)
        self.assertTrue(match.switch_hero(shooter, hog.hero))
        run.players.append(shooter)
        shooter.mover.position[:] = (4, 1, 8)
        shooter.mover.body = None
        for _ in range(35):
            run.step({})
        shooter.body_script.set_ult_charge(2240)
        self.step(run, shooter, hog, fire=32)
        for _ in range(70):
            self.step(run, shooter, hog)
            if reinhardt_charge.pinned(fight, hog.body):
                self.assertNotIn(hog.body, fight.impulses.live)
                if shooter.stats.value(stats.HERO_DAMAGE):
                    break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertGreater(shooter.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(server.npc_key(hog.account_lo, "{", {})["count"], 1)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        for _ in range(20):
            self.step(run, shooter, hog)
            if hog.body in fight.impulses.live:
                break
        self.assertIn(hog.body, fight.impulses.live)

    def test_push_does_not_cancel_real_receiver_breather_or_whole_hog(self):
        for button in (16, 32):
            with self.subTest(receiver_button=button):
                run, hog, target = self.session(name='Roadhog')
                if button == 16:
                    victim = next(t for t in run.match.combat._targets() if t.owner is target)
                    run.match.combat.vitals_of(victim).damage(400)
                else:
                    target.body_script.set_ult_charge(2240)
                self.step(run, hog, target, cast=button)
                self.first(run, hog, target)
                if button == 16:
                    self.assertTrue(any(isinstance(s, nodes.ModifyHealth) and s.active
                                        for i in target.body_script.component.instances.values()
                                        for s in i.states.values()))
                else:
                    self.assertTrue(any(a.flags & nodes.Ability.RUNNING
                                        for a in target.body_script.ultimates))
                self.assertNotIn(target.body, run.match.combat.stuns)

    def test_enemy_whole_hog_push_preserves_buffered_quick_melee(self):
        run, shooter, hog = self.session(name='Roadhog')
        victim = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, shooter.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        run.step({})

        held = {hog: (16384, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = self.health(run, hog)
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        while run.frame < first + 12:
            run.step(held)
        self.assertEqual(self.health(run, victim), 19970)
        run.step({**held, shooter: (0, 0, 32)})
        while run.frame < first + 57:
            run.step(held)
        moved_x, moved_y, moved_z = hog.mover.position
        self.assertGreater(moved_z, z + .5)
        self.assertLess(self.health(run, hog), before)
        self.assertIn(hog.body, fight.impulses.live)
        victim.mover.position[:] = (moved_x + 1.5, moved_y, moved_z + .5)
        victim.mover.body = None
        while run.frame < first + 61:
            run.step(held)
        self.assertEqual(self.health(run, victim), 19970)

        run.step(held)
        self.assertEqual(run.frame, first + 62)
        self.assertTrue(shooter.body_script.ultimates[0].flags & nodes.Ability.RUNNING)
        self.assertIn(hog.body, fight.impulses.live)
        self.assertGreater(hog.mover.position[2], moved_z)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.health(run, victim), 19940)
        for _ in range(65):
            run.step(held)
        self.assertEqual(self.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_active_impulse_storage_is_read_only_and_velocity_dedupe_are_causal(self):
        import test_server_snapshot as stored

        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        hog, target = players
        session.apply('switch_hero', {'account': hog.account_lo, 'hero': 0x02E0000000000040})
        y = hog.mover.position[1]
        for p, z in ((hog, 0), (target, 4)):
            p.mover.position[:] = (0, y, z)
            p.mover.body = None
        fight = hog.match.combat
        hog.team, target.team = 0, 1
        hog.match.collision_world = flat_world(y)
        for offset in range(120):
            frame = hog.body_script.frame+1
            hog.match.tick = frame
            fight.update(frame*.016, frame, frames=False)
            if offset == 35:
                hog.body_script.set_ult_charge(2240)
            hog.body_script.command(frame, 32 if offset == 35 else 0)
            fight.command(hog, Command(frame))
            if fight.impulses.live:
                break
        self.assertIn(target.body, fight.impulses.live,
                      ([(p.has_body, p.spawned, p.team) for p in players],
                       len(fight.history), [a.flags for a in hog.body_script.ultimates]))
        motion = fight.impulses.live[target.body]
        with (patch.object(runtime.Var, 'value', side_effect=AssertionError('no evaluation')),
              patch.object(knockback.Impulses, 'update', side_effect=AssertionError('no movement'))):
            before = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), before)
            old = motion.velocity
            motion.velocity = (old[0], old[1]+.1, old[2])
            self.assertNotEqual(snapshot_digest(case.capture(session)), before)
            motion.velocity = old
            fight.impulses.seen.clear()
            self.assertNotEqual(snapshot_digest(case.capture(session)), before)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual five NPC graphs required')
    def test_actual_five_npc_bodies_are_pushed_with_swept_movers_and_cleanup(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        case = actual.RoadhogFiveNpcTests()
        try:
            for definition in case.definitions.values():
                with self.subTest(npc=definition.name):
                    run, hog, bot, pool, look = case.session(definition, distance=4)
                    for _ in range(30):
                        run.step({hog: (*look, 0)})
                    hog.body_script.set_ult_charge(2240)
                    before = tuple(bot.position)
                    run.step({hog: (*look, 32)})
                    for _ in range(50):
                        run.step({hog: (*look, 0)})
                        if bot.entity in run.match.combat.impulses.live:
                            break
                    self.assertIn(bot.entity, run.match.combat.impulses.live)
                    self.stop(hog)
                    run.step({})
                    self.assertGreater(bot.position[2], before[2])
                    self.assertGreater(bot.position[1], before[1])
                    self.assertGreater(pool.total, 0)
                    npc.remove_spawned(run.match)
                    self.assertNotIn(bot.entity, run.match.combat.impulses.live)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_actual_hook_takes_over_whole_hog_motion_on_all_five_npcs(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        case = actual.RoadhogFiveNpcTests()
        for definition in case.definitions.values():
            with self.subTest(npc=definition.name):
                run, shooter, bot, pool, look = case.session(definition, distance=4, caster=True)
                match = run.match
                fight = match.combat
                hooker = run.players[1]
                match.switch_hero(hooker, heroes()[0x02E0000000000040])
                x, y, z = shooter.mover.position
                hooker.mover.position[:] = (x - 3, y, z)
                hooker.mover.body = None
                match.collision_world = flat_world(y)
                shooter.body_script.set_ult_charge(2240)
                run.step({shooter: (*look, 32)})
                for _ in range(50):
                    run.step({shooter: (*look, 0)})
                    if bot.entity in fight.impulses.live:
                        break
                self.assertNotEqual(fight.impulses.velocity(bot.entity), (0, 0, 0))
                before = pool.total
                for frame in range(55):
                    bx, by, bz = bot.position
                    angles = aim(fight._eye(hooker), (bx, by + 0.8, bz))
                    run.step({hooker: (*angles, 8 if frame == 0 else 0)})
                    if fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertNotIn(bot.entity, fight.impulses.live)
                self.assertEqual(bot.velocity, fight.hooks.flights[hooker.body].npc_mover.velocity)
                self.assertLessEqual(pool.total, before - 30)
