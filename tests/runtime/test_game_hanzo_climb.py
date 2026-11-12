"""Actual wall-climb commands under explicitly reconstructed contact/budget rules."""

import math
import os
import unittest
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_movement as movement_tests
import test_game_hanzo_sigma_barrier as barrier_tests
import test_game_hanzo_sonic as sonic_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import bodytrace, combat, hanzo_control, hanzo_movement, movelog, mover, sonic
from ow174.game.collision import World
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import runtime
from ow174.game.server_snapshot import snapshot_digest


class HanzoClimbTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    step = staticmethod(movement_tests.HanzoLungeTests.step)

    def arena(self, distance=0.7, ceiling=False):
        run, hero, victim, instance = movement_tests.HanzoLungeTests().arena()
        victim.spawned = False
        x, y, z = hero.mover.position
        geometry = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
        if distance is not None:
            geometry += wall(x - 10, z + distance, x + 10, z + distance, y0=y, y1=y + 20)
        if ceiling:
            geometry += box(x - 5, x + 5, y + 3, y + 3.2, z - 5, z + 5)
        run.match.collision_world = World(geometry, name="declared Hanzo climb floor/wall/ceiling fixture")
        return run, hero, instance

    def start(self, run, hero):
        self.step(run, hero, buttons=JUMP)
        self.step(run, hero, buttons=JUMP)
        self.assertTrue(hero.body_script.world.hanzo_climb.active)

    def test_held_jump_climbs_with_authored_speed_and_bounded_airborne_budget(self):
        run, hero, instance = self.arena()
        self.step(run, hero, buttons=JUMP)
        start = hero.mover.position[1]
        for _ in range(94):
            self.step(run, hero, buttons=JUMP)
        progress = hero.body_script.world.hanzo_climb
        self.assertEqual(progress.elapsed, 1.5)
        self.assertTrue(progress.active)
        self.assertAlmostEqual(hero.mover.position[1] - start, 4.5 * 1.5, delta=0.03)
        self.assertTrue(instance.component.entity_var(1204).value())
        self.assertTrue(instance.state(instance.graph.node(15).state).active)
        end = hero.mover.position[1]
        for _ in range(10):
            self.step(run, hero, buttons=JUMP)
        self.assertFalse(progress.active)
        self.assertEqual(progress.elapsed, 1.5)
        self.assertLess(hero.mover.position[1], end)
        self.assertFalse(instance.component.entity_var(1204).value())
        self.assertTrue(instance.vars[1347].value())

    def test_miss_out_of_range_and_sideways_facing_do_not_climb(self):
        for distance, yaw in ((None, 0), (1.1, 0), (0.7, 16384)):
            with self.subTest(distance=distance, yaw=yaw):
                run, hero, instance = self.arena(distance=distance)
                for _ in range(24):
                    self.step(run, hero, buttons=JUMP, yaw=yaw)
                self.assertEqual(hero.body_script.world.hanzo_climb.elapsed, 0)
                self.assertFalse(instance.component.entity_var(1204).value())

    def test_climb_reach_and_facing_angle_have_separate_admission_boundaries(self):
        for distance, yaw, active in (
            (0.79, 0, True), (0.81, 0, False),
            (0.53, 8010, True), (0.53, 8192, True), (0.53, 8374, False),
            (0.53, -8192, True), (0.53, -8374, False),
        ):
            with self.subTest(distance=distance, yaw=yaw):
                run, hero, instance = self.arena(distance=distance)
                x, y, z = hero.mover.position
                for _ in range(12):
                    self.step(run, hero, buttons=JUMP, yaw=yaw)
                progress = hero.body_script.world.hanzo_climb
                self.assertEqual(progress.active, active)
                self.assertEqual(progress.elapsed > 0, active)
                self.assertEqual(instance.component.entity_var(1204).value(), active)
                self.assertAlmostEqual(hero.mover.position[0], x)
                self.assertAlmostEqual(hero.mover.position[2], z)
                self.assertGreater(hero.mover.position[1], y)

    def test_tilted_surface_normal_gates_climb_even_when_probe_hits(self):
        for degrees, active in ((30, True), (60, False)):
            with self.subTest(degrees=degrees):
                run, hero, instance = self.arena(distance=None)
                x, y, z = hero.mover.position
                slope = math.tan(math.radians(degrees))
                lower = z + 0.55 - 0.04 * slope
                upper = z + 0.55 + 2 * slope
                a, b = (x - 5, y + 0.96, lower), (x + 5, y + 0.96, lower)
                c, d = (x + 5, y + 3, upper), (x - 5, y + 3, upper)
                run.match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + [(*a, *b, *c), (*a, *c, *d), (*a, *c, *b), (*a, *d, *c)],
                )
                self.step(run, hero, buttons=JUMP)
                px, py, pz = hero.mover.position
                hit = hero.mover.world.cast_ray(
                    (px, py + 1, pz), (0, 0, 1), 0.8, hero.mover.category,
                )
                self.assertIsNotNone(hit)
                self.assertAlmostEqual(abs(hit.normal[1]), math.sin(math.radians(degrees)), places=5)
                self.step(run, hero, buttons=JUMP)
                progress = hero.body_script.world.hanzo_climb
                self.assertEqual(progress.active, active)
                self.assertEqual(progress.elapsed > 0, active)
                self.assertEqual(instance.component.entity_var(1204).value(), active)

    def test_authored_input_mods_end_after_release_pose_and_restore_next_frame(self):
        for right in (-127, 127):
            for forward in (-127, 0, 127):
                with self.subTest(right=right, forward=forward):
                    run, hero, instance = self.arena()
                    self.start(run, hero)
                    effective = -mover.f32(0.4) if forward < 0 else float(forward / 127)
                    compute = mover.modified_throttles
                    observed = []

                    def trace(mods, lateral, ahead, compute=compute, observed=observed):
                        result = compute(mods, lateral, ahead)
                        if lateral or ahead:  # the other fixture player's empty command is unrelated
                            observed.append(result)
                        return result

                    with patch.object(mover, 'modified_throttles', side_effect=trace):
                        before = tuple(hero.mover.position)
                        self.step(run, hero, buttons=JUMP, right=right, forward=forward)
                        self.assertEqual(observed[-1], (0, effective))
                        self.assertEqual(hero.mover.throttles, (right, forward))
                        self.assertEqual(tuple(hero.mover.position[0::2]), before[0::2])
                        release = run.frame + 1
                        with patch.object(hero.mover, '_accelerate',
                                          wraps=hero.mover._accelerate) as accelerate:
                            self.step(run, hero, right=right, forward=forward)
                            self.assertEqual(accelerate.call_args.args[1:3], (0, effective))
                            self.assertEqual(hero.mover.position[0], before[0])
                            self.assertFalse(instance.node_state(instance.graph.node(15)).active)
                            mods = [m for m in hero.mover.mods.values() if m.index in (18, 19)]
                            self.assertEqual({m.end for m in mods}, {release + 1})
                            self.step(run, hero, right=right, forward=forward)
                            self.assertEqual(accelerate.call_args.args[1:3], (right / 127, forward / 127))
                            self.assertNotEqual(hero.mover.position[0], before[0])
                            self.assertFalse(any(m.index in (18, 19) for m in hero.mover.mods.values()))
                        self.assertEqual(hero.mover.throttles, (right, forward))
                        self.assertEqual(hero.mover.input_throttles, (right, forward))

    def test_release_lost_contact_and_authored_disable_gate_end_climb(self):
        for condition in ("release", "turn", "disable"):
            with self.subTest(condition=condition):
                run, hero, instance = self.arena()
                self.start(run, hero)
                progress = hero.body_script.world.hanzo_climb
                used = progress.elapsed
                if condition == "disable":
                    # Explicit supplied authored gate, not an enemy-control implementation.
                    instance.component.set(1956, True)
                    instance.component.run_now()
                self.step(run, hero, buttons=0 if condition == "release" else JUMP,
                          yaw=16384 if condition == "turn" else 0)
                self.assertFalse(progress.active)
                self.assertEqual(progress.elapsed, used)
                self.assertLess(hero.mover.velocity[1], 0)
                self.assertFalse(instance.component.entity_var(1204).value())

    def test_landing_resets_spent_budget_and_fresh_jump_climbs_again(self):
        run, hero, instance = self.arena()
        self.start(run, hero)
        self.step(run, hero)
        for _ in range(100):
            self.step(run, hero)
            if not hero.mover.airborne:
                break
        self.step(run, hero)
        self.assertFalse(hero.mover.airborne)
        self.assertEqual(hero.body_script.world.hanzo_climb.elapsed, 0)
        self.assertFalse(instance.vars[1347].value())
        self.start(run, hero)
        self.assertGreater(hero.mover.velocity[1], 4.4)

    def test_climbing_suppresses_full_draw_release_and_fresh_ground_draw_hits(self):
        self.verify_draw_release_during_climb()

    def test_climb_preserves_prepared_sonic_without_spending_cooldown(self):
        self.verify_draw_release_during_climb(sonic_prepared=True)

    def verify_draw_release_during_climb(self, *, sonic_prepared=False):
        run, hero, _ = self.arena()
        victim = run.players[1]
        fight = run.match.combat
        x, y, z = hero.mover.position
        victim.mover.position[:] = (x, y, z - 2)
        victim.mover.body = None
        victim.spawned = True
        if sonic_prepared:
            fight.configure_sonic(sonic_tests.profile())
            self.step(run, hero, buttons=8)
            self.step(run, hero)
            instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0xC1)
            selected = instance.component.entity_var(627)
            self.assertEqual(selected.value(), 1)
        for _ in range(70):
            self.step(run, hero, buttons=1)
        for _ in range(13):
            self.step(run, hero, buttons=JUMP | 1)
        progress = hero.body_script.world.hanzo_climb
        self.assertTrue(progress.active)
        self.assertFalse(fight.rockets)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(10):
                self.step(run, hero, buttons=JUMP)
                self.assertTrue(progress.active)
                self.assertFalse(fight.rockets)
            for _ in range(100):
                self.step(run, hero)
                if not hero.mover.airborne:
                    break
            self.assertFalse(hero.mover.airborne)
            self.assertFalse(progress.active)
            self.assertFalse(fired.called)
            self.assertFalse(hero.client.messages(combat.HIT))
        if sonic_prepared:
            self.assertEqual(selected.value(), 1)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertFalse(fight.sonars)

        yaw, pitch = aim(fight._eye(hero), (x, y + 0.85, z - 2))
        for _ in range(70):
            self.step(run, hero, buttons=1, yaw=yaw, pitch=pitch)
        self.step(run, hero, yaw=yaw, pitch=pitch)
        arrow, = fight.rockets
        self.assertEqual(arrow.direct.amount, 125)
        if sonic_prepared:
            self.assertEqual(arrow.shot.definition, sonic.DEFINITIONS[0])
            self.assertEqual(selected.value(), 0)
            self.assertGreater(instance.vars[61].value(), 11.9)
        for _ in range(6):
            self.step(run, hero)
        self.assertEqual(fight.vitals[victim.body].total, 75)
        self.assertEqual(len(hero.client.messages(combat.HIT)), 1)
        if sonic_prepared:
            beacon, = fight.sonars
            self.assertEqual(beacon.attached, victim.body)
            self.assertTrue(fight.revealed_to(hero, victim))

    def test_climb_preserves_all_five_storm_arrows_until_fresh_fire_after_landing(self):
        run, hero, _ = self.arena()
        victim = run.players[1]
        fight = run.match.combat
        x, y, z = hero.mover.position
        victim.mover.position[:] = (x, y, z - 2)
        victim.mover.body = None
        victim.spawned = True
        storm = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x146A)
        self.step(run, hero, buttons=16)
        self.step(run, hero)
        self.start(run, hero)
        progress = hero.body_script.world.hanzo_climb

        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(25):
                self.step(run, hero, buttons=JUMP | 1)
                self.assertTrue(progress.active)
            self.step(run, hero, buttons=JUMP)
            for _ in range(100):
                self.step(run, hero)
                if not hero.mover.airborne:
                    break
            self.assertFalse(hero.mover.airborne)
            self.assertFalse(progress.active)
            self.assertFalse(fired.called)
            self.assertTrue(storm.states[0].flags & 1)
            self.assertEqual(storm.find_var(0, 61).value(), 0)

            yaw, pitch = aim(fight._eye(hero), (x, y + 0.85, z - 2))
            for _ in range(100):
                self.step(run, hero, buttons=1, yaw=yaw, pitch=pitch)
            self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)

        self.assertGreater(storm.find_var(0, 61).value(), 9)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [70, 70, 60])
        self.assertEqual(fight.vitals[victim.body].killer, hero.body)

    def test_climb_rejects_ready_dragonstrike_then_fresh_ground_cast_hits(self):
        run, hero, _ = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        victim = run.players[1]
        x, y, z = hero.mover.position
        victim.mover.position[:] = (x, y, z + 0.35)
        victim.mover.body = None
        victim.spawned = True
        body = hero.body_script
        body.set_ult_charge(1680)
        charge = body.component.entity_var(combat.ULT_CHARGE)
        self.start(run, hero)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.step(run, hero, buttons=JUMP | 32)
            self.assertTrue(body.world.hanzo_climb.active)
            for _ in range(20):
                self.step(run, hero, buttons=JUMP)
                self.assertFalse(hanzo_control.casting(body))
                self.assertEqual(charge.value(), 1680)
            for _ in range(100):
                self.step(run, hero)
                if not hero.mover.airborne:
                    break
            self.assertFalse(hero.mover.airborne)
            self.assertFalse(body.world.hanzo_climb.active)
            self.assertFalse(hanzo_control.casting(body))
            self.assertEqual(charge.value(), 1680)
            self.assertFalse(shoot.called)
            self.assertFalse(fight.dragons)

            self.step(run, hero, buttons=32)
            self.assertTrue(hanzo_control.casting(body))
            self.assertEqual(charge.value(), 0)
            for _ in range(100):
                self.step(run, hero)
                if fight.dragons:
                    break
            self.assertEqual(shoot.call_count, 1)
        self.assertEqual(fight.vitals[victim.body].total, 75)
        flight, = fight.dragons
        self.assertEqual(flight.source_entity, hero.body)

    def test_climb_rejects_quick_melee_without_buffering_it_until_landing(self):
        run, hero, _ = self.arena()
        victim = run.players[1]
        fight = run.match.combat
        x, y, z = hero.mover.position
        victim.mover.position[:] = (x, y, z + 0.35)
        victim.mover.body = None
        victim.spawned = True
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        point = (x, y + 1, z + 0.35)
        self.start(run, hero)
        progress = hero.body_script.world.hanzo_climb

        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            yaw, pitch = aim(fight._eye(hero), point)
            self.step(run, hero, buttons=JUMP | 0x800, yaw=yaw, pitch=pitch)
            for _ in range(8):
                self.step(run, hero, buttons=JUMP)
                self.assertTrue(progress.active)
            for _ in range(100):
                self.step(run, hero)
                if not hero.mover.airborne:
                    break
            self.assertFalse(hero.mover.airborne)
            self.assertFalse(progress.active)
            self.assertFalse(damage.called)
            self.assertEqual(pool.total, 200)
            self.assertFalse(hero.client.messages(combat.HIT))

            yaw, pitch = aim(fight._eye(hero), point)
            self.step(run, hero, buttons=0x800, yaw=yaw, pitch=pitch)
            damage.assert_called_once()
        self.assertEqual(pool.total, 170)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [30])

    def test_climb_evades_enemy_rifle_at_jump_height_but_remains_damageable(self):
        jump_point = None
        for climb, tracked, health, dealt in (
            (False, False, 200, 20), (True, False, 200, 0),
            (True, True, 200, 20), (True, True, 20, 20),
        ):
            with self.subTest(climb=climb, tracked=tracked, health=health):
                run, hero, _ = self.arena(distance=0.7 if climb else None)
                soldier = run.players[1]
                fight = run.match.combat
                x, y, z = hero.mover.position
                soldier.mover.position[:] = (x + 8, y, z)
                soldier.mover.body = None
                soldier.spawned = True
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                pool.damage(200 - health)
                old_body, old_script = hero.body, hero.body_script
                for _ in range(38):
                    self.step(run, hero, buttons=JUMP)
                self.assertEqual(hero.body_script.world.hanzo_climb.active, climb)
                if climb:
                    self.assertGreater(hero.mover.position[1], y + 2.5)
                if not climb:
                    jump_point = (x, hero.mover.position[1] + 0.8, z)
                point = jump_point
                if tracked:
                    px, py, pz = hero.mover.position
                    point = (px, py + 1, pz)
                angles = aim(fight._eye(soldier), point)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as shot:
                    for _ in range(10):
                        run.step({hero: (0, 0, JUMP), soldier: (*angles, 1)})
                        if shot.called:
                            break
                shot.assert_called_once()
                self.assertIs(shot.call_args.args[0], soldier)
                self.assertEqual(old_script.world.hanzo_climb.active, climb and not pool.dead)
                self.assertEqual(pool.total, health - dealt)
                hits = soldier.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [dealt] if dealt else [])
                if pool.dead:
                    self.assertEqual(pool.killer, soldier.body)
                    self.assertFalse(old_script.component.entity_var(1204).value())
                    dead_pose = tuple(hero.mover.position)
                    self.step(run, hero, buttons=JUMP)
                    self.assertEqual(tuple(hero.mover.position), dead_pose)

                    run.frame += math.ceil(combat.respawn_seconds(run.match) / combat.TICK) + 2
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertNotEqual(hero.body, old_body)
                    self.assertTrue(old_script.retired)
                    hero.mover.position[:] = (x, y, z)
                    hero.mover.body = None
                    self.step(run, hero)
                    self.assertFalse(hero.body_script.component.entity_var(1204).value())
                    self.start(run, hero)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "prepared Sigma C015 required")
    def test_deployed_experimental_barrier_stops_arrows_but_cannot_start_climb(self):
        run, hero, _, shield = barrier_tests.HanzoSigmaBarrierTests().arena()
        fight = run.match.combat
        self.assertTrue(shield.active())
        for _ in range(140):
            x, y, z = hero.mover.position
            contact = fight.sigma_barriers.trace(hero, (x, y + 1, z), (0, 0, 1), 0.78)
            if contact is not None:
                break
            self.step(run, hero, forward=127)
        self.assertIsNotNone(contact)
        distance, actor = contact
        self.assertIs(actor, shield)
        self.assertGreater(distance, 0.5)
        for _ in range(70):
            self.step(run, hero, buttons=FIRE)
        for _ in range(8):
            self.step(run, hero)
        self.assertEqual(shield.vitals.total, 575)
        self.assertFalse(fight.rockets)
        x, y, z = hero.mover.position
        contact = fight.sigma_barriers.trace(hero, (x, y + 1, z), (0, 0, 1), 0.8)
        self.assertIsNotNone(contact)
        distance, actor = contact
        self.assertIs(actor, shield)
        self.assertGreater(distance, 0.5)
        wall_z = z + distance
        progress = hero.body_script.world.hanzo_climb
        for _ in range(10):
            self.step(run, hero, buttons=JUMP)
            self.assertFalse(progress.active)
        self.assertTrue(hero.mover.airborne)
        self.assertEqual(progress.elapsed, 0)
        self.assertEqual(shield.vitals.total, 575)

        for _ in range(90):
            self.step(run, hero)
            if not hero.mover.airborne:
                break
        self.assertFalse(hero.mover.airborne)
        x, y, z = hero.mover.position
        self.assertGreater(wall_z - z, 0.5)
        self.assertLess(wall_z - z, 0.8)
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 10, wall_z, x + 10, wall_z, y0=y, y1=y + 6)
        )
        self.start(run, hero)
        self.assertGreater(progress.elapsed, 0)
        self.assertEqual(shield.vitals.total, 575)

    def low_ledge(self, *, height=1.9, depth=8, obstruction=False):
        run, hero, instance = self.arena(distance=None)
        x, y, z = hero.mover.position
        geometry = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
        geometry += box(x - 5, x + 5, y, y + height, z + 0.7, z + depth)
        if obstruction:
            # Clear of the center ray, inside the capsule's upward sweep.
            geometry += box(x + 0.24, x + 0.7, y + 2.65, y + 3, z - 0.3, z + 0.4)
        run.match.collision_world = World(geometry, name="Hanzo low ledge")
        return run, hero, instance, (x, y, z)

    def test_wall_climb_hands_off_to_a_supported_higher_ledge(self):
        for height in (3, 5, 9):
            with self.subTest(height=height):
                run, hero, _, (_, y, z) = self.low_ledge(height=height)
                progress = hero.body_script.world.hanzo_climb
                for _ in range(110):
                    was_climbing = progress is not None and progress.active
                    previous_budget = progress.elapsed if progress is not None else 0
                    self.step(run, hero, buttons=JUMP, forward=127)
                    progress = hero.body_script.world.hanzo_climb
                    if progress.mantle is not None:
                        break
                self.assertTrue(was_climbing)
                self.assertIsNotNone(progress.mantle)
                self.assertGreater(previous_budget, 0)
                self.assertLess(previous_budget, 1.5)
                self.assertEqual(progress.elapsed, previous_budget)
                self.assertFalse(progress.active)
                for _ in range(50):
                    self.step(run, hero, buttons=JUMP, forward=127)
                    if progress.mantle is None:
                        break
                    if hero.mover.airborne:
                        self.assertEqual(progress.elapsed, previous_budget)
                self.assertIsNone(progress.mantle)
                for _ in range(12):
                    self.step(run, hero)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y + height, delta=0.01)
                self.assertGreater(hero.mover.position[2], z + 1)
                self.assertEqual(progress.elapsed, 0)

    def test_missing_commands_preserve_the_climb_to_mantle_handoff(self):
        results = []
        for skipped in (False, True):
            run, hero, _, (_, y, z) = self.low_ledge(height=3)
            for _ in range(4):
                self.step(run, hero, buttons=JUMP, forward=127)
            progress = hero.body_script.world.hanzo_climb
            self.assertTrue(progress.active)
            self.assertIsNone(progress.mantle)
            if skipped:
                run.frame += 16
                run.match.tick = run.frame
                hero.take_commands([Command(run.frame, buttons=JUMP, forward=127)])
            else:
                for _ in range(16):
                    self.step(run, hero, buttons=JUMP, forward=127)
            self.assertFalse(progress.active)
            self.assertIsNotNone(progress.mantle)
            midroute = tuple(hero.mover.position), progress.elapsed, progress.mantle
            hero.take_commands([Command(run.frame, buttons=JUMP, forward=127)] * 3)
            self.assertEqual((tuple(hero.mover.position), progress.elapsed, progress.mantle), midroute)
            for _ in range(25):
                self.step(run, hero, buttons=JUMP, forward=127)
            for _ in range(12):
                self.step(run, hero)
            self.assertIsNone(progress.mantle)
            self.assertFalse(hero.mover.airborne)
            self.assertAlmostEqual(hero.mover.position[1], y + 3, delta=0.01)
            self.assertGreater(hero.mover.position[2], z + 1)
            results.append((midroute, tuple(hero.mover.position), hero.mover.flags_word))
        self.assertEqual(*results)

    def test_ledge_above_the_climb_budget_does_not_start_a_mantle(self):
        run, hero, _, (_, y, z) = self.low_ledge(height=9.5)
        for _ in range(110):
            self.step(run, hero, buttons=JUMP, forward=127)
            progress = hero.body_script.world.hanzo_climb
            self.assertIsNone(progress.mantle)
            if not progress.active and progress.elapsed == 1.5:
                break
        self.assertEqual(progress.elapsed, 1.5)
        self.assertFalse(progress.active)
        for _ in range(85):
            self.step(run, hero)
            self.assertIsNone(progress.mantle)
            self.assertLess(hero.mover.position[2], z + 0.7)
            if not hero.mover.airborne:
                break
        self.assertFalse(hero.mover.airborne)
        self.assertAlmostEqual(hero.mover.position[1], y, delta=0.01)

    def test_low_ledge_mantle_lands_and_held_jump_does_not_retrigger(self):
        run, hero, _, (_, y, z) = self.low_ledge()
        self.step(run, hero, buttons=JUMP, forward=127)
        self.step(run, hero, buttons=JUMP, forward=127)
        progress = hero.body_script.world.hanzo_climb
        speed, route = progress.mantle
        self.assertEqual(speed, 7.5)
        self.assertEqual(len(route), 2)
        self.assertEqual(progress.elapsed, 0)
        for _ in range(55):
            self.step(run, hero, buttons=JUMP, forward=127)
        self.assertIsNone(progress.mantle)
        self.assertFalse(hero.mover.airborne)
        self.assertAlmostEqual(hero.mover.position[1], y + 1.9, delta=0.01)
        self.assertGreater(hero.mover.position[2], z + 1)
        self.step(run, hero)
        self.step(run, hero, buttons=JUMP)
        self.assertTrue(hero.mover.airborne)

    def test_mantle_release_thin_support_and_off_axis_obstruction(self):
        for case in ("release", "thin", "obstruction"):
            with self.subTest(case=case):
                run, hero, _, (_, y, z) = self.low_ledge(
                    depth=0.95 if case == "thin" else 8, obstruction=case == "obstruction",
                )
                self.step(run, hero, buttons=JUMP, forward=127)
                self.step(run, hero, buttons=JUMP, forward=127)
                progress = hero.body_script.world.hanzo_climb
                self.assertEqual(progress.mantle is None, case == "thin")
                if case == "release":
                    self.step(run, hero)
                else:
                    for _ in range(40):
                        if progress.mantle is None:
                            break
                        self.step(run, hero, buttons=JUMP, forward=127)
                        self.assertLess(hero.mover.position[2], z + 0.7)
                self.assertIsNone(progress.mantle)
                if case == "obstruction":
                    self.assertTrue(progress.blocked)
                for _ in range(70):
                    self.step(run, hero)
                self.assertLess(hero.mover.position[2], z + 0.7)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y, delta=0.01)

    def test_mantle_flashbang_stun_or_death_retires_the_route(self):
        from ow174.game.weapon_spread import MCCREE

        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, hero, _, (x, y, z) = self.low_ledge()
                caster = run.players[1]
                caster.spawn = ((x, y, z - 1.8), 0)
                caster.spawned = True
                self.assertTrue(run.match.switch_hero(caster, heroes()[MCCREE]))
                fight = run.match.combat
                if lethal:
                    fight.vitals[hero.body] = combat.Vitals(hero.body, 1)
                for _ in range(2):
                    self.step(run, hero, buttons=JUMP, forward=127)
                progress = hero.body_script.world.hanzo_climb
                self.assertIsNotNone(progress.mantle)
                for frame in range(22):
                    hx, hy, hz = hero.mover.position
                    yaw, pitch = aim(fight._eye(caster), (hx, hy + 1, hz))
                    run.step({caster: (yaw, pitch, 16 if frame == 0 else 0), hero: (0, 0, JUMP)})
                    if fight.is_stunned(hero) or fight.is_dead(hero):
                        break
                self.assertEqual(fight.is_dead(hero), lethal)
                self.assertEqual(fight.is_stunned(hero), not lethal)
                self.assertEqual(fight.vitals[hero.body].total, 0 if lethal else 175)
                self.assertIsNone(progress.mantle)
                for _ in range(30):
                    run.step({hero: (0, 0, JUMP)})
                    self.assertIsNone(progress.mantle)
                    self.assertLess(hero.mover.position[2], z + 0.7)

    def test_low_ledge_mantle_from_encrypted_commands_replays_registered_state(self):
        from dataclasses import replace

        import test_server_journal as journal_tests

        from ow174.game.link import SYN
        from ow174.game.server_journal import _ReplaySession

        hero_id = hanzo_movement.HANZO
        base = journal_tests.MAP
        arena = replace(base, spawns=(replace(base.spawns[0], position=(0, 0, 0)),))
        world = World(box(-20, 20, -1, 0, -20, 20) + box(-5, 5, 0, 1.9, 0.7, 8))
        session, sent = journal_tests.ServerJournalTests().make(world, verify_stored_state=True)
        handoff, = session.create_match(arena, [(1, "Hanzo", hero_id, 0, False)])
        peer = journal_tests.Peer(session, handoff, sent, ("127.0.0.1", 47710))
        peer.send(flags=SYN)
        peer.send()
        peer.message(21601, {})
        journal_tests.acknowledge_ownership(session, (peer,))
        now = 100.8
        session.tick(now)
        match, = session.server.matches
        hero, = match.players
        peer.message(21616, journal_tests.pick(hero.entity, hero_id), now=now)
        peer.commands(hero.body_script.frame + 1, 20, 0, now=now)
        peer.commands(hero.body_script.frame + 1, 8, JUMP, forward=127, now=now)
        progress = hero.body_script.world.hanzo_climb
        self.assertEqual(len(progress.mantle[1]), 2)
        self.assertEqual(progress.elapsed, 0)
        peer.commands(hero.body_script.frame + 1, 12, JUMP, forward=127, now=now)
        self.assertEqual(len(progress.mantle[1]), 1)
        peer.commands(hero.body_script.frame + 1, 18, JUMP, forward=127, now=now)
        peer.commands(hero.body_script.frame + 1, 20, 0, now=now)
        self.assertIsNone(progress.mantle)
        self.assertFalse(hero.mover.airborne)
        self.assertAlmostEqual(hero.mover.position[1], 1.9, delta=0.01)
        self.assertGreater(hero.mover.position[2], 1)

        document = session.finish()
        self.assertEqual(document["state_profile"], "registered-domain-v1")
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied, = replay.server.matches[0].players
        self.assertEqual(copied.mover.position, hero.mover.position)
        self.assertEqual(copied.mover.flags_word, hero.mover.flags_word)
        self.assertIsNone(copied.body_script.world.hanzo_climb.mantle)

    def test_mantle_melee_uses_elevated_pose_and_preserves_contact_gates(self):
        from dataclasses import replace

        for case in ("hit", "miss", "ally", "wall"):
            with self.subTest(case=case):
                run, hero, _, (x, y, z) = self.low_ledge()
                victim = run.players[1]
                distance = 4.5 if case == "miss" else 2.75
                victim.mover.position[:] = (x, y + 1.9, z + distance)
                victim.mover.body = None
                victim.spawned = True
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = hero.team
                elif case == "wall":
                    run.match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + box(x - 5, x + 5, y, y + 1.9, z + 0.7, z + 8)
                        + wall(x - 2, z + 2.2, x + 2, z + 2.2, y + 1.9, y + 5),
                    )
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
                for _ in range(21):
                    self.step(run, hero, buttons=JUMP, forward=127)
                progress = hero.body_script.world.hanzo_climb
                self.assertEqual(len(progress.mantle[1]), 1)
                point = (x, y + 2.9, z + distance)
                yaw, pitch = aim(fight._eye(hero), point)
                self.step(run, hero, buttons=JUMP | 0x800, yaw=yaw, pitch=pitch)
                expected = 170 if case == "hit" else 200
                self.assertEqual(pool.total, expected)
                self.assertIsNotNone(progress.mantle)
                for _ in range(80):
                    yaw, pitch = aim(fight._eye(hero), point)
                    self.step(run, hero, buttons=JUMP | 0x800, yaw=yaw, pitch=pitch)
                self.assertEqual(pool.total, expected)
                self.assertIsNone(progress.mantle)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y + 1.9, delta=0.01)
                hits = [hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)]
                self.assertEqual(hits, [30] if case == "hit" else [])
                if case == "hit":
                    self.step(run, hero, yaw=yaw, pitch=pitch)
                    self.step(run, hero, buttons=0x800, yaw=yaw, pitch=pitch)
                    self.assertEqual(pool.total, 140)

    def test_dragonstrike_and_mantle_use_the_actual_start_order(self):
        for mantle_first in (False, True):
            with self.subTest(mantle_first=mantle_first):
                run, hero, _, (x, y, z) = self.low_ledge()
                victim = run.players[1]
                victim.mover.position[:] = (x, y + 1.9, z + 4)
                victim.mover.body = None
                victim.spawned = True
                fight = run.match.combat
                fight.configure_dragonstrike(dragon_tests.prepared_profile())
                body = hero.body_script
                body.set_ult_charge(1680)
                if mantle_first:
                    for _ in range(8):
                        self.step(run, hero, buttons=JUMP, forward=127)
                    self.assertIsNotNone(body.world.hanzo_climb.mantle)
                self.step(run, hero, buttons=32 | (JUMP if mantle_first else 0),
                          forward=127 if mantle_first else 0)
                self.assertTrue(hanzo_control.casting(body))
                self.assertEqual(body.component.entity_var(combat.ULT_CHARGE).value(), 0)
                used_wall_climb = False
                for frame in range(94):
                    jump = body.world.hanzo_climb.mantle is not None if mantle_first else 40 <= frame <= 68
                    yaw, pitch = aim(fight._eye(hero), (x, y + 2.8, z + 4))
                    self.step(run, hero, buttons=JUMP if jump else 0,
                              forward=127 if jump else 0, yaw=yaw, pitch=pitch)
                    used_wall_climb |= body.world.hanzo_climb.active
                    if not mantle_first:
                        self.assertIsNone(body.world.hanzo_climb.mantle)
                    if fight.rockets:
                        break
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)
                self.assertEqual(arrow.source_entity, hero.body)
                self.assertIsNone(body.world.hanzo_climb.mantle)
                self.assertEqual(used_wall_climb, not mantle_first)
                if mantle_first:
                    self.assertFalse(hero.mover.airborne)
                    self.assertAlmostEqual(hero.mover.position[1], y + 1.9, delta=0.01)
                    self.assertGreater(arrow.position[2], z + 1)
                else:
                    self.assertLess(arrow.position[2], z + 0.7)
                for _ in range(60):
                    self.step(run, hero)
                    if fight.dragons:
                        break
                flight, = fight.dragons
                self.assertEqual(flight.source_entity, hero.body)
                self.assertEqual(fight.vitals[victim.body].total, 75)

    def test_wall_top_ends_climb_before_feet_clear_the_ledge(self):
        run, hero, instance = self.arena(distance=None)
        x, y, z = hero.mover.position
        top = y + 3
        ledge = z + 0.7
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + box(x - 5, x + 5, y, top, ledge, z + 0.95),
        )
        self.start(run, hero)
        progress = hero.body_script.world.hanzo_climb
        for _ in range(70):
            self.step(run, hero, buttons=JUMP, forward=127)
            if not progress.active:
                break
        self.assertFalse(progress.active)
        self.assertFalse(instance.component.entity_var(1204).value())
        self.assertTrue(0 < progress.elapsed < 1.5)
        height = hero.mover.position[1]
        self.assertLess(height, top)
        self.assertGreater(height + 1, top)
        for _ in range(120):
            self.step(run, hero, forward=127)
            self.assertLess(hero.mover.position[2], ledge)
            if not hero.mover.airborne:
                break
        self.assertFalse(hero.mover.airborne)
        self.assertAlmostEqual(hero.mover.position[1], y, places=2)

    def test_ceiling_sweep_stops_climb_without_passing_through(self):
        run, hero, instance = self.arena(ceiling=True)
        floor = hero.mover.position[1]
        self.start(run, hero)
        for _ in range(30):
            self.step(run, hero, buttons=JUMP)
        progress = hero.body_script.world.hanzo_climb
        self.assertFalse(progress.active)
        self.assertTrue(progress.blocked)
        self.assertLess(hero.mover.position[1], floor + 1.01)
        self.assertFalse(instance.component.entity_var(1204).value())

    def test_profile_values_drive_speed_and_duration(self):
        run, hero, instance = self.arena()
        row = instance.graph.node(21).field("m_82B8DE3B")[0]
        with patch.dict(row, {
            "m_82BF9656": {"$": "STUConfigVarFloat", "m_value": 2.25},
            "m_21411EEE": {"$": "STUConfigVarFloat", "m_value": 0.25},
        }):
            self.step(run, hero, buttons=JUMP)
            start = hero.mover.position[1]
            for _ in range(16):
                self.step(run, hero, buttons=JUMP)
            self.assertEqual(hero.body_script.world.hanzo_climb.elapsed, 0.25)
            self.assertAlmostEqual(hero.mover.position[1] - start, 2.25 * 0.25, delta=0.01)
            self.step(run, hero, buttons=JUMP)
            self.assertFalse(hero.body_script.world.hanzo_climb.active)

    def test_death_and_body_replacement_retire_active_climb(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, hero, _ = self.arena()
                self.start(run, hero)
                old = hero.body_script.world.hanzo_climb
                if death:
                    target = next(t for t in run.match.combat._targets() if t.owner is hero)
                    run.match.combat._damage(run.players[1], target, 200, False, None, run.frame)
                    self.assertTrue(run.match.combat.is_dead(hero))
                    self.assertFalse(old.active)  # immediate, without another input frame
                    pose = tuple(hero.mover.position)
                    self.step(run, hero, buttons=JUMP)
                    self.assertEqual(tuple(hero.mover.position), pose)
                else:
                    run.match.switch_hero(hero, heroes()[SOLDIER])
                    self.assertFalse(old.active)
                    self.assertIsNone(hero.body_script.world.hanzo_climb)

    def test_high_ledge_handoff_retires_with_death_or_hero_switch(self):
        for segments, death in ((2, False), (1, False), (2, True), (1, True)):
            with self.subTest(segments=segments, death=death):
                run, hero, _, (x, y, z) = self.low_ledge(height=3)
                match = run.match
                fight = match.combat
                soldier = run.players[1]
                soldier.mover.position[:] = (x + 8, y, z - 2)
                soldier.mover.body = None
                soldier.spawned = True
                hero.spawn = ((x, y, z), 0)
                old_body, old_script = hero.body, hero.body_script
                for _ in range(50):
                    self.step(run, hero, buttons=JUMP, forward=127)
                    old_climb = old_script.world.hanzo_climb
                    if (hero.mover.driven and old_climb.mantle is not None
                            and len(old_climb.mantle[1]) == segments):
                        break
                self.assertIsNotNone(old_climb.mantle)
                self.assertEqual(len(old_climb.mantle[1]), segments)
                self.assertGreater(old_climb.elapsed, 0)
                self.assertTrue(hero.mover.driven)

                if death:
                    target = next(t for t in fight._targets() if t.owner is hero)
                    pool = fight.vitals_of(target)
                    pool.damage(180)
                    for _ in range(5):
                        route_at_contact = old_climb.mantle
                        hx, hy, hz = hero.mover.position
                        angles = aim(fight._eye(soldier), (hx, hy + 1, hz))
                        run.step({hero: (0, 0, JUMP), soldier: (*angles, FIRE)})
                        if pool.dead:
                            break
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, soldier.body)
                    self.assertIsNotNone(route_at_contact)
                    self.assertEqual(len(route_at_contact[1]), segments)
                    self.assertIsNone(old_climb.mantle)
                    self.assertFalse(old_script.component.entity_var(1204).value())
                    dead_pose = tuple(hero.mover.position)
                    for _ in range(5):
                        self.step(run, hero, buttons=JUMP, forward=127)
                        self.assertEqual(tuple(hero.mover.position), dead_pose)
                        self.assertIsNone(old_climb.mantle)
                    run.frame += math.ceil(combat.respawn_seconds(match) / combat.TICK) + 2
                    match.update(run.frame * combat.TICK, run.frame)
                else:
                    self.assertTrue(match.switch_hero(hero, heroes()[SOLDIER]))
                    soldier_body = hero.body
                    self.assertNotEqual(soldier_body, old_body)
                    self.assertTrue(old_script.retired)
                    self.assertIsNone(hero.body_script.world.hanzo_climb)
                    self.assertFalse(hero.mover.driven)
                    self.step(run, hero)
                    self.step(run, hero, buttons=JUMP)
                    self.assertGreater(hero.mover.position[1], y)
                    self.assertIsNone(hero.body_script.world.hanzo_climb)
                    self.assertTrue(match.switch_hero(hero, heroes()[hanzo_movement.HANZO]))
                    self.assertNotEqual(hero.body, soldier_body)

                self.assertTrue(old_script.retired)
                self.assertIsNone(old_climb.mantle)
                self.assertFalse(old_climb.active)
                self.assertNotIn(old_body, fight.vitals)
                self.assertNotEqual(hero.body, old_body)
                self.assertIsNone(hero.body_script.world.hanzo_climb)
                self.assertFalse(hero.mover.driven)
                self.assertEqual(hero.mover.velocity, (0.0, 0.0, 0.0))
                retired_frame = old_script.frame
                self.step(run, hero)
                target = next(t for t in fight._targets() if t.owner is hero)
                self.assertEqual(fight.vitals_of(target).total, 200)
                fresh = hero.body_script.world.hanzo_climb
                self.assertIsNot(fresh, old_climb)
                self.assertEqual(fresh.elapsed, 0)
                self.assertIsNone(fresh.mantle)

                handed_off = False
                for _ in range(90):
                    self.step(run, hero, buttons=JUMP, forward=127)
                    handed_off = handed_off or fresh.mantle is not None
                    if handed_off and fresh.mantle is None:
                        break
                self.assertTrue(handed_off)
                self.assertIsNone(fresh.mantle)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y + 3, delta=0.01)
                self.assertGreater(hero.mover.position[2], z + 1)
                self.assertEqual(old_script.frame, retired_frame)

    def test_contiguous_and_missing_command_frames_agree_without_duplicate_budget(self):
        for gap in (24, 40):
            with self.subTest(gap=gap):
                results = []
                for skipped in (False, True):
                    run, hero, _ = self.arena()
                    self.step(run, hero, buttons=JUMP)
                    if skipped:
                        run.frame += gap
                        run.match.tick = run.frame
                        hero.take_commands([Command(run.frame, buttons=JUMP)])
                    else:
                        # Existing input recovery repeats held keys for32 frames,
                        # then releases them until the next real command arrives.
                        for offset in range(1, gap + 1):
                            self.step(run, hero, buttons=JUMP if offset <= 32 or offset == gap else 0)
                    progress = hero.body_script.world.hanzo_climb
                    result = tuple(hero.mover.position), progress.elapsed
                    hero.take_commands([Command(run.frame, buttons=JUMP)] * 3)
                    self.assertEqual((tuple(hero.mover.position), progress.elapsed), result)
                    results.append(result)
                self.assertEqual(results[0], results[1])

    def test_strict_mode_refuses_contact_host_and_budget_changes_stored_digest(self):
        run, hero, instance = self.arena()
        self.step(run, hero, buttons=JUMP)
        instance.component.strict = True
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "reconstructed Hanzo wall climb"):
            hanzo_movement.climb_request(hero, Command(run.frame + 1, buttons=JUMP), 0.016)
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        world = players[0].body_script.world
        before = snapshot_digest(case.capture(session))
        world.hanzo_climb = hanzo_movement.Climb(active=True, elapsed=0.5)
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(before, active)
        world.hanzo_climb.elapsed = 0.6
        updated = snapshot_digest(case.capture(session))
        self.assertNotEqual(active, updated)
        world.hanzo_climb.mantle = (7.5, ((0.0, 1.9, 0.0), (0.0, 1.9, 1.25)))
        self.assertNotEqual(updated, snapshot_digest(case.capture(session)))
